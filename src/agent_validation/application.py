"""Validation request orchestration."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import __version__
from .config import ResolvedProfile, default_profile_dir, load_profile
from .contracts import EpisodeRecord, ValidationRequest, ValidationResult, ValidationScope
from .episodes import EpisodeExecutor
from .errors import ProvisioningError, ServiceCapacityError
from .metrics import CriterionEvaluator, MetricCalculator
from .provisioning import RevisionPreparer
from .security import collect_secret_values, redact_text, validate_commit, validate_repo
from .storage import RunStore


@dataclass(frozen=True)
class ServiceSettings:
    profile_dir: Path
    artifact_root: Path
    temporary_root: Path
    active_profile: str = "default"
    max_active_validations: int = 4
    max_total_episodes: int = 32

    def __post_init__(self) -> None:
        if self.max_active_validations < 1 or self.max_total_episodes < 1:
            raise ValueError("Validation capacity settings must be at least 1")

    @classmethod
    def from_environment(cls) -> ServiceSettings:
        return cls(
            profile_dir=default_profile_dir(),
            artifact_root=Path(
                os.environ.get("AGENT_VALIDATION_ARTIFACT_DIR", "artifacts/runs")
            ).resolve(),
            temporary_root=Path(os.environ.get("AGENT_VALIDATION_TEMP_DIR", "/tmp")).resolve(),
            active_profile=os.environ.get("AGENT_VALIDATION_PROFILE", "default"),
            max_active_validations=int(os.environ.get("AGENT_VALIDATION_MAX_ACTIVE", "4")),
            max_total_episodes=int(os.environ.get("AGENT_VALIDATION_MAX_EPISODES", "32")),
        )


class ValidationApplication:
    def __init__(self, settings: ServiceSettings | None = None) -> None:
        resolved_settings = settings or ServiceSettings.from_environment()
        self.settings = resolved_settings
        self._validation_slots = threading.BoundedSemaphore(
            resolved_settings.max_active_validations
        )
        self._episode_slots = threading.BoundedSemaphore(resolved_settings.max_total_episodes)

    def validate(
        self,
        request: ValidationRequest,
        validation_scope: ValidationScope,
        profile_name: str | None = None,
    ) -> dict[str, Any]:
        return self.validate_result(request, validation_scope, profile_name).to_response()

    def validate_result(
        self,
        request: ValidationRequest,
        validation_scope: ValidationScope,
        profile_name: str | None = None,
    ) -> ValidationResult:
        with self._validation_slot():
            return self._validate_result_bounded(request, validation_scope, profile_name)

    def _validate_result_bounded(
        self,
        request: ValidationRequest,
        validation_scope: ValidationScope,
        profile_name: str | None = None,
    ) -> ValidationResult:
        validate_repo(request.repo)
        validate_commit(request.commit)
        selected_profile = profile_name or self.settings.active_profile
        resolved = load_profile(selected_profile, self.settings.profile_dir)
        planned = resolved.plan_episodes(validation_scope)
        secret_values = collect_secret_values(request, resolved.profile)
        revision = RevisionPreparer(
            request.repo,
            request.commit,
            request.solution_overrides,
            resolved,
            self.settings.temporary_root,
            secret_values,
        )
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ") + "-" + uuid4().hex[:12]
        try:
            store = RunStore(self.settings.artifact_root, run_id, secret_values)
        except Exception:
            revision.close()
            raise
        run_configuration = self._run_configuration(
            run_id, request, resolved, validation_scope, revision
        )
        try:
            store.write_run_configuration(run_configuration)
            store.write_planned_episodes(planned)
            with revision:
                run_configuration["verified_commit"] = revision.verified_commit
                run_configuration["solution_dependencies"] = revision.dependency_manifest
                store.write_run_configuration(run_configuration)
                records = self._execute_episodes(resolved, revision, store, planned)
            store.write_provisioning_events(revision.events)
            if revision.cleanup_error:
                raise ProvisioningError(revision.cleanup_error)
            calculator = MetricCalculator(resolved, validation_scope, planned, records)
            computations = calculator.compute()
            result = CriterionEvaluator(resolved, validation_scope, computations).evaluate()
            store.finalize(
                result,
                {
                    key: computation.model_dump(mode="json")
                    for key, computation in computations.items()
                },
                calculator.intermediates,
                run_configuration,
            )
            return result
        except Exception as exc:
            revision.close()
            run_configuration["verified_commit"] = revision.verified_commit
            run_configuration["solution_dependencies"] = revision.dependency_manifest
            run_configuration["cleanup_error"] = revision.cleanup_error
            try:
                store.write_provisioning_events(revision.events)
                store.mark_failed(run_configuration, str(exc))
            except Exception:
                pass
            raise

    @contextmanager
    def _validation_slot(self):
        if not self._validation_slots.acquire(blocking=False):
            raise ServiceCapacityError("The active-validation capacity has been reached")
        try:
            yield
        finally:
            self._validation_slots.release()

    def _execute_episodes(
        self,
        resolved: ResolvedProfile,
        revision: RevisionPreparer,
        store: RunStore,
        planned: list,
    ) -> list[EpisodeRecord]:
        executor = EpisodeExecutor(resolved, revision, store)

        def execute_bounded(episode) -> EpisodeRecord:
            with self._episode_slots:
                return executor.execute(episode)

        concurrency = resolved.profile.resources.episode_concurrency
        if concurrency == 1:
            return [execute_bounded(episode) for episode in planned]
        records: list[EpisodeRecord] = []
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(execute_bounded, episode) for episode in planned]
            for future in futures:
                records.append(future.result())
        return records

    def _run_configuration(
        self,
        run_id: str,
        request: ValidationRequest,
        resolved: ResolvedProfile,
        validation_scope: ValidationScope,
        revision: RevisionPreparer,
    ) -> dict[str, Any]:
        dependency_versions = {}
        for package in ("agent-validation", "pydantic", "PyYAML", "fastapi"):
            try:
                dependency_versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                dependency_versions[package] = "not-installed"
        return {
            "run_id": run_id,
            "status": "running",
            "requested_repo": request.repo,
            "requested_commit": request.commit,
            "verified_commit": None,
            "solution_overrides": request.solution_overrides,
            "profile": resolved.profile.model_dump(mode="json"),
            "profile_path": str(resolved.path),
            "profile_sha256": resolved.sha256,
            "validation_scope": validation_scope.value,
            "random_seed": resolved.profile.random_seed,
            "temporary_root": str(revision.temporary_root),
            "implementation": {
                "version": __version__,
                "python": sys.version,
                "platform": platform.platform(),
                "dependencies": dependency_versions,
            },
            "environment_variables_forwarded": sorted(
                set(resolved.profile.environment.passthrough)
            ),
        }


def error_details(exc: Exception) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": redact_text(str(exc))[:4096],
    }
