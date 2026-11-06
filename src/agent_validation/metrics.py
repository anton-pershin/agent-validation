"""Validation metric computation and acceptance-criterion evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from .config import ResolvedProfile, ScopeConfig
from .contracts import (
    ConditionType,
    CriterionEvaluation,
    EpisodeRecord,
    MetricComputation,
    MetricEvaluation,
    PlannedEpisode,
    ValidationResult,
    ValidationScope,
)
from .metric_names import metric_key


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
            "fault_exposure_rates": {},
            "fault_recovery_success_rates": {},
            "vacuous_metrics": [],
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
        self._validate_population()

    def _validate_population(self) -> None:
        expected: set[tuple[object, ...]] = set()
        for repeat in range(1, self.scope.repeats + 1):
            for task_id in self.scope.task_ids:
                expected.add((task_id, "unperturbed", ConditionType.UNPERTURBED, repeat, None))
                for variant in self.profile.selected_variants(self.scope):
                    expected.add(
                        (
                            task_id,
                            f"prompt/{variant.id}",
                            ConditionType.PROMPT,
                            repeat,
                            None,
                        )
                    )
                for scenario in self.profile.selected_scenarios(self.scope):
                    eligible_task_ids = self.scope.failure_task_ids.get(
                        scenario.id, scenario.task_ids
                    )
                    if task_id in eligible_task_ids:
                        expected.add(
                            (
                                task_id,
                                f"failure/{scenario.id}",
                                ConditionType.FAILURE,
                                repeat,
                                scenario.id,
                            )
                        )
        actual_keys = [self._logical_episode_key(episode) for episode in self.planned]
        if len(set(actual_keys)) != len(actual_keys):
            self._population_error = "Planned episodes contain duplicate logical episodes"
        elif set(actual_keys) != expected:
            self._population_error = "Planned episodes do not match the resolved profile population"
        for episode in self.planned:
            expected_fault = (
                episode.condition_id.split("/", 1)[1]
                if episode.condition_type == ConditionType.FAILURE
                else None
            )
            if episode.fault_scenario_id != expected_fault:
                self._population_error = (
                    f"Planned fault metadata is inconsistent: {episode.episode_id}"
                )
        planned_by_id = {episode.episode_id: episode for episode in self.planned}
        for record in self.records:
            planned = planned_by_id.get(record.episode_id)
            if planned is not None and self._logical_episode_key(
                record
            ) != self._logical_episode_key(planned):
                self._population_error = (
                    f"Observed episode metadata does not match its plan: {record.episode_id}"
                )
            if (
                record.action_attempts is not None
                and record.action_attempts < 0
                or record.elapsed_seconds is not None
                and record.elapsed_seconds < 0
            ):
                self._population_error = (
                    f"Observed episode has negative measurements: {record.episode_id}"
                )

    @staticmethod
    def _logical_episode_key(
        episode: PlannedEpisode | EpisodeRecord,
    ) -> tuple[object, ...]:
        return (
            episode.task_id,
            episode.condition_id,
            episode.condition_type,
            episode.repeat,
            episode.condition_id.split("/", 1)[1]
            if episode.condition_type == ConditionType.FAILURE
            else None,
        )

    def compute(self) -> dict[str, MetricComputation]:
        computations: dict[str, MetricComputation] = {}
        self._compute_into(computations, metric_key("VM1"), self._vm1)
        self._compute_into(computations, metric_key("VM2"), self._vm2)
        self._compute_into(computations, metric_key("VM3"), self._vm3)
        self._compute_into(computations, metric_key("VM4"), self._vm4)
        for variant in self.profile.selected_variants(self.scope):
            variant_id = variant.id
            self._compute_into(
                computations,
                metric_key("VM5", variant_id),
                lambda variant_id=variant_id: self._vm5(variant_id),
            )
        self._compute_into(computations, metric_key("VM6"), self._vm6)
        for scenario in self.profile.selected_scenarios(self.scope):
            scenario_id = scenario.id
            self._compute_into(
                computations,
                metric_key("VM7", scenario_id),
                lambda scenario_id=scenario_id: self._vm7(scenario_id),
            )
        self._compute_into(computations, metric_key("VM8"), self._vm8)
        self._compute_into(computations, metric_key("VM9"), self._vm9)
        self._compute_into(computations, metric_key("VM10"), self._vm10)
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
            self._record_vacuous("VM2")
            return 1.0
        correct_flags = sum(record.success is False for record in flagged)
        return correct_flags / len(flagged)

    def _vm3(self) -> float:
        records = self._records_for(self.planned)
        self._validate_error_flags(records)
        unsuccessful = [record for record in records if record.success is False]
        if not unsuccessful:
            self._record_vacuous("VM3")
            return 1.0
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
        return baseline - variant

    def _vm6(self) -> float:
        variants = self.profile.selected_variants(self.scope)
        if not variants:
            raise MetricError("VM6 requires at least one prompt variant")
        condition_ids = ["unperturbed", *[f"prompt/{item.id}" for item in variants]]
        rates = [self._overall_rate(item, item).overall for item in condition_ids]
        return max(rates) - min(rates)

    def _vm7(self, scenario_id: str) -> float:
        scenario_config = next(
            item for item in self.profile.selected_scenarios(self.scope) if item.id == scenario_id
        )
        task_ids = set(self.scope.task_ids) & set(
            self.scope.failure_task_ids.get(scenario_config.id, scenario_config.task_ids)
        )
        if not task_ids:
            raise MetricError(f"Scenario {scenario_id} has no eligible tasks in scope")
        condition_id = f"failure/{scenario_id}"
        self._record_fault_evidence(condition_id, task_ids)
        baseline = self._overall_rate(
            "unperturbed",
            f"unperturbed/for/failure/{scenario_id}",
            task_ids=task_ids,
        ).overall
        scenario = self._overall_rate(condition_id, condition_id, task_ids=task_ids).overall
        return max(0.0, baseline - scenario)

    def _vm8(self) -> float:
        scenarios = self.profile.selected_scenarios(self.scope)
        if not scenarios:
            raise MetricError("VM8 requires at least one transient-failure scenario")
        task_ids = set(self.scope.task_ids)
        for scenario in scenarios:
            task_ids &= set(self.scope.failure_task_ids.get(scenario.id, scenario.task_ids))
        if not task_ids:
            raise MetricError("VM8 requires a nonempty common scenario task set")
        rates = [
            self._overall_rate(
                "unperturbed",
                "unperturbed/for/failure-range",
                task_ids=task_ids,
            ).overall
        ]
        for scenario in scenarios:
            condition_id = f"failure/{scenario.id}"
            rates.append(
                self._overall_rate(
                    condition_id,
                    f"{condition_id}/for/failure-range",
                    task_ids=task_ids,
                ).overall
            )
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

    def _select(
        self,
        condition_id: str,
        repeat: int | None = None,
        task_ids: set[str] | None = None,
    ) -> list[PlannedEpisode]:
        return [
            episode
            for episode in self.planned
            if episode.condition_id == condition_id
            and (repeat is None or episode.repeat == repeat)
            and (task_ids is None or episode.task_id in task_ids)
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
        self,
        condition_id: str,
        evidence_key: str,
        repeat: int | None = None,
        task_ids: set[str] | None = None,
    ) -> RateSummary:
        episodes = self._select(condition_id, repeat, task_ids)
        if not episodes:
            raise MetricError(f"No planned episodes for condition {condition_id}")
        if task_ids is not None and {episode.task_id for episode in episodes} != task_ids:
            raise MetricError(f"Condition {condition_id} has an incomplete comparison task set")
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

    def _record_vacuous(self, vm_id: str) -> None:
        vacuous_metrics = self.intermediates["vacuous_metrics"]
        assert isinstance(vacuous_metrics, list)
        key = metric_key(vm_id)
        if key not in vacuous_metrics:
            vacuous_metrics.append(key)

    def _validate_error_flags(self, records: list[EpisodeRecord]) -> None:
        invalid = [record.episode_id for record in records if record.error_flag_invalid]
        if invalid:
            raise MetricError(
                f"Completed responses have missing or invalid likely_incorrect flags: {invalid}"
            )

    def _record_fault_evidence(self, condition_id: str, task_ids: set[str]) -> None:
        episodes = self._select(condition_id, task_ids=task_ids)
        records = self._records_for(episodes)
        if not records:
            raise MetricError(f"No planned episodes for condition {condition_id}")
        records_by_task: dict[str, list[EpisodeRecord]] = defaultdict(list)
        for record in records:
            records_by_task[record.task_id].append(record)
        exposure_task_rates: dict[str, float] = {}
        recovery_task_rates: dict[str, float | None] = {}
        for task_id, task_records in records_by_task.items():
            exposed = [record for record in task_records if record.fault_exposure is True]
            exposure_task_rates[task_id] = len(exposed) / len(task_records)
            recovery_task_rates[task_id] = (
                sum(record.success is True for record in exposed) / len(exposed)
                if exposed
                else None
            )
        exposed_count = sum(record.fault_exposure is True for record in records)
        recovery_values = [rate for rate in recovery_task_rates.values() if rate is not None]
        fault_exposure_rates = self.intermediates["fault_exposure_rates"]
        fault_recovery_rates = self.intermediates["fault_recovery_success_rates"]
        assert isinstance(fault_exposure_rates, dict)
        assert isinstance(fault_recovery_rates, dict)
        scenario_id = condition_id.split("/", 1)[1]
        fault_exposure_rates[scenario_id] = {
            "overall": exposed_count / len(records),
            "task_rates": exposure_task_rates,
        }
        fault_recovery_rates[scenario_id] = {
            "overall": (
                sum(recovery_values) / len(recovery_values) if recovery_values else None
            ),
            "task_rates": recovery_task_rates,
        }

    def _threshold(self, key: str) -> str:
        key = key.split("/", 1)[0]
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
            "AC1": [metric_key("VM1")],
            "AC2": [metric_key("VM2"), metric_key("VM3")],
            "AC3": [metric_key("VM4")],
            "AC4": [
                *[
                    metric_key("VM5", item.id)
                    for item in self.profile.selected_variants(self.scope)
                ],
                metric_key("VM6"),
            ],
            "AC5": [
                *[
                    metric_key("VM7", item.id)
                    for item in self.profile.selected_scenarios(self.scope)
                ],
                metric_key("VM8"),
            ],
            "AC6": [metric_key("VM9")],
            "AC7": [metric_key("VM10")],
        }

    def _accepted(self, metric_id: str, value: int | float | None) -> bool:
        metric_id = metric_id.split("/", 1)[0]
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
