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
    assert len(full) == 42
    assert len(partial) == 6
    assert len({episode.episode_id for episode in full}) == 42
    assert {episode.repeat for episode in partial} == {1, 2}
    assert {episode.condition_id for episode in partial} == {
        "unperturbed",
        "prompt/concise",
        "failure/pick_up_failure",
    }


def test_profile_rejects_unknown_references(test_profile) -> None:
    profile_dir, data = test_profile
    data["partial"]["task_ids"] = ["missing"]
    (profile_dir / "test.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ProfileError, match="Unknown references"):
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
