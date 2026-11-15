from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from agent_validation.blocks_world import BlocksWorldEnvironment
from agent_validation.config import load_profile
from agent_validation.contracts import ValidationScope
from agent_validation.episodes import EpisodeExecutor
from agent_validation.errors import BenchmarkError
from agent_validation.storage import RunStore


def test_benchmark_failure_is_infrastructure_not_solution_failure(
    service_settings, monkeypatch, tmp_path: Path
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    planned = resolved.plan_episodes(ValidationScope.PARTIAL)[0]

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
