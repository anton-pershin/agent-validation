"""Validation request orchestration."""

from __future__ import annotations

import asyncio
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
from .config import (
    ResolvedProfile,
    configuration_response,
    default_profile_dir,
    load_profile,
    resolve_partial_profile,
)
from .contracts import (
    EpisodeRecord,
    RunManifest,
    ValidationRequest,
    ValidationResult,
    ValidationScope,
)
from .episodes import EpisodeExecutor
from .errors import (
    InvalidRequestError,
    ProvisioningError,
    RunDirectoryExistsError,
    ServiceCapacityError,
    bind_run_context,
)
from .metrics import CriterionEvaluator, MetricCalculator
from .provenance import collect_validator_provenance, dependency_versions
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
    validator_source_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.max_active_validations < 1 or self.max_total_episodes < 1:
            raise ValueError("Validation capacity settings must be at least 1")

    @classmethod
    def from_environment(cls) -> ServiceSettings:
        source_dir = os.environ.get("AGENT_VALIDATION_SOURCE_DIR")
        return cls(
            profile_dir=default_profile_dir(),
            artifact_root=Path(
                os.environ.get("AGENT_VALIDATION_ARTIFACT_DIR", "artifacts/runs")
            ).resolve(),
            temporary_root=Path(os.environ.get("AGENT_VALIDATION_TEMP_DIR", "/tmp")).resolve(),
            active_profile=os.environ.get("AGENT_VALIDATION_PROFILE", "default"),
            max_active_validations=int(os.environ.get("AGENT_VALIDATION_MAX_ACTIVE", "4")),
            max_total_episodes=int(os.environ.get("AGENT_VALIDATION_MAX_EPISODES", "32")),
            validator_source_dir=Path(source_dir).resolve() if source_dir else None,
        )


class ValidationApplication:
    def __init__(self, settings: ServiceSettings | None = None) -> None:
        resolved_settings = settings or ServiceSettings.from_environment()
        self.settings = resolved_settings
        self._validation_slots = threading.BoundedSemaphore(
            resolved_settings.max_active_validations
        )
        self._episode_slots = threading.BoundedSemaphore(resolved_settings.max_total_episodes)

    def profile_configuration(self, profile_name: str) -> dict[str, Any]:
        return configuration_response(load_profile(profile_name, self.settings.profile_dir))

    def validate(
        self,
        request: ValidationRequest,
        validation_scope: ValidationScope,
        profile_name: str | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.validate_result(
            request, validation_scope, profile_name, configuration
        ).to_response()

    def validate_result(
        self,
        request: ValidationRequest,
        validation_scope: ValidationScope,
        profile_name: str | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> ValidationResult:
        with self._validation_slot():
            return self._validate_result_bounded(
                request, validation_scope, profile_name, configuration
            )

    def _validate_result_bounded(
        self,
        request: ValidationRequest,
        validation_scope: ValidationScope,
        profile_name: str | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> ValidationResult:
        validate_repo(request.repo)
        validate_commit(request.commit)
        if validation_scope == ValidationScope.FULL and configuration is not None:
            raise InvalidRequestError("Full validation does not accept a modified configuration")
        if validation_scope == ValidationScope.PARTIAL and configuration is None:
            raise InvalidRequestError("Partial validation requires a complete configuration")
        selected_profile = profile_name or self.settings.active_profile
        base = load_profile(selected_profile, self.settings.profile_dir)
        resolved = (
            base
            if validation_scope == ValidationScope.FULL
            else resolve_partial_profile(base, configuration or {})
        )
        planned = resolved.plan_episodes()
        secret_values = collect_secret_values(request, resolved.profile)
        revision = RevisionPreparer(
            request.repo,
            request.commit,
            request.solution_overrides,
            resolved,
            self.settings.temporary_root,
            secret_values,
        )
        try:
            store = self._allocate_run(secret_values)
        except Exception:
            revision.close()
            raise
        run_configuration = self._initial_manifest(
            store, request, resolved, validation_scope, revision
        )
        try:
            store.write_run_configuration(run_configuration)
            run_configuration.implementation.update(
                {
                    "dependencies": dependency_versions(),
                    "validator": collect_validator_provenance(
                        self.settings.validator_source_dir
                    ),
                }
            )
            store.write_run_configuration(run_configuration)
            store.write_planned_episodes(planned)
            run_configuration.artifacts["planned_episodes"] = "planned_episodes.json"
            store.write_run_configuration(run_configuration)
            with revision:
                run_configuration.verified_commit = revision.verified_commit
                run_configuration.solution_dependencies = revision.dependency_manifest
                store.write_run_configuration(run_configuration)
                records = self._execute_episodes(resolved, revision, store, planned)
            store.write_provisioning_events(revision.events)
            run_configuration.artifacts["provisioning"] = "provisioning.json"
            if revision.cleanup_error:
                raise ProvisioningError(revision.cleanup_error)
            calculator = MetricCalculator(resolved, validation_scope, planned, records)
            computations = calculator.compute()
            result = CriterionEvaluator(resolved, validation_scope, computations).evaluate(
                run_id=store.run_id, artifacts_location=store.artifacts_location
            )
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
        except (KeyboardInterrupt, asyncio.CancelledError) as exc:
            bind_run_context(exc, store.run_id, store.artifacts_location)
            self._finish_interrupted(run_configuration, revision, store, exc, aborted=True)
            raise
        except Exception as exc:
            bind_run_context(exc, store.run_id, store.artifacts_location)
            self._finish_interrupted(run_configuration, revision, store, exc, aborted=False)
            raise

    def _allocate_run(self, secret_values: set[str]) -> RunStore:
        for _ in range(10):
            run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ") + "-" + uuid4().hex[:12]
            try:
                return RunStore(self.settings.artifact_root, run_id, secret_values)
            except RunDirectoryExistsError:
                continue
        raise ServiceCapacityError("Cannot allocate a unique validation run ID")

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

    def _initial_manifest(
        self,
        store: RunStore,
        request: ValidationRequest,
        resolved: ResolvedProfile,
        validation_scope: ValidationScope,
        revision: RevisionPreparer,
    ) -> RunManifest:
        now = datetime.now(UTC).isoformat()
        return RunManifest(
            run_id=store.run_id,
            artifacts_location=store.artifacts_location,
            created_at=now,
            updated_at=now,
            requested_repo=request.repo,
            requested_commit=request.commit,
            solution_overrides=request.solution_overrides,
            base_profile=resolved.base_profile_name,
            profile_path=str(resolved.path),
            profile_sha256=resolved.sha256,
            resolved_configuration=resolved.profile.model_dump(mode="json"),
            resolved_configuration_sha256=resolved.effective_sha256,
            validation_scope=validation_scope,
            random_seed=resolved.profile.random_seed,
            temporary_root=str(revision.temporary_root),
            implementation={
                "version": __version__,
                "python": sys.version,
                "platform": platform.platform(),
                "dependencies": {},
                "validator": None,
            },
            environment_variables_forwarded=sorted(
                set(resolved.profile.environment.passthrough)
            ),
            artifacts={"manifest": "run.json"},
        )

    def _finish_interrupted(
        self,
        run_configuration: RunManifest,
        revision: RevisionPreparer,
        store: RunStore,
        error: BaseException,
        *,
        aborted: bool,
    ) -> None:
        revision.close()
        run_configuration.verified_commit = revision.verified_commit
        run_configuration.solution_dependencies = revision.dependency_manifest
        run_configuration.cleanup_error = revision.cleanup_error
        try:
            store.write_provisioning_events(revision.events)
            run_configuration.artifacts["provisioning"] = "provisioning.json"
            if aborted:
                store.mark_aborted(run_configuration, str(error) or "Validation was aborted")
            else:
                store.mark_failed(run_configuration, str(error))
        except Exception:
            pass


def error_details(exc: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": redact_text(str(exc))[:4096],
    }
    for key in ("run_id", "artifacts_location"):
        value = getattr(exc, key, None)
        if value:
            details[key] = value
    return details
