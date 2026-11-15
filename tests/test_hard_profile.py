from __future__ import annotations

from collections import Counter, deque
from functools import lru_cache
from pathlib import Path
from statistics import mean
from typing import Any

from agent_validation.blocks_world import BlocksWorldEnvironment, BlocksWorldProblem
from agent_validation.config import ResolvedProfile, load_profile
from agent_validation.contracts import EpisodeRecord, ValidationScope
from agent_validation.metrics import CriterionEvaluator, MetricCalculator

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "config" / "profiles"
TABLE = "@table"
HELD = "@held"
Action = tuple[str, ...]
State = tuple[str, ...]


@lru_cache
def hard_profile() -> ResolvedProfile:
    return load_profile("blocksworld_hard", PROFILE_DIR)


def problem_path(resolved: ResolvedProfile, task_id: str) -> Path:
    task = next(task for task in resolved.profile.tasks if task.id == task_id)
    return (resolved.path.parent / task.problem).resolve()


def abstract_problem(problem: BlocksWorldProblem) -> tuple[tuple[str, ...], State, dict[int, str]]:
    blocks = tuple(
        sorted(
            {
                argument
                for predicate in problem.initial_state | problem.goals
                if predicate[0] in {"on", "on_table", "clear", "holding"}
                for argument in predicate[1:]
            }
        )
    )
    indices = {block: index for index, block in enumerate(blocks)}
    initial: list[str | None] = [None] * len(blocks)
    for predicate in problem.initial_state:
        if predicate[0] == "on_table":
            initial[indices[predicate[1]]] = TABLE
        elif predicate[0] == "on":
            initial[indices[predicate[1]]] = predicate[2]
        elif predicate[0] == "holding":
            initial[indices[predicate[1]]] = HELD
    assert all(location is not None for location in initial)
    goals: dict[int, str] = {}
    for predicate in problem.goals:
        assert predicate[0] in {"on", "on_table"}
        index = indices[predicate[1]]
        goals[index] = TABLE if predicate[0] == "on_table" else predicate[2]
    assert len(goals) == len(blocks)
    return blocks, tuple(location for location in initial if location is not None), goals


def next_states(blocks: tuple[str, ...], state: State) -> list[tuple[Action, State]]:
    locations = dict(zip(blocks, state, strict=True))
    # A block is clear when no block is directly on it. Exclude the held block.
    clear = {
        block
        for block in blocks
        if locations[block] != HELD
        and not any(locations[other] == block for other in blocks)
    }
    held = next((block for block in blocks if locations[block] == HELD), None)
    successors: list[tuple[Action, State]] = []

    def moved(block: str, destination: str) -> State:
        return tuple(destination if item == block else locations[item] for item in blocks)

    if held is not None:
        successors.append((("put_down", held), moved(held, TABLE)))
        for other in blocks:
            if other in clear:
                successors.append((("stack", held, other), moved(held, other)))
        return successors
    for block in blocks:
        if block not in clear:
            continue
        if locations[block] == TABLE:
            successors.append((("pick_up", block), moved(block, HELD)))
        else:
            successors.append((("unstack", block, locations[block]), moved(block, HELD)))
    return successors


def shortest_plan(problem: BlocksWorldProblem) -> list[Action]:
    blocks, initial, goals = abstract_problem(problem)
    queue = deque([initial])
    previous: dict[State, tuple[State | None, Action | None]] = {initial: (None, None)}
    while queue:
        state = queue.popleft()
        if all(state[index] == destination for index, destination in goals.items()):
            plan: list[Action] = []
            while previous[state][0] is not None:
                parent, action = previous[state]
                assert parent is not None and action is not None
                plan.append(action)
                state = parent
            return list(reversed(plan))
        for action, successor in next_states(blocks, state):
            if successor not in previous:
                previous[successor] = (state, action)
                queue.append(successor)
    raise AssertionError("Task has no successful plan")


@lru_cache
def shortest_plans() -> dict[str, list[Action]]:
    resolved = hard_profile()
    return {
        task.id: shortest_plan(
            BlocksWorldEnvironment.from_pddl(problem_path(resolved, task.id)).problem
        )
        for task in resolved.profile.tasks
    }


def public_action(action: Action) -> dict[str, Any]:
    name = action[0]
    if name in {"pick_up", "put_down"}:
        arguments = {"block": action[1]}
    else:
        arguments = {"block": action[1], "other_block": action[2]}
    return {"name": name, "arguments": arguments}


def test_hard_full_and_partial_population_counts() -> None:
    resolved = hard_profile()
    full = resolved.plan_episodes(ValidationScope.FULL)
    partial = resolved.plan_episodes(ValidationScope.PARTIAL)
    assert len(full) == 890
    assert len(partial) == 42
    assert Counter(episode.condition_id for episode in full) == {
        "unperturbed": 200,
        "prompt/concise": 200,
        "prompt/formal": 200,
        "failure/pick_up_failure": 200,
        "failure/unstack_failure": 90,
    }
    assert Counter(episode.condition_id for episode in partial) == {
        "unperturbed": 10,
        "prompt/concise": 10,
        "prompt/formal": 10,
        "failure/pick_up_failure": 8,
        "failure/unstack_failure": 4,
    }
    assert {episode.repeat for episode in full} == set(range(1, 11))
    assert {episode.repeat for episode in partial} == {1, 2}


def test_shortest_plan_lengths_have_mean_ten() -> None:
    plans = shortest_plans()
    lengths = {task_id: len(plan) for task_id, plan in plans.items()}
    assert Counter(lengths.values()) == {8: 6, 10: 8, 12: 6}
    assert mean(lengths.values()) == 10


def test_task_identity_solvability_and_fault_coverage() -> None:
    resolved = hard_profile()
    plans = shortest_plans()
    unstack_tasks = set(
        next(
            scenario.task_ids
            for scenario in resolved.profile.failure_scenarios
            if scenario.id == "unstack_failure"
        )
    )
    assert len(unstack_tasks) == 9
    identities = set()
    for task in resolved.profile.tasks:
        environment = BlocksWorldEnvironment.from_pddl(problem_path(resolved, task.id))
        identities.add((environment.problem.initial_state, environment.problem.goals))
        actions = plans[task.id]
        assert any(action[0] == "pick_up" for action in actions)
        if task.id in unstack_tasks:
            assert any(action[0] == "unstack" for action in actions), task.id
        for action in actions:
            result = environment.execute(public_action(action))
            assert result["status"] == "ok", (task.id, action, result)
        assert environment.goal_satisfied(), task.id
        assert len(actions) <= resolved.profile.criteria.AC6.max_action_attempts
    assert len(identities) == 20


def test_metrics_compute_over_all_hard_full_episodes() -> None:
    resolved = hard_profile()
    planned = resolved.plan_episodes(ValidationScope.FULL)
    plans = shortest_plans()
    records = [
        EpisodeRecord(
            episode_id=episode.episode_id,
            task_id=episode.task_id,
            condition_id=episode.condition_id,
            condition_type=episode.condition_type,
            repeat=episode.repeat,
            success=True,
            likely_incorrect=False,
            action_attempts=len(plans[episode.task_id]),
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


def test_default_population_is_unchanged_and_hard_profile_is_documented() -> None:
    default = load_profile("default", PROFILE_DIR)
    assert len(default.plan_episodes(ValidationScope.FULL)) == 890
    assert len(default.plan_episodes(ValidationScope.PARTIAL)) == 42
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "blocksworld_hard" in readme
    assert "optimal" in readme
