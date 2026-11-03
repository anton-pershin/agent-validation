"""Typed public, episode, metric, and result contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationScope(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class ConditionType(StrEnum):
    UNPERTURBED = "unperturbed"
    PROMPT = "prompt"
    FAILURE = "failure"


class ValidationRequest(ContractModel):
    repo: str = Field(min_length=1, max_length=4096)
    commit: str = Field(min_length=40, max_length=64)
    solution_overrides: str = Field(default="", max_length=16384)


class PlannedEpisode(ContractModel):
    episode_id: str
    task_id: str
    condition_id: str
    condition_type: ConditionType
    repeat: int = Field(ge=1)
    instructions: str
    task_input: str
    problem_path: str
    fault_scenario_id: str | None = None


class EpisodeRecord(ContractModel):
    episode_id: str
    task_id: str
    condition_id: str
    condition_type: ConditionType
    repeat: int
    success: bool | None
    likely_incorrect: bool | None = None
    error_flag_invalid: bool = False
    action_attempts: int | None = None
    final_response_received: bool | None = None
    elapsed_seconds: float | None = None
    completed: bool = False
    answer: str | None = None
    confidence: float | None = None
    reason: str | None = None
    termination_reason: str
    failure_kind: Literal["none", "solution", "infrastructure"] = "none"
    error_message: str | None = None
    fault_exposure: bool | None = None


class MetricComputation(ContractModel):
    metric_id: str
    computed_value: int | float | None
    expected_value_or_threshold: str
    error_message: str | None = None

    @property
    def computed(self) -> bool:
        return self.error_message is None


class MetricEvaluation(ContractModel):
    computation_status: Literal["computed", "failed_to_compute"]
    acceptance_status: Literal["accepted", "rejected"] | None
    computed_value: int | float | None
    expected_value_or_threshold: str
    error_message: str | None


class CriterionEvaluation(ContractModel):
    status: Literal["accepted", "valid", "invalid"]
    metrics_status: dict[str, MetricEvaluation]


class ValidationResult(ContractModel):
    profile: str
    validation_scope: ValidationScope
    criteria: dict[str, CriterionEvaluation]

    def to_response(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "validation_scope": self.validation_scope.value,
            **{
                criterion_id: evaluation.model_dump(mode="json")
                for criterion_id, evaluation in self.criteria.items()
            },
        }


class RunContext(ContractModel):
    run_id: str
    requested_commit: str
    verified_commit: str | None = None
    temporary_root: str
    status: Literal["running", "completed", "failed"] = "running"
    error_message: str | None = None
