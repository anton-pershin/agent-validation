from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from agent_validation.config import ResolvedProfile, ValidationProfile
from agent_validation.contracts import (
    ConditionType,
    EpisodeRecord,
    MetricComputation,
    PlannedEpisode,
    ValidationScope,
)
from agent_validation.metrics import CriterionEvaluator, MetricCalculator


def resolved_profile(tmp_path: Path) -> ResolvedProfile:
    scope = {
        "task_ids": ["t1", "t2"],
        "repeats": 2,
        "prompt_variant_ids": ["v1", "v2"],
        "failure_scenario_ids": ["f1", "f2"],
    }
    profile = ValidationProfile.model_validate(
        {
            "name": "metrics",
            "original_instructions": "Solve",
            "tasks": [
                {"id": "t1", "problem": "t1.pddl"},
                {"id": "t2", "problem": "t2.pddl"},
            ],
            "prompt_variants": [
                {"id": "v1", "instructions": "Variant one"},
                {"id": "v2", "instructions": "Variant two"},
            ],
            "failure_scenarios": [
                {"id": "f1", "action_name": "pick_up", "task_ids": ["t1", "t2"]},
                {"id": "f2", "action_name": "stack", "task_ids": ["t1", "t2"]},
            ],
            "full": scope,
            "partial": scope,
            "criteria": {
                "AC1": {"min_success_rate": 0.70},
                "AC2": {"min_precision": 0.80, "min_recall": 0.50},
                "AC3": {"max_range": 0.50},
                "AC4": {"max_loss": 0.50, "max_range": 0.75},
                "AC5": {"max_loss": 0.25, "max_range": 0.25},
                "AC6": {"max_action_attempts": 12},
                "AC7": {"min_timely_response_rate": 0.90, "time_limit_seconds": 300},
            },
        }
    )
    return ResolvedProfile(profile=profile, path=tmp_path / "metrics.yaml", sha256="test")


def successful(condition_id: str, task_id: str, repeat: int) -> bool:
    if condition_id in {"unperturbed", "failure/f1"}:
        return not (task_id == "t2" and repeat == 2)
    if condition_id == "prompt/v1":
        return not ((task_id == "t1" and repeat == 2) or task_id == "t2")
    if condition_id == "prompt/v2":
        return True
    if condition_id == "failure/f2":
        return repeat == 1
    raise AssertionError(condition_id)


def metric_population(tmp_path: Path):
    resolved = resolved_profile(tmp_path)
    planned = []
    for repeat in (1, 2):
        for task_id in ("t1", "t2"):
            conditions = [
                ("unperturbed", ConditionType.UNPERTURBED),
                ("prompt/v1", ConditionType.PROMPT),
                ("prompt/v2", ConditionType.PROMPT),
            ]
            conditions.extend(
                (f"failure/{scenario.id}", ConditionType.FAILURE)
                for scenario in resolved.profile.failure_scenarios
                if task_id in scenario.task_ids
            )
            for condition_id, condition_type in conditions:
                planned.append(
                    PlannedEpisode(
                        episode_id=f"{task_id}-{repeat}-{condition_id.replace('/', '-')}",
                        task_id=task_id,
                        condition_id=condition_id,
                        condition_type=condition_type,
                        repeat=repeat,
                        instructions="Solve",
                        task_input="public",
                        problem_path="unused",
                        fault_scenario_id=(
                            condition_id.split("/")[1]
                            if condition_type == ConditionType.FAILURE
                            else None
                        ),
                    )
                )
    records = []
    unsuccessful = [
        episode
        for episode in planned
        if not successful(episode.condition_id, episode.task_id, episode.repeat)
    ]
    assert len(unsuccessful) == 7
    no_response = {episode.episode_id for episode in unsuccessful[-2:]}
    flagged_failures = {episode.episode_id for episode in unsuccessful[:-2]}
    successful_false_positive = next(
        episode.episode_id
        for episode in planned
        if successful(episode.condition_id, episode.task_id, episode.repeat)
    )
    for index, episode in enumerate(planned):
        success = successful(episode.condition_id, episode.task_id, episode.repeat)
        has_response = episode.episode_id not in no_response
        flagged = (
            episode.episode_id in flagged_failures
            or episode.episode_id == successful_false_positive
        )
        records.append(
            EpisodeRecord(
                episode_id=episode.episode_id,
                task_id=episode.task_id,
                condition_id=episode.condition_id,
                condition_type=episode.condition_type,
                repeat=episode.repeat,
                success=success,
                likely_incorrect=flagged if has_response else None,
                action_attempts=index % 13,
                final_response_received=has_response,
                elapsed_seconds=10.0,
                completed=success and has_response,
                answer="answer" if has_response else None,
                confidence=0.9 if has_response else None,
                reason="fixture" if has_response else None,
                termination_reason="completed" if has_response else "solution_exit",
                fault_exposure=(True if episode.condition_type == ConditionType.FAILURE else None),
            )
        )
    return resolved, planned, records


def test_all_metrics_match_independent_values_and_all_criteria_accept(tmp_path: Path) -> None:
    resolved, planned, records = metric_population(tmp_path)
    calculator = MetricCalculator(resolved, ValidationScope.FULL, planned, records)
    computations = calculator.compute()
    expected = {
        "VM1/unperturbed_overall_success_rate": 0.75,
        "VM2/error_flag_precision": 5 / 6,
        "VM3/error_flag_recall": 5 / 7,
        "VM4/repeated_run_overall_success_rate_range": 0.5,
        "VM5/prompt_variant_overall_success_rate_loss/v1": 0.5,
        "VM5/prompt_variant_overall_success_rate_loss/v2": -0.25,
        "VM6/prompt_variant_overall_success_rate_range": 0.75,
        "VM7/transient_failure_overall_success_rate_loss/f1": 0.0,
        "VM7/transient_failure_overall_success_rate_loss/f2": 0.25,
        "VM8/transient_failure_overall_success_rate_range": 0.25,
        "VM9/maximum_action_attempts": 12,
        "VM10/timely_final_response_rate": 0.9,
    }
    assert set(computations) == set(expected)
    for key, value in expected.items():
        assert computations[key].computed_value == pytest.approx(value), key
        assert computations[key].error_message is None, key
    result = CriterionEvaluator(resolved, ValidationScope.FULL, computations).evaluate()
    assert {key: value.status for key, value in result.criteria.items()} == {
        f"AC{index}": "accepted" for index in range(1, 8)
    }
    assert calculator.intermediates["task_success_rates"]["unperturbed"] == {
        "t1": 1.0,
        "t2": 0.5,
    }


def test_vm5_reports_variant_gain_as_negative_loss_and_accepts_it(tmp_path: Path) -> None:
    resolved, planned, records = metric_population(tmp_path)
    computations = MetricCalculator(resolved, ValidationScope.FULL, planned, records).compute()
    metric_id = "VM5/prompt_variant_overall_success_rate_loss/v2"
    assert computations[metric_id].computed_value == pytest.approx(-0.25)
    result = CriterionEvaluator(resolved, ValidationScope.FULL, computations).evaluate()
    metric = result.criteria["AC4"].metrics_status[metric_id]
    assert metric.computed_value == pytest.approx(-0.25)
    assert metric.acceptance_status == "accepted"


def test_rejected_metric_makes_valid_and_failed_metric_makes_invalid(tmp_path: Path) -> None:
    resolved, planned, records = metric_population(tmp_path)
    computations = MetricCalculator(resolved, ValidationScope.FULL, planned, records).compute()
    rejected = deepcopy(computations)
    rejected["VM1/unperturbed_overall_success_rate"].computed_value = 0.60
    result = CriterionEvaluator(resolved, ValidationScope.FULL, rejected).evaluate()
    assert result.criteria["AC1"].status == "valid"
    failed = deepcopy(computations)
    failed["VM2/error_flag_precision"] = MetricComputation(
        metric_id="VM2/error_flag_precision",
        computed_value=None,
        expected_value_or_threshold=">= 0.8",
        error_message="missing",
    )
    result = CriterionEvaluator(resolved, ValidationScope.FULL, failed).evaluate()
    assert result.criteria["AC2"].status == "invalid"
    metric = result.to_response()["AC2"]["metrics_status"]["VM2/error_flag_precision"]
    assert metric["computation_status"] == "failed_to_compute"
    assert metric["acceptance_status"] is None


def test_zero_precision_denominator_uses_vacuous_value_and_invalid_flags_still_fail(
    tmp_path: Path,
) -> None:
    resolved, planned, records = metric_population(tmp_path)
    no_flags = deepcopy(records)
    for record in no_flags:
        record.likely_incorrect = False
    calculator = MetricCalculator(resolved, ValidationScope.FULL, planned, no_flags)
    computations = calculator.compute()
    assert computations["VM2/error_flag_precision"].computed_value == 1.0
    assert computations["VM3/error_flag_recall"].computed_value == 0.0
    assert calculator.intermediates["vacuous_metrics"] == ["VM2/error_flag_precision"]
    invalid_flags = deepcopy(records)
    invalid_flags[0].error_flag_invalid = True
    computations = MetricCalculator(
        resolved, ValidationScope.FULL, planned, invalid_flags
    ).compute()
    assert computations["VM2/error_flag_precision"].error_message is not None
    assert computations["VM3/error_flag_recall"].error_message is not None


def test_zero_recall_denominator_uses_vacuous_value(tmp_path: Path) -> None:
    resolved, planned, records = metric_population(tmp_path)
    for record in records:
        record.success = True
        record.likely_incorrect = True
    calculator = MetricCalculator(resolved, ValidationScope.FULL, planned, records)
    computations = calculator.compute()
    assert computations["VM2/error_flag_precision"].computed_value == 0.0
    assert computations["VM3/error_flag_recall"].computed_value == 1.0
    assert calculator.intermediates["vacuous_metrics"] == ["VM3/error_flag_recall"]


def test_perfect_population_computes_both_metrics_and_accepts_ac2(tmp_path: Path) -> None:
    resolved, planned, records = metric_population(tmp_path)
    for record in records:
        record.success = True
        record.likely_incorrect = False
    calculator = MetricCalculator(resolved, ValidationScope.FULL, planned, records)
    computations = calculator.compute()
    assert computations["VM2/error_flag_precision"].computed_value == 1.0
    assert computations["VM3/error_flag_recall"].computed_value == 1.0
    assert set(calculator.intermediates["vacuous_metrics"]) == {
        "VM2/error_flag_precision",
        "VM3/error_flag_recall",
    }
    result = CriterionEvaluator(resolved, ValidationScope.FULL, computations).evaluate()
    assert result.criteria["AC2"].status == "accepted"


def test_missing_record_insufficient_repeats_and_infrastructure_failure(tmp_path: Path) -> None:
    resolved, planned, records = metric_population(tmp_path)
    unperturbed_id = next(
        episode.episode_id for episode in planned if episode.condition_id == "unperturbed"
    )
    missing = [record for record in records if record.episode_id != unperturbed_id]
    computations = MetricCalculator(resolved, ValidationScope.FULL, planned, missing).compute()
    assert computations["VM1/unperturbed_overall_success_rate"].error_message is not None
    one_repeat_planned = [episode for episode in planned if episode.repeat == 1]
    one_repeat_records = [record for record in records if record.repeat == 1]
    computations = MetricCalculator(
        resolved, ValidationScope.FULL, one_repeat_planned, one_repeat_records
    ).compute()
    assert (
        "at least two" in computations["VM4/repeated_run_overall_success_rate_range"].error_message
    )
    infrastructure = deepcopy(records)
    infrastructure[0].success = None
    computations = MetricCalculator(
        resolved, ValidationScope.FULL, planned, infrastructure
    ).compute()
    assert computations["VM9/maximum_action_attempts"].error_message is not None


def test_non_exposed_outcomes_count_in_scenario_metrics_and_coverage_evidence(
    tmp_path: Path,
) -> None:
    resolved, planned, records = metric_population(tmp_path)
    changed = deepcopy(records)
    next(record for record in changed if record.condition_id == "failure/f1").fault_exposure = False
    next(
        record
        for record in changed
        if record.condition_id == "failure/f1" and record.success is False
    ).fault_exposure = False
    calculator = MetricCalculator(resolved, ValidationScope.FULL, planned, changed)
    computations = calculator.compute()
    assert computations[
        "VM7/transient_failure_overall_success_rate_loss/f1"
    ].computed_value == pytest.approx(0.0)
    assert computations[
        "VM8/transient_failure_overall_success_rate_range"
    ].computed_value == pytest.approx(0.25)
    assert computations[
        "VM7/transient_failure_overall_success_rate_loss/f1"
    ].error_message is None
    assert computations[
        "VM8/transient_failure_overall_success_rate_range"
    ].error_message is None
    assert calculator.intermediates["task_success_rates"]["failure/f1"] == {
        "t1": 1.0,
        "t2": 0.5,
    }
    assert calculator.intermediates["fault_exposure_rates"]["f1"] == {
        "overall": 0.5,
        "task_rates": {"t1": 0.5, "t2": 0.5},
    }
    assert calculator.intermediates["fault_recovery_success_rates"]["f1"] == {
        "overall": 1.0,
        "task_rates": {"t1": 1.0, "t2": 1.0},
    }


def test_zero_fault_exposure_keeps_metrics_computable_and_recovery_null(
    tmp_path: Path,
) -> None:
    resolved, planned, records = metric_population(tmp_path)
    changed = deepcopy(records)
    for record in changed:
        if record.condition_id == "failure/f1":
            record.fault_exposure = False
    calculator = MetricCalculator(resolved, ValidationScope.FULL, planned, changed)
    computations = calculator.compute()
    assert computations[
        "VM7/transient_failure_overall_success_rate_loss/f1"
    ].computed_value == pytest.approx(0.0)
    assert computations[
        "VM8/transient_failure_overall_success_rate_range"
    ].computed_value == pytest.approx(0.25)
    assert calculator.intermediates["fault_exposure_rates"]["f1"] == {
        "overall": 0.0,
        "task_rates": {"t1": 0.0, "t2": 0.0},
    }
    assert calculator.intermediates["fault_recovery_success_rates"]["f1"] == {
        "overall": None,
        "task_rates": {"t1": None, "t2": None},
    }


def test_failure_comparisons_use_matched_task_sets(tmp_path: Path) -> None:
    resolved, planned, records = metric_population(tmp_path)
    resolved.profile.failure_scenarios[0].task_ids = ["t1"]
    planned = [
        episode
        for episode in planned
        if not (episode.condition_id == "failure/f1" and episode.task_id == "t2")
    ]
    records = [
        record
        for record in records
        if not (record.condition_id == "failure/f1" and record.task_id == "t2")
    ]
    changed = next(
        record
        for record in records
        if record.condition_id == "failure/f1" and record.task_id == "t1" and record.repeat == 2
    )
    changed.success = False
    computations = MetricCalculator(resolved, ValidationScope.FULL, planned, records).compute()
    assert computations[
        "VM7/transient_failure_overall_success_rate_loss/f1"
    ].computed_value == pytest.approx(0.5)
    assert computations[
        "VM7/transient_failure_overall_success_rate_loss/f2"
    ].computed_value == pytest.approx(0.25)
    assert computations[
        "VM8/transient_failure_overall_success_rate_range"
    ].computed_value == pytest.approx(0.5)
    baseline = computations["VM1/unperturbed_overall_success_rate"].computed_value
    assert baseline == pytest.approx(0.75)


def test_record_metadata_must_match_the_planned_episode(tmp_path: Path) -> None:
    resolved, planned, records = metric_population(tmp_path)
    records[0].task_id = "contradictory"
    computations = MetricCalculator(resolved, ValidationScope.FULL, planned, records).compute()
    assert (
        "metadata does not match"
        in computations["VM1/unperturbed_overall_success_rate"].error_message
    )
