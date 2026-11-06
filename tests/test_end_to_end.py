from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from agent_validation.application import ServiceSettings, ValidationApplication
from agent_validation.config import ValidationProfile, load_profile
from agent_validation.contracts import (
    EpisodeRecord,
    PlannedEpisode,
    ValidationRequest,
    ValidationScope,
)
from agent_validation.metrics import CriterionEvaluator, MetricCalculator


def run_validation(
    settings: ServiceSettings,
    repo: Path,
    commit: str,
    behavior: str = "success",
) -> dict:
    request = ValidationRequest(
        repo=str(repo),
        commit=commit,
        solution_overrides=f"--behavior {behavior}",
    )
    return ValidationApplication(settings).validate(request, ValidationScope.PARTIAL)


def run_directories(settings: ServiceSettings) -> list[Path]:
    return sorted(settings.artifact_root.iterdir())


def records_for(run_directory: Path) -> list[EpisodeRecord]:
    return [
        EpisodeRecord.model_validate(json.loads(line))
        for line in (run_directory / "episodes.jsonl").read_text().splitlines()
    ]


def test_repeat_placeholder_reaches_each_episode_solution(
    service_settings: ServiceSettings, git_solution
) -> None:
    repo, first, _ = git_solution
    request = ValidationRequest(
        repo=str(repo),
        commit=first,
        solution_overrides="--behavior echo_seed --seed {repeat}",
    )
    ValidationApplication(service_settings).validate(request, ValidationScope.PARTIAL)
    run_directory = run_directories(service_settings)[0]
    records = records_for(run_directory)
    assert len(records) == 6
    assert {record.answer for record in records} == {"1", "2"}
    assert all(record.answer == str(record.repeat) for record in records)
    configuration = json.loads((run_directory / "run.json").read_text())
    assert configuration["solution_overrides"] == "--behavior echo_seed --seed {repeat}"


def test_exact_commit_evidence_recomputation_and_cleanup(
    service_settings: ServiceSettings, git_solution
) -> None:
    repo, first, second = git_solution
    source_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result = run_validation(service_settings, repo, first)
    assert result["validation_scope"] == "partial"
    assert result["AC1"]["status"] == "accepted"
    assert result["AC2"]["status"] == "accepted"
    assert result["AC2"]["metrics_status"]["VM2/error_flag_precision"]["computed_value"] == 1.0
    assert result["AC2"]["metrics_status"]["VM3/error_flag_recall"]["computed_value"] == 1.0
    first_run = run_directories(service_settings)[0]
    run_configuration = json.loads((first_run / "run.json").read_text())
    assert run_configuration["verified_commit"] == first
    assert run_configuration["status"] == "completed"
    assert "typing_extensions" in run_configuration["solution_dependencies"]
    assert (first_run / "provisioning.json").exists()
    assert not Path(run_configuration["temporary_root"]).exists()
    first_records = records_for(first_run)
    assert len(first_records) == 6
    assert all(record.success is True for record in first_records)
    assert {record.answer for record in first_records} == {"revision-one"}
    assert sum(record.fault_exposure is True for record in first_records) == 2

    protocol_lines = (
        (first_run / "episodes" / first_records[0].episode_id / "protocol.jsonl")
        .read_text()
        .splitlines()
    )
    task_entry = json.loads(protocol_lines[0])["message"]
    assert set(task_entry) == {"protocol_version", "type", "episode_id", "task"}
    assert set(task_entry["task"]) == {"id", "instructions", "input", "tools"}
    serialized_task = json.dumps(task_entry)
    assert "goal_achieved" not in serialized_task
    assert "reference_plan" not in serialized_task
    assert "condition_id" not in serialized_task

    planned = [
        PlannedEpisode.model_validate(item)
        for item in json.loads((first_run / "planned_episodes.json").read_text())
    ]
    resolved = load_profile("test", service_settings.profile_dir)
    recomputed = MetricCalculator(resolved, ValidationScope.PARTIAL, planned, first_records)
    recomputed_result = CriterionEvaluator(
        resolved, ValidationScope.PARTIAL, recomputed.compute()
    ).evaluate()
    assert recomputed_result.to_response() == result

    run_validation(service_settings, repo, second)
    second_run = run_directories(service_settings)[1]
    second_configuration = json.loads((second_run / "run.json").read_text())
    second_records = records_for(second_run)
    assert second_configuration["verified_commit"] == second
    assert {record.answer for record in second_records} == {"revision-two"}
    assert second_configuration["temporary_root"] != run_configuration["temporary_root"]
    assert not Path(second_configuration["temporary_root"]).exists()
    assert not service_settings.temporary_root.exists() or not any(
        service_settings.temporary_root.iterdir()
    )
    current_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert current_status == source_status


def test_overlapping_validations_do_not_mix_revisions_or_evidence(
    service_settings: ServiceSettings, git_solution
) -> None:
    repo, first, second = git_solution
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(run_validation, service_settings, repo, first)
        second_future = pool.submit(run_validation, service_settings, repo, second)
        first_future.result()
        second_future.result()
    runs = run_directories(service_settings)
    assert len(runs) == 2
    answers = [{record.answer for record in records_for(run_directory)} for run_directory in runs]
    assert {frozenset(items) for items in answers} == {
        frozenset({"revision-one"}),
        frozenset({"revision-two"}),
    }
    for run_directory in runs:
        configuration = json.loads((run_directory / "run.json").read_text())
        assert not Path(configuration["temporary_root"]).exists()


def test_concurrent_episodes_keep_state_isolated(
    service_settings: ServiceSettings, git_solution
) -> None:
    profile_path = service_settings.profile_dir / "test.yaml"
    data = yaml.safe_load(profile_path.read_text())
    data["resources"]["episode_concurrency"] = 2
    profile_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    repo, first, _ = git_solution
    run_validation(service_settings, repo, first, "mutate_state")
    records = records_for(run_directories(service_settings)[0])
    assert len(records) == 6
    assert all(record.success is True for record in records)
    assert {record.answer for record in records} == {"revision-one"}


def single_episode_settings(settings: ServiceSettings, **criteria_updates) -> ServiceSettings:
    profile_path = settings.profile_dir / "test.yaml"
    data = yaml.safe_load(profile_path.read_text())
    scope = {
        "task_ids": ["stack_ab"],
        "repeats": 1,
        "prompt_variant_ids": [],
        "failure_scenario_ids": [],
    }
    data["full"] = scope
    data["partial"] = scope
    for criterion, values in criteria_updates.items():
        data["criteria"].setdefault(criterion, {}).update(values)
    profile_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return settings


@pytest.mark.parametrize(
    ("behavior", "termination", "success", "final_received"),
    [
        ("wrong", "completed", False, True),
        ("unflagged_failure", "completed", False, True),
        ("crash", "solution_exit", False, False),
        ("malformed", "malformed_output", False, False),
        ("no_final", "solution_exit", False, False),
        ("budget", "action_budget_exhausted", False, False),
    ],
)
def test_solution_failure_records(
    service_settings: ServiceSettings,
    git_solution,
    behavior: str,
    termination: str,
    success: bool,
    final_received: bool,
) -> None:
    settings = single_episode_settings(service_settings)
    repo, first, _ = git_solution
    result = run_validation(settings, repo, first, behavior)
    record = records_for(run_directories(settings)[0])[0]
    assert record.termination_reason == termination
    assert record.success is success
    assert record.final_response_received is final_received
    assert record.failure_kind == ("none" if final_received else "solution")
    if behavior == "budget":
        assert record.action_attempts == 60
        assert result["AC6"]["status"] == "valid"
        assert (
            result["AC6"]["metrics_status"]["VM9/maximum_action_attempts"]["computed_value"] == 60
        )


@pytest.mark.parametrize("behavior", ["missing_flag", "invalid_flag"])
def test_invalid_self_assessment_is_retained_and_fails_error_awareness(
    service_settings: ServiceSettings, git_solution, behavior: str
) -> None:
    settings = single_episode_settings(service_settings)
    repo, first, _ = git_solution
    result = run_validation(settings, repo, first, behavior)
    record = records_for(run_directories(settings)[0])[0]
    assert record.success is True
    assert record.final_response_received is True
    assert record.likely_incorrect is None
    assert record.error_flag_invalid is True
    assert result["AC2"]["status"] == "invalid"


def test_timeout_kills_solution_process_group(
    service_settings: ServiceSettings, git_solution
) -> None:
    settings = single_episode_settings(service_settings, AC7={"time_limit_seconds": 0.2})
    repo, first, _ = git_solution
    result = run_validation(settings, repo, first, "timeout")
    record = records_for(run_directories(settings)[0])[0]
    assert record.termination_reason == "timeout"
    assert record.elapsed_seconds < 3
    assert result["AC7"]["status"] == "valid"
    children = []
    for command_file in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = command_file.read_bytes()
        except OSError:
            continue
        if b"agent-validation-test-child" in command:
            children.append(command_file)
    assert not children


def test_output_limit_is_enforced(service_settings: ServiceSettings, git_solution) -> None:
    settings = single_episode_settings(service_settings)
    profile_path = settings.profile_dir / "test.yaml"
    data = yaml.safe_load(profile_path.read_text())
    data["resources"]["max_output_bytes"] = 1024
    profile_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    repo, first, _ = git_solution
    run_validation(settings, repo, first, "output_flood")
    record = records_for(run_directories(settings)[0])[0]
    assert record.termination_reason == "output_limit_exceeded"
    assert record.success is False


def test_credentials_are_redacted_from_evidence(
    service_settings: ServiceSettings, git_solution, monkeypatch
) -> None:
    settings = single_episode_settings(service_settings)
    repo, first, _ = git_solution
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    request = ValidationRequest(
        repo=str(repo),
        commit=first,
        solution_overrides="--behavior log_secret --api-key override-secret",
    )
    ValidationApplication(settings).validate(request, ValidationScope.PARTIAL)
    run_directory = run_directories(settings)[0]
    for path in run_directory.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        assert "provider-secret" not in content, path
        assert "override-secret" not in content, path


def test_short_and_proxy_secrets_are_redacted_from_all_evidence(
    service_settings: ServiceSettings, git_solution, monkeypatch
) -> None:
    settings = single_episode_settings(service_settings)
    repo, first, _ = git_solution
    monkeypatch.setenv("OPENAI_API_KEY", "Z9!")
    monkeypatch.setenv("HTTPS_PROXY", "https://user:px@proxy.invalid/")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    request = ValidationRequest(
        repo=str(repo),
        commit=first,
        solution_overrides="--behavior log_secret --api-key s3",
    )
    ValidationApplication(settings).validate(request, ValidationScope.PARTIAL)
    run_directory = run_directories(settings)[0]
    for path in run_directory.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        assert "Z9!" not in content, path
        assert "px" not in content, path
        assert "s3" not in content, path


def test_one_character_secret_does_not_corrupt_typed_evidence(
    service_settings: ServiceSettings, git_solution, monkeypatch
) -> None:
    repo, first, _ = git_solution
    monkeypatch.setenv("OPENAI_API_KEY", "p")
    request = ValidationRequest(
        repo=str(repo), commit=first, solution_overrides="--behavior log_secret"
    )
    ValidationApplication(service_settings).validate(request, ValidationScope.PARTIAL)
    run_directory = run_directories(service_settings)[0]
    records = records_for(run_directory)
    assert len(records) == 6
    assert {record.condition_type.value for record in records} == {
        "unperturbed",
        "prompt",
        "failure",
    }
    assert all(record.answer == "<redacted>" for record in records)
    planned = [
        PlannedEpisode.model_validate(item)
        for item in json.loads((run_directory / "planned_episodes.json").read_text())
    ]
    assert len(planned) == 6
    configuration = json.loads((run_directory / "run.json").read_text())
    assert configuration["validation_scope"] == "partial"
    assert configuration["status"] == "completed"
    ValidationProfile.model_validate(configuration["profile"])


def test_overrides_are_not_executed_through_a_shell(
    service_settings: ServiceSettings, git_solution, tmp_path: Path
) -> None:
    settings = single_episode_settings(service_settings)
    repo, first, _ = git_solution
    marker = tmp_path / "shell-was-used"
    request = ValidationRequest(
        repo=str(repo),
        commit=first,
        solution_overrides=f"--behavior success; touch {marker}",
    )
    ValidationApplication(settings).validate(request, ValidationScope.PARTIAL)
    assert not marker.exists()


@pytest.mark.parametrize(
    ("behavior", "expected_action_counts"),
    [
        ("catalog", {3, 4}),
        ("batch", {2, 4}),
        ("batch_after_goal", {3, 5}),
    ],
)
def test_catalog_batches_and_actions_after_goal(
    service_settings: ServiceSettings,
    git_solution,
    behavior: str,
    expected_action_counts: set[int],
) -> None:
    repo, first, _ = git_solution
    result = run_validation(service_settings, repo, first, behavior)
    records = records_for(run_directories(service_settings)[0])
    assert all(record.success is True for record in records)
    assert {record.action_attempts for record in records} == expected_action_counts
    assert result["AC6"]["status"] == "accepted"


def test_default_partial_population(git_solution, tmp_path: Path) -> None:
    repo, first, _ = git_solution
    settings = ServiceSettings(
        profile_dir=Path(__file__).resolve().parents[1] / "config" / "profiles",
        artifact_root=tmp_path / "artifacts",
        temporary_root=tmp_path / "temporary",
        active_profile="default",
    )
    result = run_validation(settings, repo, first)
    records = records_for(run_directories(settings)[0])
    assert result["validation_scope"] == "partial"
    assert len(records) == 42
    assert all(record.success is True for record in records)
    for criterion in ("AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7"):
        assert result[criterion]["status"] == "accepted", criterion
    assert {record.condition_id for record in records} == {
        "unperturbed",
        "prompt/concise",
        "prompt/formal",
        "failure/pick_up_failure",
        "failure/unstack_failure",
    }
