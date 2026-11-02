"""A small PDDL Blocks World interpreter with independent goal checking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import FailureScenarioConfig
from .errors import BenchmarkError

Predicate = tuple[str, ...]

_TOKEN_RE = re.compile(r"[()]|[^\s()]+")


TOOL_DESCRIPTIONS: list[dict[str, Any]] = [
    {
        "name": "get_action_catalog",
        "description": "Return the available Blocks World actions and their argument schemas.",
        "arguments_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "pick_up",
        "description": "Pick up a clear block from the table with an empty hand.",
        "arguments_schema": {
            "type": "object",
            "properties": {"block": {"type": "string"}},
            "required": ["block"],
            "additionalProperties": False,
        },
    },
    {
        "name": "put_down",
        "description": "Put the held block on the table.",
        "arguments_schema": {
            "type": "object",
            "properties": {"block": {"type": "string"}},
            "required": ["block"],
            "additionalProperties": False,
        },
    },
    {
        "name": "stack",
        "description": "Stack the held block on a clear destination block.",
        "arguments_schema": {
            "type": "object",
            "properties": {
                "block": {"type": "string"},
                "other_block": {"type": "string"},
            },
            "required": ["block", "other_block"],
            "additionalProperties": False,
        },
    },
    {
        "name": "unstack",
        "description": "Unstack a clear block from another block with an empty hand.",
        "arguments_schema": {
            "type": "object",
            "properties": {
                "block": {"type": "string"},
                "other_block": {"type": "string"},
            },
            "required": ["block", "other_block"],
            "additionalProperties": False,
        },
    },
]


@dataclass(frozen=True)
class BlocksWorldProblem:
    initial_state: frozenset[Predicate]
    goals: frozenset[Predicate]


class TransientFailureInjector:
    def __init__(self, scenario: FailureScenarioConfig | None) -> None:
        self.scenario = scenario
        self.matching_actions = 0
        self.exposed = False

    def failure_for(self, action_name: str) -> str | None:
        if self.scenario is None or self.exposed:
            return None
        normalized = action_name.replace("-", "_")
        expected = self.scenario.action_name.replace("-", "_")
        if normalized != expected:
            return None
        self.matching_actions += 1
        if self.matching_actions != self.scenario.occurrence:
            return None
        self.exposed = True
        return self.scenario.error_message


class BlocksWorldEnvironment:
    def __init__(self, problem: BlocksWorldProblem) -> None:
        self.problem = problem
        self._state = set(problem.initial_state)

    @classmethod
    def from_pddl(cls, path: str | Path) -> BlocksWorldEnvironment:
        return cls(parse_problem(Path(path)))

    def observation(self) -> dict[str, Any]:
        return {"state": [_format_predicate(predicate) for predicate in sorted(self._state)]}

    def goal_satisfied(self) -> bool:
        return self.problem.goals.issubset(self._state)

    def execute(self, action: Any, injected_failure: str | None = None) -> dict[str, Any]:
        if not isinstance(action, dict):
            return self._error("malformed_action", "Action must be a JSON object")
        name = action.get("name")
        arguments = action.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return self._error(
                "malformed_action", "Action requires a string name and an arguments object"
            )
        normalized = name.replace("-", "_")
        if normalized == "get_action_catalog":
            if injected_failure is not None:
                return self._error("transient_failure", injected_failure)
            return {
                "status": "ok",
                "observation": {
                    "actions": TOOL_DESCRIPTIONS,
                    **self.observation(),
                },
                "error": None,
            }
        if normalized not in {"pick_up", "put_down", "stack", "unstack"}:
            return self._error("unknown_action", f"Unknown action: {name}")
        if injected_failure is not None:
            return self._error("transient_failure", injected_failure)
        try:
            if normalized == "pick_up":
                self._pick_up(arguments)
            elif normalized == "put_down":
                self._put_down(arguments)
            elif normalized == "stack":
                self._stack(arguments)
            elif normalized == "unstack":
                self._unstack(arguments)
        except ActionPreconditionError as exc:
            return self._error("precondition_failed", str(exc))
        except ActionArgumentError as exc:
            return self._error("malformed_action", str(exc))
        return {
            "status": "ok",
            "observation": {"action": normalized, **self.observation()},
            "error": None,
        }

    def _pick_up(self, arguments: dict[str, Any]) -> None:
        block = _one_block(arguments)
        self._require(
            [("clear", block), ("on_table", block), ("hand_empty",)],
            f"Cannot pick up {block}: preconditions are not satisfied",
        )
        self._state.discard(("on_table", block))
        self._state.discard(("clear", block))
        self._state.discard(("hand_empty",))
        self._state.add(("holding", block))

    def _put_down(self, arguments: dict[str, Any]) -> None:
        block = _one_block(arguments)
        self._require(
            [("holding", block)],
            f"Cannot put down {block}: the hand does not hold it",
        )
        self._state.discard(("holding", block))
        self._state.update({("on_table", block), ("clear", block), ("hand_empty",)})

    def _stack(self, arguments: dict[str, Any]) -> None:
        block, other = _two_blocks(arguments)
        self._require(
            [("holding", block), ("clear", other)],
            f"Cannot stack {block} on {other}: preconditions are not satisfied",
        )
        self._state.discard(("holding", block))
        self._state.discard(("clear", other))
        self._state.update({("on", block, other), ("clear", block), ("hand_empty",)})

    def _unstack(self, arguments: dict[str, Any]) -> None:
        block, other = _two_blocks(arguments)
        self._require(
            [("on", block, other), ("clear", block), ("hand_empty",)],
            f"Cannot unstack {block} from {other}: preconditions are not satisfied",
        )
        self._state.discard(("on", block, other))
        self._state.discard(("clear", block))
        self._state.discard(("hand_empty",))
        self._state.update({("holding", block), ("clear", other)})

    def _require(self, predicates: list[Predicate], message: str) -> None:
        if any(predicate not in self._state for predicate in predicates):
            raise ActionPreconditionError(message)

    def _error(self, error_type: str, message: str) -> dict[str, Any]:
        return {
            "status": "error",
            "observation": self.observation(),
            "error": {"type": error_type, "message": message},
        }


class ActionPreconditionError(Exception):
    pass


class ActionArgumentError(Exception):
    pass


def _one_block(arguments: dict[str, Any]) -> str:
    if set(arguments) != {"block"} or not _valid_block(arguments.get("block")):
        raise ActionArgumentError("Action requires only a valid block argument")
    return str(arguments["block"]).lower()


def _two_blocks(arguments: dict[str, Any]) -> tuple[str, str]:
    if set(arguments) != {"block", "other_block"}:
        raise ActionArgumentError("Action requires block and other_block arguments only")
    block = arguments.get("block")
    other = arguments.get("other_block")
    if not _valid_block(block) or not _valid_block(other) or block == other:
        raise ActionArgumentError("Block arguments must be distinct valid identifiers")
    return str(block).lower(), str(other).lower()


def _valid_block(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", value))


def parse_problem(path: Path) -> BlocksWorldProblem:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchmarkError(f"Cannot read PDDL problem: {path}") from exc
    try:
        expression = _parse_sexpr(_tokens(text))
        sections = {
            section[0]: section
            for section in expression[1:]
            if isinstance(section, list) and section and isinstance(section[0], str)
        }
        initial = frozenset(_predicate(predicate) for predicate in sections[":init"][1:])
        goal_expression = sections[":goal"][1]
        goals = (
            goal_expression[1:]
            if isinstance(goal_expression, list) and goal_expression and goal_expression[0] == "and"
            else [goal_expression]
        )
        if not goals:
            raise BenchmarkError(f"PDDL goal must not be empty: {path}")
        return BlocksWorldProblem(
            initial_state=initial,
            goals=frozenset(_predicate(goal) for goal in goals),
        )
    except BenchmarkError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise BenchmarkError(f"Unsupported or invalid PDDL problem {path}: {exc}") from exc


def _tokens(text: str) -> list[str]:
    without_comments = re.sub(r";[^\n]*", "", text)
    return [token.lower() for token in _TOKEN_RE.findall(without_comments)]


def _parse_sexpr(tokens: list[str]) -> list[Any]:
    if not tokens or tokens.pop(0) != "(":
        raise ValueError("Expected an opening parenthesis")
    expression: list[Any] = []
    while tokens:
        token = tokens.pop(0)
        if token == ")":
            return expression
        if token == "(":
            tokens.insert(0, token)
            expression.append(_parse_sexpr(tokens))
        else:
            expression.append(token)
    raise ValueError("Unclosed parenthesis")


def _predicate(expression: Any) -> Predicate:
    if not isinstance(expression, list) or not expression:
        raise ValueError("Predicate must be a nonempty list")
    if not all(isinstance(part, str) for part in expression):
        raise ValueError("Predicate arguments must be symbols")
    return tuple(part.replace("-", "_") for part in expression)


def _format_predicate(predicate: Predicate) -> str:
    return "(" + " ".join(part.replace("_", "-") for part in predicate) + ")"
