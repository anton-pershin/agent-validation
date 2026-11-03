from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from test_end_to_end import records_for, run_directories, run_validation

from agent_validation.application import ServiceSettings
from agent_validation.blocks_world import BlocksWorldEnvironment


def one_episode(settings: ServiceSettings, time_limit: float = 0.3) -> ServiceSettings:
    path = settings.profile_dir / "test.yaml"
    data = yaml.safe_load(path.read_text())
    scope = {
        "task_ids": ["stack_ab"],
        "repeats": 1,
        "prompt_variant_ids": [],
        "failure_scenario_ids": [],
    }
    data["full"] = scope
    data["partial"] = scope
    data["criteria"]["AC7"]["time_limit_seconds"] = time_limit
    data["resources"]["max_output_bytes"] = 1_000_000
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return settings


def only_record(settings: ServiceSettings):
    return records_for(run_directories(settings)[0])[0]


@pytest.mark.parametrize("behavior", ["partial_line", "non_reading"])
def test_blocked_protocol_io_still_times_out(
    service_settings: ServiceSettings, git_solution, behavior: str
) -> None:
    settings = one_episode(service_settings)
    if behavior == "non_reading":
        problem = settings.profile_dir.parent / "problem.pddl"
        problem.write_text(problem.read_text() + ";" + "x" * 200_000 + "\n")
    repo, first, _ = git_solution
    run_validation(settings, repo, first, behavior)
    record = only_record(settings)
    assert record.termination_reason == "timeout"
    assert record.elapsed_seconds < 3


def test_slow_action_is_stopped_at_deadline(
    service_settings: ServiceSettings, git_solution, monkeypatch
) -> None:
    settings = one_episode(service_settings, time_limit=0.1)
    original = BlocksWorldEnvironment.execute

    def slow_execute(self, action, injected_failure=None):
        time.sleep(0.3)
        return original(self, action, injected_failure)

    monkeypatch.setattr(BlocksWorldEnvironment, "execute", slow_execute)
    repo, first, _ = git_solution
    run_validation(settings, repo, first)
    assert only_record(settings).termination_reason == "timeout"


def test_scoring_time_is_excluded_from_response_time(
    service_settings: ServiceSettings, git_solution, monkeypatch
) -> None:
    settings = one_episode(service_settings, time_limit=1)
    original = BlocksWorldEnvironment.goal_satisfied

    def slow_score(self):
        time.sleep(0.3)
        return original(self)

    monkeypatch.setattr(BlocksWorldEnvironment, "goal_satisfied", slow_score)
    repo, first, _ = git_solution
    run_validation(settings, repo, first)
    record = only_record(settings)
    assert record.success is True
    assert record.elapsed_seconds < 0.25


def test_stderr_flood_cannot_be_hidden_by_fast_final_response(
    service_settings: ServiceSettings, git_solution
) -> None:
    settings = one_episode(service_settings)
    path = settings.profile_dir / "test.yaml"
    data = yaml.safe_load(path.read_text())
    data["resources"]["max_output_bytes"] = 1024
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    repo, first, _ = git_solution
    run_validation(settings, repo, first, "stderr_flood")
    record = only_record(settings)
    assert record.termination_reason == "output_limit_exceeded"
    assert record.success is False


@pytest.mark.parametrize(
    "behavior",
    ["empty_batch", "missing_call_id", "bool_version", "float_version", "huge_confidence"],
)
def test_malformed_protocol_values_are_solution_failures(
    service_settings: ServiceSettings, git_solution, behavior: str
) -> None:
    settings = one_episode(service_settings)
    repo, first, _ = git_solution
    run_validation(settings, repo, first, behavior)
    record = only_record(settings)
    assert record.termination_reason == "malformed_output"
    assert record.failure_kind == "solution"
    if behavior in {"empty_batch", "missing_call_id"}:
        assert record.action_attempts == 0


def test_malformed_incomplete_final_does_not_invalidate_error_flags(
    service_settings: ServiceSettings, git_solution
) -> None:
    settings = one_episode(service_settings)
    repo, first, _ = git_solution
    run_validation(settings, repo, first, "malformed_final")
    record = only_record(settings)
    assert record.final_response_received is False
    assert record.error_flag_invalid is False
    assert record.likely_incorrect is None


def test_goal_without_completion_declaration_is_unsuccessful(
    service_settings: ServiceSettings, git_solution
) -> None:
    settings = one_episode(service_settings)
    repo, first, _ = git_solution
    run_validation(settings, repo, first, "stop_after_goal")
    record = only_record(settings)
    assert record.success is False
    assert record.completed is False
    assert record.final_response_received is True
    assert record.termination_reason == "stopped"


def test_malformed_action_does_not_confirm_fault_exposure(
    service_settings: ServiceSettings, git_solution
) -> None:
    repo, first, _ = git_solution
    run_validation(service_settings, repo, first, "malformed_action_fault")
    records = records_for(run_directories(service_settings)[0])
    failure_records = [record for record in records if record.condition_type == "failure"]
    assert len(failure_records) == 2
    assert all(record.fault_exposure is False for record in failure_records)
    assert all(record.action_attempts == 1 for record in failure_records)


def test_home_and_installed_package_state_do_not_leak_between_episodes(
    service_settings: ServiceSettings, git_solution
) -> None:
    repo, first, _ = git_solution
    run_validation(service_settings, repo, first, "mutate_state")
    records = records_for(run_directories(service_settings)[0])
    assert all(record.success is True for record in records)
    assert {record.answer for record in records} == {"revision-one"}


def test_escaped_session_child_is_terminated(
    service_settings: ServiceSettings, git_solution
) -> None:
    settings = one_episode(service_settings, time_limit=0.2)
    repo, first, _ = git_solution
    run_validation(settings, repo, first, "timeout_escape")
    assert only_record(settings).termination_reason == "timeout"
    children = []
    for command_file in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = command_file.read_bytes()
        except OSError:
            continue
        if b"agent-validation-test-child" in command:
            children.append(command_file)
    assert not children
