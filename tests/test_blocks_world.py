from __future__ import annotations

from pathlib import Path

from agent_validation.blocks_world import BlocksWorldEnvironment, TransientFailureInjector
from agent_validation.config import FailureScenarioConfig

PROBLEM = (
    Path(__file__).resolve().parents[1] / "config" / "blocks_world" / "problems" / "stack_ab.pddl"
)


def test_actions_and_independent_goal_check() -> None:
    environment = BlocksWorldEnvironment.from_pddl(PROBLEM)
    result = environment.execute({"name": "pick_up", "arguments": {"block": "a"}})
    assert result["status"] == "ok"
    assert not environment.goal_satisfied()
    result = environment.execute({"name": "stack", "arguments": {"block": "a", "other_block": "b"}})
    assert result["status"] == "ok"
    assert environment.goal_satisfied()
    assert "goal_achieved" not in result["observation"]


def test_catalog_unknown_malformed_and_precondition_failures() -> None:
    environment = BlocksWorldEnvironment.from_pddl(PROBLEM)
    catalog = environment.execute({"name": "get_action_catalog", "arguments": {}})
    assert len(catalog["observation"]["actions"]) == 5
    assert (
        environment.execute({"name": "fly", "arguments": {}})["error"]["type"] == "unknown_action"
    )
    assert environment.execute(None)["error"]["type"] == "malformed_action"
    before = environment.observation()
    failed = environment.execute({"name": "put_down", "arguments": {"block": "a"}})
    assert failed["error"]["type"] == "precondition_failed"
    assert environment.observation() == before


def test_transient_failure_does_not_change_state_and_occurs_once() -> None:
    environment = BlocksWorldEnvironment.from_pddl(PROBLEM)
    scenario = FailureScenarioConfig(id="fault", action_name="pick_up", task_ids=["stack_ab"])
    injector = TransientFailureInjector(scenario)
    before = environment.observation()
    action = {"name": "pick_up", "arguments": {"block": "a"}}
    first = environment.execute(action, injector.failure_for("pick_up"))
    assert first["error"]["type"] == "transient_failure"
    assert environment.observation() == before
    assert injector.exposed
    assert injector.failure_for("pick_up") is None
    assert environment.execute(action)["status"] == "ok"
