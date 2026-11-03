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
    injector.confirm(first)
    assert first["error"]["type"] == "transient_failure"
    assert environment.observation() == before
    assert injector.exposed
    assert injector.failure_for("pick_up") is None
    assert environment.execute(action)["status"] == "ok"


def test_fault_occurrence_must_match_schedule() -> None:
    scenario = FailureScenarioConfig(
        id="fault", action_name="pick_up", occurrence=2, task_ids=["stack_ab"]
    )
    injector = TransientFailureInjector(scenario)
    assert injector.failure_for("pick_up") is None
    assert injector.failure_for("pick_up") == scenario.error_message


def test_malformed_fault_target_and_catalog_arguments() -> None:
    environment = BlocksWorldEnvironment.from_pddl(PROBLEM)
    scenario = FailureScenarioConfig(id="fault", action_name="pick_up", task_ids=["stack_ab"])
    injector = TransientFailureInjector(scenario)
    injected = injector.failure_for("pick_up")
    result = environment.execute({"name": "pick_up", "arguments": None}, injected)
    injector.confirm(result)
    assert result["error"]["type"] == "malformed_action"
    assert injector.exposed is False
    catalog = environment.execute({"name": "get_action_catalog", "arguments": {"unexpected": True}})
    assert catalog["error"]["type"] == "malformed_action"


def test_hyphenated_object_names_are_consistent(tmp_path: Path) -> None:
    problem = tmp_path / "hyphen.pddl"
    problem.write_text(
        """(define (problem hyphen)
  (:domain blocks-world)
  (:objects a-b - block)
  (:init (on-table a-b) (clear a-b) (hand-empty))
  (:goal (and (on-table a-b))))
"""
    )
    environment = BlocksWorldEnvironment.from_pddl(problem)
    result = environment.execute({"name": "pick_up", "arguments": {"block": "a-b"}})
    assert result["status"] == "ok"
    assert "(holding a-b)" in result["observation"]["state"]
