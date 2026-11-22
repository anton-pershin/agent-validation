"""Small AppWorld test double used through the real worker subprocess protocol."""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
from typing import Any

__version__ = "0.1.3.post1"


class _Task:
    def __init__(self, task_id: str) -> None:
        self.instruction = f"Public instruction for {task_id}"
        self.supervisor = {
            "first_name": "Public",
            "last_name": "Supervisor",
            "email": "public@example.invalid",
            "phone_number": "000",
        }
        self.app_descriptions = {"public_app": "Public application description"}
        self.db_version = "other" if task_id == "data_mismatch" else "0.1.0"


class _Tracker:
    def __init__(self, success: bool) -> None:
        self.success = success

    def to_dict(self, stats_only: bool = False) -> dict[str, Any]:
        del stats_only
        return {"success": self.success, "private": "PRIVATE_EVALUATION"}

    def report(self, print_it: bool = True, colorize: bool = False) -> str:
        del print_it, colorize
        return "PRIVATE_EVALUATION_REPORT"


class _Supervisor:
    def __init__(self, world: AppWorld) -> None:
        self.world = world

    def complete_task(self, answer: Any = None) -> dict[str, Any]:
        self.world._completed = True
        return {"status": "completed", "answer": answer}


class _APIs:
    def __init__(self, world: AppWorld) -> None:
        self.supervisor = _Supervisor(world)


class AppWorld:
    def __init__(self, task_id: str, experiment_name: str, **settings: Any) -> None:
        del settings
        self.task = _Task(task_id)
        self.task_id = task_id
        self.experiment_name = experiment_name
        self._completed = False
        self._namespace: dict[str, Any] = {}
        self._namespace["apis"] = _APIs(self)
        self._namespace["requester"] = object()
        root = Path(os.environ["APPWORLD_ROOT"])
        self.output_directory = root / "experiments" / "outputs" / experiment_name
        self.output_directory.mkdir(parents=True, exist_ok=True)
        (self.output_directory / "task-id").write_text(task_id, encoding="utf-8")

    def execute(self, code: str) -> str:
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                exec(code, self._namespace)  # noqa: S102 - test-only fake runtime
        except Exception as exc:
            return f"Execution failed. Traceback:\n{exc}"
        return output.getvalue() or "Execution successful."

    def task_completed(self) -> bool:
        return self._completed

    def evaluate(self) -> _Tracker:
        if self.task_id == "eval_crash":
            raise RuntimeError("fake evaluator infrastructure failure")
        return _Tracker(success=self.task_id != "eval_fail")

    def close(self) -> None:
        (self.output_directory / "closed").write_text("true", encoding="utf-8")
