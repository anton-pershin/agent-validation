"""Validation metric computation and acceptance-criterion evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from .config import ResolvedProfile, ScopeConfig
from .contracts import (
    CriterionEvaluation,
    EpisodeRecord,
    MetricComputation,
    MetricEvaluation,
    PlannedEpisode,
    ValidationResult,
    ValidationScope,
)


class MetricError(Exception):
    pass


@dataclass(frozen=True)
class RateSummary:
    overall: float
    task_rates: dict[str, float]


class MetricCalculator:
    def __init__(
        self,
        resolved_profile: ResolvedProfile,
        validation_scope: ValidationScope,
        planned: list[PlannedEpisode],
        records: list[EpisodeRecord],
    ) -> None:
        self.resolved_profile = resolved_profile
        self.profile = resolved_profile.profile
        self.validation_scope = validation_scope
        self.scope = self.profile.scope(validation_scope)
        self.planned = planned
        self.records = records
        self.intermediates: dict[str, object] = {
            "task_success_rates": {},
            "overall_success_rates": {},
            "repeat_success_rates": {},
        }
        self._record_by_id: dict[str, EpisodeRecord] = {}
        self._population_error: str | None = None
        planned_ids = [episode.episode_id for episode in planned]
        if len(set(planned_ids)) != len(planned_ids):
            self._population_error = "Planned episode identifiers are not unique"
        for record in records:
            if record.episode_id in self._record_by_id:
                self._population_error = "Observed episode identifiers are not unique"
            self._record_by_id[record.episode_id] = record
        if set(self._record_by_id) - set(planned_ids):
            self._population_error = "Observed records contain unplanned episodes"

    def compute(self) -> dict[str, MetricComputation]:
        computations: dict[str, MetricComputation] = {}
        self._compute_into(computations, "VM1", self._vm1)
        self._compute_into(computations, "VM2", self._vm2)
        self._compute_into(computations, "VM3", self._vm3)
        self._compute_into(computations, "VM4", self._vm4)
        for variant in self.profile.selected_variants(self.scope):
            variant_id = variant.id
            self._compute_into(
                computations,
                f"VM5/{variant_id}",
                lambda variant_id=variant_id: self._vm5(variant_id),
            )
        self._compute_into(computations, "VM6", self._vm6)
        for scenario in self.profile.selected_scenarios(self.scope):
            scenario_id = scenario.id
            self._compute_into(
                computations,
                f"VM7/{scenario_id}",
                lambda scenario_id=scenario_id: self._vm7(scenario_id),
            )
        self._compute_into(computations, "VM8", self._vm8)
        self._compute_into(computations, "VM9", self._vm9)
        self._compute_into(computations, "VM10", self._vm10)
        self.intermediates["metric_errors"] = {
            key: computation.error_message
            for key, computation in computations.items()
            if computation.error_message is not None
        }
        return computations

    def _compute_into(
        self,
        computations: dict[str, MetricComputation],
        key: str,
        function: Callable[[], int | float],
    ) -> None:
        try:
            value = function()
            computations[key] = MetricComputation(
                metric_id=key,
                computed_value=value,
                expected_value_or_threshold=self._threshold(key),
            )
        except MetricError as exc:
            computations[key] = MetricComputation(
                metric_id=key,
                computed_value=None,
                expected_value_or_threshold=self._threshold(key),
                error_message=str(exc),
            )

    def _vm1(self) -> float:
        return self._overall_rate("unperturbed", "unperturbed").overall

    def _vm2(self) -> float:
        records = self._records_for(self.planned)
        self._validate_error_flags(records)
        flagged = [record for record in records if record.likely_incorrect is True]
        if not flagged:
            raise MetricError("VM2 has a zero error-flag denominator")
        correct_flags = sum(record.success is False for record in flagged)
        return correct_flags / len(flagged)

    def _vm3(self) -> float:
        records = self._records_for(self.planned)
        self._validate_error_flags(records)
        unsuccessful = [record for record in records if record.success is False]
        if not unsuccessful:
            raise MetricError("VM3 has a zero unsuccessful-episode denominator")
        recalled = sum(record.likely_incorrect is True for record in unsuccessful)
        return recalled / len(unsuccessful)

    def _vm4(self) -> float:
        repeats = sorted({episode.repeat for episode in self._select("unperturbed")})
        if len(repeats) < 2:
            raise MetricError("VM4 requires at least two complete repeats")
        rates: dict[str, float] = {}
        for repeat in repeats:
            summary = self._overall_rate(
                "unperturbed", f"unperturbed/repeat/{repeat}", repeat=repeat
            )
            rates[str(repeat)] = summary.overall
        self.intermediates["repeat_success_rates"]["unperturbed"] = rates
        return max(rates.values()) - min(rates.values())

    def _vm5(self, variant_id: str) -> float:
        baseline = self._overall_rate("unperturbed", "unperturbed").overall
        variant = self._overall_rate(f"prompt/{variant_id}", f"prompt/{variant_id}").overall
        return max(0.0, baseline - variant)

    def _vm6(self) -> float:
        variants = self.profile.selected_variants(self.scope)
        if not variants:
            raise MetricError("VM6 requires at least one prompt variant")
        condition_ids = ["unperturbed", *[f"prompt/{item.id}" for item in variants]]
        rates = [self._overall_rate(item, item).overall for item in condition_ids]
        return max(rates) - min(rates)

    def _vm7(self, scenario_id: str) -> float:
        self._require_fault_exposure(f"failure/{scenario_id}")
        baseline = self._overall_rate("unperturbed", "unperturbed").overall
        scenario = self._overall_rate(f"failure/{scenario_id}", f"failure/{scenario_id}").overall
        return max(0.0, baseline - scenario)

    def _vm8(self) -> float:
        scenarios = self.profile.selected_scenarios(self.scope)
        if not scenarios:
            raise MetricError("VM8 requires at least one transient-failure scenario")
        condition_ids = [
            "unperturbed",
            *[f"failure/{item.id}" for item in scenarios],
        ]
        for condition_id in condition_ids[1:]:
            self._require_fault_exposure(condition_id)
        rates = [self._overall_rate(item, item).overall for item in condition_ids]
        return max(rates) - min(rates)

    def _vm9(self) -> int:
        records = self._records_for(self.planned)
        if not records:
            raise MetricError("VM9 has an empty episode population")
        if any(record.action_attempts is None for record in records):
            raise MetricError("VM9 has a missing action-attempt record")
        return max(
            record.action_attempts for record in records if record.action_attempts is not None
        )

    def _vm10(self) -> float:
        records = self._records_for(self.planned)
        if not records:
            raise MetricError("VM10 has an empty episode population")
        if any(
            record.final_response_received is None or record.elapsed_seconds is None
            for record in records
        ):
            raise MetricError("VM10 has a missing response or timing record")
        limit = self.profile.criteria.AC7.time_limit_seconds
        timely = sum(
            record.final_response_received is True
            and record.elapsed_seconds is not None
            and record.elapsed_seconds <= limit
            for record in records
        )
        return timely / len(records)

    def _select(self, condition_id: str, repeat: int | None = None) -> list[PlannedEpisode]:
        return [
            episode
            for episode in self.planned
            if episode.condition_id == condition_id and (repeat is None or episode.repeat == repeat)
        ]

    def _records_for(self, episodes: list[PlannedEpisode]) -> list[EpisodeRecord]:
        if self._population_error:
            raise MetricError(self._population_error)
        records = []
        for episode in episodes:
            record = self._record_by_id.get(episode.episode_id)
            if record is None:
                raise MetricError(f"Missing required episode record: {episode.episode_id}")
            if record.success is None:
                raise MetricError(
                    f"Episode outcome cannot be computed because of infrastructure failure: "
                    f"{episode.episode_id}"
                )
            records.append(record)
        return records

    def _overall_rate(
        self, condition_id: str, evidence_key: str, repeat: int | None = None
    ) -> RateSummary:
        episodes = self._select(condition_id, repeat)
        if not episodes:
            raise MetricError(f"No planned episodes for condition {condition_id}")
        records = self._records_for(episodes)
        planned_by_task: dict[str, list[PlannedEpisode]] = defaultdict(list)
        for episode in episodes:
            planned_by_task[episode.task_id].append(episode)
        records_by_id = {record.episode_id: record for record in records}
        task_rates: dict[str, float] = {}
        for task_id, task_episodes in planned_by_task.items():
            successes = sum(
                records_by_id[episode.episode_id].success is True for episode in task_episodes
            )
            task_rates[task_id] = successes / len(task_episodes)
        summary = RateSummary(
            overall=sum(task_rates.values()) / len(task_rates), task_rates=task_rates
        )
        task_rates_evidence = self.intermediates["task_success_rates"]
        overall_rates_evidence = self.intermediates["overall_success_rates"]
        assert isinstance(task_rates_evidence, dict)
        assert isinstance(overall_rates_evidence, dict)
        task_rates_evidence[evidence_key] = task_rates
        overall_rates_evidence[evidence_key] = summary.overall
        return summary

    def _validate_error_flags(self, records: list[EpisodeRecord]) -> None:
        invalid = [record.episode_id for record in records if record.error_flag_invalid]
        if invalid:
            raise MetricError(
                f"Completed responses have missing or invalid likely_incorrect flags: {invalid}"
            )

    def _require_fault_exposure(self, condition_id: str) -> None:
        episodes = self._select(condition_id)
        records = self._records_for(episodes)
        if not records:
            raise MetricError(f"No planned episodes for condition {condition_id}")
        if any(record.fault_exposure is not True for record in records):
            raise MetricError(
                f"Required fault exposure was not observed for every episode in {condition_id}"
            )

    def _threshold(self, key: str) -> str:
        criteria = self.profile.criteria
        if key == "VM1":
            return f">= {criteria.AC1.min_success_rate}"
        if key == "VM2":
            return f">= {criteria.AC2.min_precision}"
        if key == "VM3":
            return f">= {criteria.AC2.min_recall}"
        if key == "VM4":
            return f"<= {criteria.AC3.max_range}"
        if key.startswith("VM5"):
            return f"<= {criteria.AC4.max_loss}"
        if key == "VM6":
            return f"<= {criteria.AC4.max_range}"
        if key.startswith("VM7"):
            return f"<= {criteria.AC5.max_loss}"
        if key == "VM8":
            return f"<= {criteria.AC5.max_range}"
        if key == "VM9":
            return f"<= {criteria.AC6.max_action_attempts}"
        if key == "VM10":
            return f">= {criteria.AC7.min_timely_response_rate}"
        raise KeyError(key)


class CriterionEvaluator:
    def __init__(
        self,
        resolved_profile: ResolvedProfile,
        validation_scope: ValidationScope,
        computations: dict[str, MetricComputation],
    ) -> None:
        self.resolved_profile = resolved_profile
        self.profile = resolved_profile.profile
        self.scope: ScopeConfig = self.profile.scope(validation_scope)
        self.computations = computations
        self.validation_scope = validation_scope

    def evaluate(self) -> ValidationResult:
        required = self.required_metrics()
        criteria = {}
        for criterion_id in self.profile.acceptance_criteria:
            metric_statuses = {}
            for metric_id in required[criterion_id]:
                computation = self.computations.get(metric_id)
                if computation is None:
                    computation = MetricComputation(
                        metric_id=metric_id,
                        computed_value=None,
                        expected_value_or_threshold="required",
                        error_message="Required metric was not computed",
                    )
                if computation.error_message is not None:
                    metric_statuses[metric_id] = MetricEvaluation(
                        computation_status="failed_to_compute",
                        acceptance_status=None,
                        computed_value=None,
                        expected_value_or_threshold=computation.expected_value_or_threshold,
                        error_message=computation.error_message,
                    )
                    continue
                accepted = self._accepted(metric_id, computation.computed_value)
                metric_statuses[metric_id] = MetricEvaluation(
                    computation_status="computed",
                    acceptance_status="accepted" if accepted else "rejected",
                    computed_value=computation.computed_value,
                    expected_value_or_threshold=computation.expected_value_or_threshold,
                    error_message=None,
                )
            statuses = [metric.acceptance_status for metric in metric_statuses.values()]
            if any(
                metric.computation_status == "failed_to_compute"
                for metric in metric_statuses.values()
            ):
                criterion_status = "invalid"
            elif any(status == "rejected" for status in statuses):
                criterion_status = "valid"
            else:
                criterion_status = "accepted"
            criteria[criterion_id] = CriterionEvaluation(
                status=criterion_status, metrics_status=metric_statuses
            )
        return ValidationResult(
            profile=self.profile.name,
            validation_scope=self.validation_scope,
            criteria=criteria,
        )

    def required_metrics(self) -> dict[str, list[str]]:
        return {
            "AC1": ["VM1"],
            "AC2": ["VM2", "VM3"],
            "AC3": ["VM4"],
            "AC4": [
                *[f"VM5/{item.id}" for item in self.profile.selected_variants(self.scope)],
                "VM6",
            ],
            "AC5": [
                *[f"VM7/{item.id}" for item in self.profile.selected_scenarios(self.scope)],
                "VM8",
            ],
            "AC6": ["VM9"],
            "AC7": ["VM10"],
        }

    def _accepted(self, metric_id: str, value: int | float | None) -> bool:
        if value is None:
            raise ValueError(f"Computed metric {metric_id} has no value")
        criteria = self.profile.criteria
        if metric_id == "VM1":
            return value >= criteria.AC1.min_success_rate
        if metric_id == "VM2":
            return value >= criteria.AC2.min_precision
        if metric_id == "VM3":
            return value >= criteria.AC2.min_recall
        if metric_id == "VM4":
            return value <= criteria.AC3.max_range
        if metric_id.startswith("VM5"):
            return value <= criteria.AC4.max_loss
        if metric_id == "VM6":
            return value <= criteria.AC4.max_range
        if metric_id.startswith("VM7"):
            return value <= criteria.AC5.max_loss
        if metric_id == "VM8":
            return value <= criteria.AC5.max_range
        if metric_id == "VM9":
            return value <= criteria.AC6.max_action_attempts
        if metric_id == "VM10":
            return value >= criteria.AC7.min_timely_response_rate
        raise KeyError(metric_id)
