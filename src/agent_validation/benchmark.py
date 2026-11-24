"""Benchmark-neutral episode sessions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from .blocks_world import TOOL_DESCRIPTIONS, BlocksWorldEnvironment
from .config import ResolvedProfile
from .contracts import PlannedEpisode


class BenchmarkSession(ABC):
    """One benchmark world for one planned episode."""

    task_input: str
    tools: list[dict[str, Any]]
    action_limit: int

    @abstractmethod
    def observation(self) -> dict[str, Any]:
        """Return the current public observation without executing an action."""

    @abstractmethod
    def execute(
        self,
        action: Any,
        injected_failure: str | None = None,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        """Execute one public action."""

    @abstractmethod
    def succeed(self, completed: bool) -> bool:
        """Independently score the final state after the solution response."""

    @abstractmethod
    def close(self) -> None:
        """Release benchmark resources."""

    def abort(self) -> None:
        """Release resources without adding a new failure during cancellation."""
        try:
            self.close()
        except Exception:
            pass

    def evidence(self) -> dict[str, Any]:
        """Return benchmark-specific evidence, if any."""
        return {}

    def worker_log(self) -> str:
        """Return bounded benchmark-worker standard error."""
        return ""


class BlocksWorldSession(BenchmarkSession):
    def __init__(self, resolved: ResolvedProfile, planned: PlannedEpisode) -> None:
        if planned.problem_path is None:
            raise ValueError("Blocks World episodes require a problem path")
        self.environment = BlocksWorldEnvironment.from_pddl(planned.problem_path)
        self.task_input = planned.task_input
        self.tools = TOOL_DESCRIPTIONS
        self.action_limit = resolved.profile.criteria.AC6.max_action_attempts

    def observation(self) -> dict[str, Any]:
        return self.environment.observation()

    def execute(
        self,
        action: Any,
        injected_failure: str | None = None,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        del deadline
        return self.environment.execute(action, injected_failure)

    def succeed(self, completed: bool) -> bool:
        return completed and self.environment.goal_satisfied()

    def close(self) -> None:
        return None


def create_benchmark_session(
    resolved: ResolvedProfile,
    planned: PlannedEpisode,
    *,
    run_id: str,
    on_create: Callable[[BenchmarkSession], None] | None = None,
) -> BenchmarkSession:
    if resolved.profile.benchmark == "blocks_world":
        session = BlocksWorldSession(resolved, planned)
        if on_create is not None:
            on_create(session)
        return session
    if resolved.profile.benchmark == "appworld":
        from .appworld_session import AppWorldSession

        return AppWorldSession(resolved, planned, run_id=run_id, on_create=on_create)
    raise ValueError(f"Unsupported benchmark: {resolved.profile.benchmark}")
