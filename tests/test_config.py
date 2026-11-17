from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from agent_validation.config import (
    complete_configuration_schema,
    load_partial_configuration,
    load_profile,
    resolve_partial_profile,
)
from agent_validation.errors import InvalidRequestError, ProfileError
from agent_validation.security import parse_solution_overrides, validate_commit

ROOT = Path(__file__).resolve().parents[1]


def test_default_registered_full_and_example_partial_populations() -> None:
    profile = load_profile("default", ROOT / "config" / "profiles")
    full = profile.plan_episodes()
    partial = resolve_partial_profile(
        profile,
        load_partial_configuration(ROOT / "config" / "examples" / "default_partial.yaml"),
    ).plan_episodes()
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


def test_complete_configuration_round_trip_and_base_immutability(test_profile) -> None:
    profile_dir, _ = test_profile
    base = load_profile("test", profile_dir)
    configuration = base.profile.model_dump(mode="json")
    resolved = resolve_partial_profile(base, deepcopy(configuration))
    assert resolved.profile.model_dump(mode="json") == configuration
    assert resolved.base_profile_name == "test"
    assert resolved.sha256 == base.sha256
    assert resolved.effective_sha256 == base.effective_sha256
    configuration["resources"]["episode_concurrency"] = 2
    changed = resolve_partial_profile(base, configuration)
    assert changed.effective_sha256 != base.effective_sha256
    assert load_profile("test", profile_dir).sha256 == base.sha256
    assert "partial" not in complete_configuration_schema()["properties"]


def test_partial_configuration_can_modify_effective_settings(test_profile) -> None:
    profile_dir, _ = test_profile
    base = load_profile("test", profile_dir)
    configuration = base.profile.model_dump(mode="json")
    configuration["full"] = {
        "task_ids": ["stack_ab"],
        "repeats": 1,
        "prompt_variant_ids": [],
        "failure_scenario_ids": [],
        "failure_task_ids": {},
    }
    configuration["acceptance_criteria"] = ["AC1"]
    configuration["criteria"]["AC1"]["min_success_rate"] = 0.5
    configuration["criteria"]["AC7"]["time_limit_seconds"] = 10
    configuration["resources"]["episode_concurrency"] = 2
    configuration["prompt_variants"][0]["instructions"] = "Modified diagnostic prompt"
    configuration["failure_scenarios"][0]["occurrence"] = 2
    resolved = resolve_partial_profile(base, configuration)
    assert len(resolved.plan_episodes()) == 1
    assert resolved.profile.acceptance_criteria == ["AC1"]
    assert resolved.profile.criteria.AC1.min_success_rate == 0.5
    assert resolved.profile.criteria.AC7.time_limit_seconds == 10
    assert resolved.profile.resources.episode_concurrency == 2
    assert resolved.profile.prompt_variants[0].instructions == "Modified diagnostic prompt"
    assert resolved.profile.failure_scenarios[0].occurrence == 2
    assert base.profile.full.repeats == 2
    assert base.profile.acceptance_criteria == [f"AC{index}" for index in range(1, 8)]
    unchanged = resolve_partial_profile(base, base.profile.model_dump(mode="json"))
    assert len(unchanged.plan_episodes()) == len(base.plan_episodes())


def test_profile_rejects_unknown_references(test_profile) -> None:
    profile_dir, data = test_profile
    data["full"]["task_ids"] = ["missing"]
    (profile_dir / "test.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ProfileError, match="Unknown references"):
        load_profile("test", profile_dir)


@pytest.mark.parametrize(
    "key",
    ["task_ids", "prompt_variant_ids", "failure_scenario_ids"],
)
def test_profile_rejects_duplicate_scope_references(test_profile, key: str) -> None:
    profile_dir, data = test_profile
    values = data["full"][key]
    data["full"][key] = [values[0], values[0]]
    (profile_dir / "test.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ProfileError, match="Duplicate"):
        load_profile("test", profile_dir)


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data["resources"].pop("episode_concurrency"),
        lambda data: data["resources"].update(unknown_field=1),
        lambda data: data["resources"].update(episode_concurrency=0),
        lambda data: data["full"].update(task_ids=["missing"]),
    ],
)
def test_partial_configuration_rejects_incomplete_or_invalid_values(
    test_profile, change
) -> None:
    profile_dir, _ = test_profile
    base = load_profile("test", profile_dir)
    configuration = base.profile.model_dump(mode="json")
    change(configuration)
    with pytest.raises(InvalidRequestError, match="Invalid partial configuration"):
        resolve_partial_profile(base, configuration)


def test_profile_rejects_path_traversal_name(test_profile) -> None:
    profile_dir, _ = test_profile
    with pytest.raises(InvalidRequestError):
        load_profile("../test", profile_dir)


def test_internal_spec_numbers_are_unique() -> None:
    specs = (ROOT / ".internal" / "specs").glob("*.md")
    numbers = [path.name.split("-", 1)[0] for path in specs]
    assert len(numbers) == len(set(numbers))


def test_commit_and_override_validation() -> None:
    assert validate_commit("a" * 40) == "a" * 40
    with pytest.raises(InvalidRequestError):
        validate_commit("abc123")
    with pytest.raises(InvalidRequestError):
        parse_solution_overrides("'unterminated")
    assert parse_solution_overrides("--model 'a model'") == ["--model", "a model"]
