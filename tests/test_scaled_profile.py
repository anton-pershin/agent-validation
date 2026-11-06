from __future__ import annotations

from collections import Counter
from pathlib import Path

from agent_validation.blocks_world import BlocksWorldEnvironment
from agent_validation.config import load_profile
from agent_validation.contracts import EpisodeRecord, ValidationScope
from agent_validation.metrics import CriterionEvaluator, MetricCalculator

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "config" / "profiles"


def solution(task_id: str) -> list[tuple[str, dict]]:
    plans = {
        "stack_ab": [("pick_up", {"block": "a"}), ("stack", {"block": "a", "other_block": "b"})],
        "unstack_ab": [
            ("unstack", {"block": "a", "other_block": "b"}),
            ("put_down", {"block": "a"}),
            ("pick_up", {"block": "a"}),
            ("put_down", {"block": "a"}),
        ],
        "restack_abc": [
            ("unstack", {"block": "a", "other_block": "b"}),
            ("put_down", {"block": "a"}),
            ("pick_up", {"block": "b"}),
            ("stack", {"block": "b", "other_block": "c"}),
        ],
        "stack_three": [
            ("pick_up", {"block": "b"}),
            ("stack", {"block": "b", "other_block": "c"}),
            ("pick_up", {"block": "a"}),
            ("stack", {"block": "a", "other_block": "b"}),
        ],
        "move_c": [("pick_up", {"block": "c"}), ("stack", {"block": "c", "other_block": "a"})],
        "stack_bc": [("pick_up", {"block": "b"}), ("stack", {"block": "b", "other_block": "c"})],
        "stack_ca": [("pick_up", {"block": "c"}), ("stack", {"block": "c", "other_block": "a"})],
        "stack_ba": [("pick_up", {"block": "b"}), ("stack", {"block": "b", "other_block": "a"})],
        "stack_cb": [("pick_up", {"block": "c"}), ("stack", {"block": "c", "other_block": "b"})],
        "stack_ac": [("pick_up", {"block": "a"}), ("stack", {"block": "a", "other_block": "c"})],
        "stack_ba_on_c": [
            ("pick_up", {"block": "a"}),
            ("stack", {"block": "a", "other_block": "c"}),
            ("pick_up", {"block": "b"}),
            ("stack", {"block": "b", "other_block": "a"}),
        ],
        "stack_ca_on_b": [
            ("pick_up", {"block": "a"}),
            ("stack", {"block": "a", "other_block": "b"}),
            ("pick_up", {"block": "c"}),
            ("stack", {"block": "c", "other_block": "a"}),
        ],
        "unstack_ba": [
            ("unstack", {"block": "b", "other_block": "a"}),
            ("put_down", {"block": "b"}),
            ("pick_up", {"block": "b"}),
            ("put_down", {"block": "b"}),
        ],
        "unstack_ca": [
            ("unstack", {"block": "c", "other_block": "a"}),
            ("put_down", {"block": "c"}),
            ("pick_up", {"block": "c"}),
            ("put_down", {"block": "c"}),
        ],
        "unstack_bc": [
            ("unstack", {"block": "b", "other_block": "c"}),
            ("put_down", {"block": "b"}),
            ("pick_up", {"block": "b"}),
            ("put_down", {"block": "b"}),
        ],
        "restack_bca": [
            ("unstack", {"block": "b", "other_block": "c"}),
            ("put_down", {"block": "b"}),
            ("pick_up", {"block": "c"}),
            ("stack", {"block": "c", "other_block": "a"}),
        ],
        "restack_cab": [
            ("unstack", {"block": "c", "other_block": "a"}),
            ("put_down", {"block": "c"}),
            ("pick_up", {"block": "a"}),
            ("stack", {"block": "a", "other_block": "b"}),
        ],
        "restack_bac": [
            ("unstack", {"block": "b", "other_block": "a"}),
            ("put_down", {"block": "b"}),
            ("pick_up", {"block": "a"}),
            ("stack", {"block": "a", "other_block": "c"}),
        ],
        "move_a_to_c": [("pick_up", {"block": "a"}), ("stack", {"block": "a", "other_block": "c"})],
        "move_b_to_a": [("pick_up", {"block": "b"}), ("stack", {"block": "b", "other_block": "a"})],
        "move_c_to_b": [("pick_up", {"block": "c"}), ("stack", {"block": "c", "other_block": "b"})],
        "tower_four": [
            ("pick_up", {"block": "c"}),
            ("stack", {"block": "c", "other_block": "d"}),
            ("pick_up", {"block": "b"}),
            ("stack", {"block": "b", "other_block": "c"}),
            ("pick_up", {"block": "a"}),
            ("stack", {"block": "a", "other_block": "b"}),
        ],
        "unstack_tower": [
            ("unstack", {"block": "a", "other_block": "b"}),
            ("put_down", {"block": "a"}),
            ("unstack", {"block": "b", "other_block": "c"}),
            ("put_down", {"block": "b"}),
            ("pick_up", {"block": "b"}),
            ("put_down", {"block": "b"}),
        ],
    }
    return plans[task_id]


def test_full_and_partial_population_counts() -> None:
    resolved = load_profile("default", PROFILE_DIR)
    full = resolved.plan_episodes(ValidationScope.FULL)
    partial = resolved.plan_episodes(ValidationScope.PARTIAL)
    assert len(full) == 840
    assert len(partial) == 42
    assert Counter(episode.condition_id for episode in full) == {
        "unperturbed": 200,
        "prompt/concise": 200,
        "prompt/formal": 200,
        "failure/pick_up_failure": 150,
        "failure/unstack_failure": 90,
    }
    assert {episode.repeat for episode in full} == set(range(1, 11))


def test_pick_up_failure_uses_tasks_that_require_pick_up() -> None:
    resolved = load_profile("default", PROFILE_DIR)
    scenarios = {scenario.id: scenario for scenario in resolved.profile.failure_scenarios}
    unstack_only = {"unstack_ab", "unstack_ba", "unstack_bc", "unstack_ca", "unstack_tower"}
    assert len(scenarios["pick_up_failure"].task_ids) == 15
    assert not unstack_only & set(scenarios["pick_up_failure"].task_ids)
    assert set(scenarios["pick_up_failure"].task_ids) & set(
        scenarios["unstack_failure"].task_ids
    ) == {"restack_abc", "restack_bca", "restack_cab", "restack_bac"}


def test_task_identity_solvability_and_fault_coverage() -> None:
    resolved = load_profile("default", PROFILE_DIR)
    identities = set()
    for task in resolved.profile.tasks:
        path = (resolved.path.parent / task.problem).resolve()
        environment = BlocksWorldEnvironment.from_pddl(path)
        identities.add((environment.problem.initial_state, environment.problem.goals))
        actions = solution(task.id)
        assert any(name == "pick_up" for name, _ in actions)
        for name, arguments in actions:
            result = environment.execute({"name": name, "arguments": arguments})
            assert result["status"] == "ok", (task.id, name, result)
        assert environment.goal_satisfied(), task.id
        assert len(actions) <= 50
        if task.id in next(
            scenario.task_ids
            for scenario in resolved.profile.failure_scenarios
            if scenario.id == "unstack_failure"
        ):
            assert any(name == "unstack" for name, _ in actions), task.id
    assert len(identities) == 20


def test_metrics_compute_over_all_full_episodes() -> None:
    resolved = load_profile("default", PROFILE_DIR)
    planned = resolved.plan_episodes(ValidationScope.FULL)
    records = [
        EpisodeRecord(
            episode_id=episode.episode_id,
            task_id=episode.task_id,
            condition_id=episode.condition_id,
            condition_type=episode.condition_type,
            repeat=episode.repeat,
            success=True,
            likely_incorrect=False,
            action_attempts=2,
            final_response_received=True,
            elapsed_seconds=10.0,
            completed=True,
            answer="complete",
            confidence=1.0,
            reason="scripted success",
            termination_reason="completed",
            fault_exposure=True if episode.fault_scenario_id else None,
        )
        for episode in planned
    ]
    calculator = MetricCalculator(resolved, ValidationScope.FULL, planned, records)
    computations = calculator.compute()
    assert all(computation.error_message is None for computation in computations.values())
    result = CriterionEvaluator(resolved, ValidationScope.FULL, computations).evaluate()
    assert {key: criterion.status for key, criterion in result.criteria.items()} == {
        f"AC{index}": "accepted" for index in range(1, 8)
    }
