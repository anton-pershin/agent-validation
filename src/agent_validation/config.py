"""Validation profile loading and episode population planning."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import ConditionType, PlannedEpisode, ValidationScope
from .errors import InvalidRequestError, ProfileError, ProfileNotFoundError


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskConfig(ProfileModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    problem: str


class PromptVariantConfig(ProfileModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    instructions: str = Field(min_length=1)


class FailureScenarioConfig(ProfileModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    action_name: str
    occurrence: int = Field(default=1, ge=1)
    error_message: str = "Temporary action failure. Retry or choose another action."
    task_ids: list[str] = Field(min_length=1)


class ScopeConfig(ProfileModel):
    task_ids: list[str] = Field(min_length=1)
    repeats: int = Field(ge=1)
    prompt_variant_ids: list[str] = Field(default_factory=list)
    failure_scenario_ids: list[str] = Field(default_factory=list)
    failure_task_ids: dict[str, list[str]] = Field(default_factory=dict)


class AC1Config(ProfileModel):
    min_success_rate: float = Field(default=0.95, ge=0, le=1)


class AC2Config(ProfileModel):
    min_precision: float = Field(default=0.80, ge=0, le=1)
    min_recall: float = Field(default=0.50, ge=0, le=1)


class RangeConfig(ProfileModel):
    max_range: float = Field(default=0.05, ge=0, le=1)


class AC4Config(RangeConfig):
    max_loss: float = Field(default=0.05, ge=0, le=1)


class AC5Config(RangeConfig):
    max_loss: float = Field(default=0.05, ge=0, le=1)


class AC6Config(ProfileModel):
    max_action_attempts: int = Field(default=50, ge=0)


class AC7Config(ProfileModel):
    min_timely_response_rate: float = Field(default=0.95, ge=0, le=1)
    time_limit_seconds: float = Field(default=300, gt=0)


class CriteriaConfig(ProfileModel):
    AC1: AC1Config = Field(default_factory=AC1Config)
    AC2: AC2Config = Field(default_factory=AC2Config)
    AC3: RangeConfig = Field(default_factory=RangeConfig)
    AC4: AC4Config = Field(default_factory=AC4Config)
    AC5: AC5Config = Field(default_factory=AC5Config)
    AC6: AC6Config = Field(default_factory=AC6Config)
    AC7: AC7Config = Field(default_factory=AC7Config)


class ResourceConfig(ProfileModel):
    episode_concurrency: int = Field(default=1, ge=1, le=32)
    max_output_bytes: int = Field(default=1_000_000, ge=1024)
    max_retained_log_bytes: int = Field(default=65536, ge=1024)
    max_provisioning_output_bytes: int = Field(default=1_000_000, ge=1024)
    install_timeout_seconds: float = Field(default=300, gt=0)
    git_timeout_seconds: float = Field(default=120, gt=0)
    process_shutdown_seconds: float = Field(default=2, gt=0)


class EnvironmentConfig(ProfileModel):
    passthrough: list[str] = Field(default_factory=list)


class ValidationProfile(ProfileModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    solution_module: str = "reliable_agent_pddl"
    random_seed: int = 0
    original_instructions: str = Field(min_length=1)
    tasks: list[TaskConfig] = Field(min_length=1)
    prompt_variants: list[PromptVariantConfig] = Field(default_factory=list)
    failure_scenarios: list[FailureScenarioConfig] = Field(default_factory=list)
    full: ScopeConfig
    acceptance_criteria: list[str] = Field(
        default=["AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7"]
    )
    criteria: CriteriaConfig = Field(default_factory=CriteriaConfig)
    resources: ResourceConfig = Field(default_factory=ResourceConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)

    @model_validator(mode="after")
    def validate_references(self) -> ValidationProfile:
        task_ids = _unique_ids([task.id for task in self.tasks], "task")
        variant_ids = _unique_ids(
            [variant.id for variant in self.prompt_variants], "prompt variant"
        )
        scenario_ids = _unique_ids(
            [scenario.id for scenario in self.failure_scenarios], "failure scenario"
        )
        for scenario in self.failure_scenarios:
            _unique_ids(scenario.task_ids, f"task reference in scenario {scenario.id}")
            _require_references(scenario.task_ids, task_ids, f"scenario {scenario.id}")
        for scope_name, scope in (("full", self.full),):
            _unique_ids(scope.task_ids, f"task reference in {scope_name} scope")
            _unique_ids(scope.prompt_variant_ids, f"prompt reference in {scope_name} scope")
            _unique_ids(scope.failure_scenario_ids, f"scenario reference in {scope_name} scope")
            _require_references(scope.task_ids, task_ids, f"{scope_name} scope")
            _require_references(scope.prompt_variant_ids, variant_ids, f"{scope_name} scope")
            _require_references(scope.failure_scenario_ids, scenario_ids, f"{scope_name} scope")
            for scenario_id, scenario_task_ids in scope.failure_task_ids.items():
                if scenario_id not in scope.failure_scenario_ids:
                    raise ValueError(f"Failure task override for unselected scenario {scenario_id}")
                _unique_ids(
                    scenario_task_ids,
                    f"task override for scenario {scenario_id} in {scope_name} scope",
                )
                _require_references(
                    scenario_task_ids,
                    set(scope.task_ids),
                    f"scenario {scenario_id} in {scope_name} scope",
                )
        allowed_criteria = {f"AC{index}" for index in range(1, 8)}
        unknown = set(self.acceptance_criteria) - allowed_criteria
        if unknown:
            raise ValueError(f"Unknown acceptance criteria: {sorted(unknown)}")
        if len(set(self.acceptance_criteria)) != len(self.acceptance_criteria):
            raise ValueError("Acceptance criteria must not contain duplicates")
        return self

    def scope(self) -> ScopeConfig:
        return self.full

    def selected_variants(self, scope: ScopeConfig) -> list[PromptVariantConfig]:
        by_id = {variant.id: variant for variant in self.prompt_variants}
        return [by_id[variant_id] for variant_id in scope.prompt_variant_ids]

    def selected_scenarios(self, scope: ScopeConfig) -> list[FailureScenarioConfig]:
        by_id = {scenario.id: scenario for scenario in self.failure_scenarios}
        return [by_id[scenario_id] for scenario_id in scope.failure_scenario_ids]


class ResolvedProfile(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    profile: ValidationProfile
    path: Path
    sha256: str
    base_name: str | None = None

    @property
    def base_profile_name(self) -> str:
        return self.base_name or self.profile.name

    @property
    def effective_sha256(self) -> str:
        return hashlib.sha256(profile_for_evidence(self).encode("utf-8")).hexdigest()

    def plan_episodes(
        self, validation_scope: ValidationScope | None = None
    ) -> list[PlannedEpisode]:
        del validation_scope
        scope = self.profile.scope()
        tasks = {task.id: task for task in self.profile.tasks}
        variants = self.profile.selected_variants(scope)
        scenarios = self.profile.selected_scenarios(scope)
        planned: list[PlannedEpisode] = []
        for repeat in range(1, scope.repeats + 1):
            for task_id in scope.task_ids:
                task = tasks[task_id]
                problem_path = (self.path.parent / task.problem).resolve()
                try:
                    task_input = problem_path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise ProfileError(
                        f"Cannot read problem for task {task_id}: {problem_path}"
                    ) from exc
                common = {
                    "task_id": task_id,
                    "repeat": repeat,
                    "task_input": task_input,
                    "problem_path": str(problem_path),
                }
                planned.append(
                    PlannedEpisode(
                        episode_id=uuid4().hex,
                        condition_id="unperturbed",
                        condition_type=ConditionType.UNPERTURBED,
                        instructions=self.profile.original_instructions,
                        **common,
                    )
                )
                for variant in variants:
                    planned.append(
                        PlannedEpisode(
                            episode_id=uuid4().hex,
                            condition_id=f"prompt/{variant.id}",
                            condition_type=ConditionType.PROMPT,
                            instructions=variant.instructions,
                            **common,
                        )
                    )
                for scenario in scenarios:
                    eligible_task_ids = scope.failure_task_ids.get(scenario.id, scenario.task_ids)
                    if task_id not in eligible_task_ids:
                        continue
                    planned.append(
                        PlannedEpisode(
                            episode_id=uuid4().hex,
                            condition_id=f"failure/{scenario.id}",
                            condition_type=ConditionType.FAILURE,
                            fault_scenario_id=scenario.id,
                            instructions=self.profile.original_instructions,
                            **common,
                        )
                    )
        return planned


def _unique_ids(ids: list[str], label: str) -> set[str]:
    if len(set(ids)) != len(ids):
        raise ValueError(f"Duplicate {label} identifiers")
    return set(ids)


def _require_references(references: list[str], available: set[str], context: str) -> None:
    unknown = set(references) - available
    if unknown:
        raise ValueError(f"Unknown references in {context}: {sorted(unknown)}")


def load_profile(name: str, profile_dir: Path) -> ResolvedProfile:
    if not name or not all(character.isalnum() or character in "_-" for character in name):
        raise InvalidRequestError("Profile name contains invalid characters")
    path = (profile_dir / f"{name}.yaml").resolve()
    try:
        path.relative_to(profile_dir.resolve())
    except ValueError as exc:
        raise InvalidRequestError("Profile path escapes the profile directory") from exc
    try:
        raw_bytes = path.read_bytes()
        raw = yaml.safe_load(raw_bytes)
        if not isinstance(raw, dict):
            raise ProfileError(f"Profile {name} must contain a YAML mapping")
        profile = ValidationProfile.model_validate(raw)
    except FileNotFoundError as exc:
        raise ProfileNotFoundError(f"Validation profile does not exist: {name}") from exc
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ProfileError(f"Cannot load validation profile {name}: {exc}") from exc
    if profile.name != name:
        raise ProfileError(f"Profile filename {name} does not match internal name {profile.name}")
    return ResolvedProfile(
        profile=profile,
        path=path,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        base_name=name,
    )


def normalized_configuration(profile: ValidationProfile) -> dict[str, Any]:
    """Return every effective profile value, including fields with defaults."""
    return profile.model_dump(mode="json")


def complete_configuration_schema() -> dict[str, Any]:
    """Return a JSON Schema that requires every field in a submitted copy."""
    schema = ValidationProfile.model_json_schema()

    def require_all(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties)
            for child in node.values():
                require_all(child)
        elif isinstance(node, list):
            for child in node:
                require_all(child)

    require_all(schema)
    return schema


def configuration_response(resolved: ResolvedProfile) -> dict[str, Any]:
    return {
        "profile": resolved.base_profile_name,
        "schema": complete_configuration_schema(),
        "configuration": normalized_configuration(resolved.profile),
    }


def resolve_partial_profile(
    base: ResolvedProfile, configuration: dict[str, Any]
) -> ResolvedProfile:
    """Validate a complete modified copy without changing its registered base."""
    if not isinstance(configuration, dict):
        raise InvalidRequestError("Partial configuration must be a mapping")
    errors = sorted(
        Draft202012Validator(complete_configuration_schema()).iter_errors(configuration),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        raise InvalidRequestError(_partial_schema_error(errors[0]))
    try:
        profile = ValidationProfile.model_validate(configuration)
    except ValueError as exc:
        raise InvalidRequestError(
            "Invalid partial configuration: references or structural constraints are invalid"
        ) from exc
    return ResolvedProfile(
        profile=profile,
        path=base.path,
        sha256=base.sha256,
        base_name=base.base_profile_name,
    )


def _partial_schema_error(error) -> str:
    if error.validator == "required":
        missing = next(
            (name for name in error.validator_value if name not in error.instance),
            "unknown",
        )
        detail = f"missing required field {missing!r}"
    elif error.validator == "additionalProperties":
        detail = "unknown fields are not allowed"
    elif error.validator == "type":
        expected = error.validator_value
        detail = f"expected type {expected}"
    else:
        detail = f"value does not satisfy the {error.validator} constraint"
    return f"Invalid partial configuration: {detail}"


def load_partial_configuration(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidRequestError(f"Cannot read partial configuration {path}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InvalidRequestError(f"Cannot parse partial configuration {path}") from exc
    if not isinstance(raw, dict):
        raise InvalidRequestError(f"Partial configuration {path} must contain a mapping")
    return raw


def default_profile_dir() -> Path:
    configured = os.environ.get("AGENT_VALIDATION_PROFILE_DIR")
    if configured:
        return Path(configured).resolve()
    return (Path.cwd() / "config" / "profiles").resolve()


def profile_for_evidence(resolved: ResolvedProfile) -> str:
    """Return a stable JSON representation for hashing and audit evidence."""
    return json.dumps(
        resolved.profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
