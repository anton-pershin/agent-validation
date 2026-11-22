from __future__ import annotations

import json
from pathlib import Path

import yaml
from appworld_helpers import appworld_configuration

from agent_validation.application import ServiceSettings, ValidationApplication
from agent_validation.config import load_profile
from agent_validation.contracts import EpisodeRecord, ValidationRequest, ValidationScope


def settings_for(
    tmp_path: Path, task_id: str = "task_one", time_limit_seconds: float = 300
) -> ServiceSettings:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    configuration = appworld_configuration(task_id, solution_module="reliable_agent")
    configuration["criteria"]["AC7"]["time_limit_seconds"] = time_limit_seconds
    (profile_dir / "appworld.yaml").write_text(
        yaml.safe_dump(configuration, sort_keys=False), encoding="utf-8"
    )
    return ServiceSettings(
        profile_dir=profile_dir,
        artifact_root=tmp_path / "artifacts",
        temporary_root=tmp_path / "temporary",
        active_profile="appworld",
        max_active_validations=1,
        max_total_episodes=2,
    )


def run_validation(settings: ServiceSettings, repo: Path, commit: str, behavior: str) -> dict:
    return ValidationApplication(settings).validate(
        ValidationRequest(
            repo=str(repo), commit=commit, solution_overrides=f"--behavior {behavior}"
        ),
        ValidationScope.FULL,
    )


def only_record(settings: ServiceSettings) -> tuple[Path, EpisodeRecord]:
    run_directory = next(settings.artifact_root.iterdir())
    line = (run_directory / "episodes.jsonl").read_text(encoding="utf-8").strip()
    return run_directory, EpisodeRecord.model_validate(json.loads(line))


def test_appworld_full_service_result_evidence_and_cleanup(
    fake_appworld_runtime: Path, git_solution, tmp_path: Path
) -> None:
    repo, commit, _ = git_solution
    settings = settings_for(tmp_path)
    result = run_validation(settings, repo, commit, "appworld_success")
    assert result["profile"] == "appworld"
    assert result["validation_scope"] == "full"
    assert result["AC1"]["status"] == "accepted"
    assert result["AC2"]["status"] == "accepted"
    assert result["AC7"]["status"] == "accepted"
    assert "AC6" not in result
    run_directory, record = only_record(settings)
    assert record.success is True
    assert record.action_attempts == 1
    assert record.failure_kind == "none"
    episode_directory = run_directory / "episodes" / record.episode_id
    evidence = json.loads((episode_directory / "benchmark.json").read_text())
    assert evidence["runtime"]["package_version"] == "0.1.3.post1"
    assert evidence["runtime"]["data_version"] == "0.1.0"
    assert evidence["evaluation_success"] is True
    assert evidence["evaluation_report"] == "PRIVATE_EVALUATION_REPORT"
    assert evidence["resources"]["peak_rss_kib"] > 0
    assert evidence["resources"]["total_cpu_seconds"] >= 0
    assert evidence["output_removed"] is True
    protocol = (episode_directory / "protocol.jsonl").read_text()
    assert "PRIVATE_EVALUATION" not in protocol
    assert "ground_truth" not in protocol
    manifest = json.loads((run_directory / "run.json").read_text())
    assert manifest["implementation"]["benchmark"]["source_commit"] == (
        "66ad8099e12188ece0d3fe45e661dbc01880813b"
    )
    outputs = fake_appworld_runtime / "experiments" / "outputs"
    assert not outputs.exists() or not any(outputs.iterdir())
    assert not Path(manifest["temporary_root"]).exists()


def test_appworld_task_completion_is_required(
    fake_appworld_runtime: Path, git_solution, tmp_path: Path
) -> None:
    repo, commit, _ = git_solution
    settings = settings_for(tmp_path)
    result = run_validation(settings, repo, commit, "appworld_no_complete")
    assert result["AC1"]["status"] == "valid"
    _, record = only_record(settings)
    assert record.success is False
    assert record.completed is True


def test_appworld_evaluator_failure_invalidates_affected_metrics(
    fake_appworld_runtime: Path, git_solution, tmp_path: Path
) -> None:
    repo, commit, _ = git_solution
    settings = settings_for(tmp_path, "eval_crash")
    result = run_validation(settings, repo, commit, "appworld_success")
    assert result["AC1"]["status"] == "invalid"
    run_directory, record = only_record(settings)
    assert record.success is None
    assert record.failure_kind == "infrastructure"
    assert record.termination_reason == "infrastructure_error"
    assert "fake evaluator infrastructure failure" in (record.error_message or "")
    evidence = json.loads(
        (run_directory / "episodes" / record.episode_id / "benchmark.json").read_text()
    )
    assert "cleanup_error" in evidence
    assert evidence["output_removed"] is True
    assert load_profile("appworld", settings.profile_dir).profile.benchmark == "appworld"


def test_appworld_solution_timeout_remains_solution_failure(
    fake_appworld_runtime: Path, git_solution, tmp_path: Path
) -> None:
    repo, commit, _ = git_solution
    settings = settings_for(tmp_path, time_limit_seconds=0.2)
    run_validation(settings, repo, commit, "appworld_slow")
    run_directory, record = only_record(settings)
    assert record.success is False
    assert record.failure_kind == "solution"
    assert record.termination_reason == "timeout"
    evidence = json.loads(
        (run_directory / "episodes" / record.episode_id / "benchmark.json").read_text()
    )
    assert evidence["output_removed"] is True
    assert "cleanup_error" not in evidence
