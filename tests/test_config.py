from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_validation.config import load_profile
from agent_validation.contracts import ValidationScope
from agent_validation.errors import InvalidRequestError, ProfileError
from agent_validation.security import parse_solution_overrides, validate_commit


def test_default_profile_populations() -> None:
    profile_dir = Path(__file__).resolve().parents[1] / "config" / "profiles"
    profile = load_profile("default", profile_dir)
    full = profile.plan_episodes(ValidationScope.FULL)
    partial = profile.plan_episodes(ValidationScope.PARTIAL)
    assert len(full) == 840
    assert len(partial) == 42
    assert len({episode.episode_id for episode in full}) == 840
    assert {episode.repeat for episode in full} == set(range(1, 11))
    assert {episode.condition_id for episode in partial} == {
        "unperturbed",
        "prompt/concise",
        "prompt/formal",
        "failure/pick_up_failure",
        "failure/unstack_failure",
    }


def test_profile_rejects_unknown_references(test_profile) -> None:
    profile_dir, data = test_profile
    data["partial"]["task_ids"] = ["missing"]
    (profile_dir / "test.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ProfileError, match="Unknown references"):
        load_profile("test", profile_dir)


@pytest.mark.parametrize(
    "path",
    [
        ("partial", "task_ids"),
        ("partial", "prompt_variant_ids"),
        ("partial", "failure_scenario_ids"),
    ],
)
def test_profile_rejects_duplicate_scope_references(test_profile, path) -> None:
    profile_dir, data = test_profile
    section, key = path
    values = data[section][key]
    data[section][key] = [values[0], values[0]]
    (profile_dir / "test.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ProfileError, match="Duplicate"):
        load_profile("test", profile_dir)


def test_profile_rejects_path_traversal_name(test_profile) -> None:
    profile_dir, _ = test_profile
    with pytest.raises(InvalidRequestError):
        load_profile("../test", profile_dir)


def test_commit_and_override_validation() -> None:
    assert validate_commit("a" * 40) == "a" * 40
    with pytest.raises(InvalidRequestError):
        validate_commit("abc123")
    with pytest.raises(InvalidRequestError):
        parse_solution_overrides("'unterminated")
    assert parse_solution_overrides("--model 'a model'") == ["--model", "a model"]
