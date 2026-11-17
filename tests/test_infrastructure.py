from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import Mock

from agent_validation.blocks_world import BlocksWorldEnvironment
from agent_validation.config import load_profile
from agent_validation.episodes import EpisodeExecutor
from agent_validation.errors import BenchmarkError
from agent_validation.protocol import SolutionProcess
from agent_validation.storage import RunStore


def test_benchmark_failure_is_infrastructure_not_solution_failure(
    service_settings, monkeypatch, tmp_path: Path
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    planned = resolved.plan_episodes()[0]

    def fail_benchmark(path: str):
        raise BenchmarkError("simulated benchmark failure")

    monkeypatch.setattr(BlocksWorldEnvironment, "from_pddl", fail_benchmark)
    store = RunStore(tmp_path / "artifacts", "infrastructure-test")
    revision = Mock()
    record = EpisodeExecutor(resolved, revision, store).execute(planned)
    revision.cleanup_episode.assert_called_once_with(planned.episode_id)
    assert record.success is None
    assert record.action_attempts is None
    assert record.final_response_received is None
    assert record.failure_kind == "infrastructure"
    assert record.termination_reason == "infrastructure_error"
    assert "simulated benchmark failure" in record.error_message
    assert (store.run_directory / "episodes.jsonl").exists()


def test_cancellation_before_process_registration_prevents_start(
    service_settings, monkeypatch, tmp_path: Path
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    planned = resolved.plan_episodes()[0]
    store = RunStore(tmp_path / "artifacts", "cancel-before-start")
    revision = Mock()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    revision.episode_workspace.return_value = workspace
    revision.episode_environment.return_value = os.environ.copy()
    executor = EpisodeExecutor(resolved, revision, store)

    def cancel_during_command(episode_id, repeat):
        executor.cancel()
        return [sys.executable, "-c", "pass"]

    revision.solution_command.side_effect = cancel_during_command
    start = Mock()
    monkeypatch.setattr(SolutionProcess, "start", start)
    record = executor.execute(planned)
    assert record.termination_reason == "aborted"
    start.assert_not_called()


def test_cancellation_after_start_closes_process_before_task_delivery(
    service_settings, monkeypatch, tmp_path: Path
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    planned = resolved.plan_episodes()[0]
    store = RunStore(tmp_path / "artifacts", "cancel-after-start")
    revision = Mock()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    revision.episode_workspace.return_value = workspace
    revision.episode_environment.return_value = os.environ.copy()
    revision.solution_command.return_value = [
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
    ]
    executor = EpisodeExecutor(resolved, revision, store)
    original_start = SolutionProcess.start
    processes = []

    def start_and_cancel(process):
        original_start(process)
        processes.append(process)
        executor.cancel()

    monkeypatch.setattr(SolutionProcess, "start", start_and_cancel)
    record = executor.execute(planned)
    assert record.termination_reason == "aborted"
    assert len(processes) == 1
    assert processes[0].process.poll() is not None
