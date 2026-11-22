from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_validation.api import create_app
from agent_validation.application import ServiceSettings
from agent_validation.config import ValidationProfile, load_profile, resolve_partial_profile
from agent_validation.errors import InvalidRequestError

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "config" / "profiles"


def test_appworld_registered_profile_and_population() -> None:
    resolved = load_profile("appworld", PROFILE_DIR)
    profile = resolved.profile
    assert profile.benchmark == "appworld"
    assert profile.solution_module == "reliable_agent_appworld"
    assert profile.appworld is not None
    assert profile.appworld.package_version == "0.1.3.post1"
    assert profile.appworld.source_commit == "66ad8099e12188ece0d3fe45e661dbc01880813b"
    assert profile.appworld.data_version == "0.1.0"
    assert Counter(task.split for task in profile.tasks) == {
        "train": 90,
        "dev": 57,
        "test_normal": 168,
    }
    assert len({task.id for task in profile.tasks}) == 315
    assert profile.full.task_ids == [task.id for task in profile.tasks]
    assert profile.full.repeats == 2
    assert profile.full.prompt_variant_ids == []
    assert profile.full.failure_scenario_ids == []
    assert profile.acceptance_criteria == ["AC1", "AC2", "AC3", "AC7"]
    assert profile.criteria.AC1.min_success_rate == 0.95
    assert profile.criteria.AC2.min_precision == 0.80
    assert profile.criteria.AC2.min_recall == 0.50
    assert profile.criteria.AC3.max_range == 0.05
    assert profile.criteria.AC7.min_timely_response_rate == 0.95
    assert profile.criteria.AC7.time_limit_seconds == 300
    assert profile.resources.episode_concurrency == 1
    episodes = resolved.plan_episodes()
    assert len(episodes) == 630
    assert len({episode.episode_id for episode in episodes}) == 630
    assert {episode.repeat for episode in episodes} == {1, 2}
    assert {episode.condition_id for episode in episodes} == {"unperturbed"}
    assert all(episode.problem_path is None for episode in episodes)
    assert all(episode.task_input == "" for episode in episodes)


def test_appworld_discovery_and_fine_grained_partial_configuration(tmp_path: Path) -> None:
    settings = ServiceSettings(
        profile_dir=PROFILE_DIR,
        artifact_root=tmp_path / "artifacts",
        temporary_root=tmp_path / "temporary",
        active_profile="appworld",
    )
    response = TestClient(create_app(settings)).get("/profiles/appworld/configuration")
    assert response.status_code == 200
    document = response.json()
    assert document["profile"] == "appworld"
    assert document["configuration"]["benchmark"] == "appworld"
    assert "AppWorldConfig" in str(document["schema"])
    base = load_profile("appworld", PROFILE_DIR)
    configuration = deepcopy(document["configuration"])
    selected = configuration["tasks"][0]
    configuration["tasks"] = [selected]
    configuration["full"].update(
        task_ids=[selected["id"]], repeats=1, prompt_variant_ids=[], failure_scenario_ids=[]
    )
    configuration["acceptance_criteria"] = ["AC1"]
    configuration["resources"]["episode_concurrency"] = 4
    configuration["appworld"]["max_interactions"] = 75
    partial = resolve_partial_profile(base, configuration)
    assert partial.base_profile_name == "appworld"
    assert partial.sha256 == base.sha256
    assert len(partial.plan_episodes()) == 1
    assert partial.profile.resources.episode_concurrency == 4
    assert partial.profile.appworld is not None
    assert partial.profile.appworld.max_interactions == 75
    assert load_profile("appworld", PROFILE_DIR).sha256 == base.sha256


def test_partial_appworld_configuration_remains_strict() -> None:
    base = load_profile("appworld", PROFILE_DIR)
    configuration = base.profile.model_dump(mode="json")
    configuration["appworld"].pop("max_interactions")
    with pytest.raises(InvalidRequestError, match="Invalid partial configuration"):
        resolve_partial_profile(base, configuration)


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(appworld=None),
        lambda data: data["tasks"][0].update(problem="task.json"),
        lambda data: data["tasks"][0].update(split=None),
        lambda data: data.update(benchmark="blocks_world"),
    ],
)
def test_invalid_benchmark_specific_configuration_is_rejected(change) -> None:
    data = load_profile("appworld", PROFILE_DIR).profile.model_dump(mode="json")
    change(data)
    with pytest.raises(ValueError):
        ValidationProfile.model_validate(data)
