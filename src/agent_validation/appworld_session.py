"""AppWorld benchmark session backed by isolated stdio workers."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .benchmark import BenchmarkSession
from .config import ResolvedProfile
from .contracts import PlannedEpisode
from .errors import (
    BenchmarkError,
    SolutionExitedError,
    SolutionOutputLimitError,
    SolutionProtocolError,
    SolutionTimeoutError,
)
from .protocol import SolutionProcess

APPWORLD_TOOL_DESCRIPTIONS: list[dict[str, Any]] = [
    {
        "name": "execute_code",
        "description": (
            "Execute Python in the stateful AppWorld shell. Use the available apis and "
            "requester objects. Print values that must be observed. Call "
            "apis.supervisor.complete_task(...) only when the task is complete."
        ),
        "arguments_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "minLength": 1}},
            "required": ["code"],
            "additionalProperties": False,
        },
    }
]


class AppWorldSession(BenchmarkSession):
    def __init__(
        self,
        resolved: ResolvedProfile,
        planned: PlannedEpisode,
        *,
        run_id: str,
        on_create: Callable[[BenchmarkSession], None] | None = None,
    ) -> None:
        self.resolved = resolved
        self.planned = planned
        self.settings = resolved.profile.appworld
        if self.settings is None:
            raise BenchmarkError("AppWorld profile requires its appworld configuration")
        self.action_limit = self.settings.max_interactions
        self.tools = APPWORLD_TOOL_DESCRIPTIONS
        self.task_input = ""
        self.process: SolutionProcess | None = None
        self.evaluation_process: SolutionProcess | None = None
        self.output_directory: Path | None = None
        self._last_observation: dict[str, Any] = {}
        self._closed = False
        self._worker_closed = False
        self.experiment_name = f"validation-{run_id}-{planned.episode_id}"
        self._evidence: dict[str, Any] = {
            "benchmark": "appworld",
            "task_id": planned.task_id,
            "configured": self.settings.model_dump(mode="json"),
            "experiment_name": self.experiment_name,
            "execution_count": 0,
            "success": None,
            "resources": None,
            "resources_final": False,
            "sandbox": "landlock",
        }
        if on_create is not None:
            on_create(self)
        try:
            python = os.environ.get("AGENT_VALIDATION_APPWORLD_PYTHON")
            root_value = os.environ.get("AGENT_VALIDATION_APPWORLD_ROOT")
            if not python or not root_value:
                raise BenchmarkError(
                    "AppWorld requires AGENT_VALIDATION_APPWORLD_PYTHON and "
                    "AGENT_VALIDATION_APPWORLD_ROOT"
                )
            self.python = Path(os.path.abspath(os.path.expanduser(python)))
            self.root = Path(root_value).resolve()
            if not self.python.is_file():
                raise BenchmarkError(f"AppWorld Python does not exist: {self.python}")
            if not (self.root / "data" / "datasets").is_dir():
                raise BenchmarkError(f"AppWorld data is not provisioned under {self.root}")
            self.output_directory = (
                self.root / "experiments" / "outputs" / self.experiment_name
            )
            worker = Path(__file__).resolve().with_name("appworld_worker.py")
            resources = resolved.profile.resources
            self.process = SolutionProcess(
                [str(self.python), str(worker)],
                self.root,
                self._environment(self.root),
                max_output_bytes=resources.max_output_bytes,
                max_retained_log_bytes=resources.max_retained_log_bytes,
                shutdown_seconds=resources.process_shutdown_seconds,
            )
            self.process.start()
            response = self._request(
                "initialize",
                {
                    "type": "initialize",
                    "task_id": planned.task_id,
                    "experiment_name": self.experiment_name,
                    "settings": {
                        "max_interactions": self.settings.max_interactions,
                        "max_api_calls_per_interaction": (
                            self.settings.max_api_calls_per_interaction
                        ),
                        "execution_timeout_seconds": self.settings.execution_timeout_seconds,
                        "random_seed": resolved.profile.random_seed,
                    },
                },
                "initialized",
                timeout_seconds=self.settings.startup_timeout_seconds,
            )
            runtime = response["runtime"]
            self._evidence["runtime"] = runtime
            if runtime.get("package_version") != self.settings.package_version:
                raise BenchmarkError(
                    "AppWorld runtime version does not match the registered profile: "
                    f"{runtime.get('package_version')}"
                )
            if runtime.get("data_version") != self.settings.data_version:
                raise BenchmarkError(
                    "AppWorld data version does not match the registered profile: "
                    f"{runtime.get('data_version')}"
                )
            if type(runtime.get("landlock_abi")) is not int or runtime["landlock_abi"] < 1:
                raise BenchmarkError("AppWorld worker did not enforce Landlock privacy isolation")
            self.task_input = json.dumps(response["task"], ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            self._evidence["startup_error"] = str(exc)
            try:
                self.abort()
            except Exception:
                pass
            if isinstance(exc, BenchmarkError):
                raise
            raise BenchmarkError(f"Cannot initialize AppWorld worker: {exc}") from exc

    def observation(self) -> dict[str, Any]:
        return self._last_observation

    def execute(
        self,
        action: Any,
        injected_failure: str | None = None,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        error = self._validate_action(action)
        if error is not None:
            return self._error(error[0], error[1])
        if injected_failure is not None:
            return self._error("transient_failure", injected_failure)
        code = action["arguments"]["code"]
        timeout_seconds = float(self.settings.execution_timeout_seconds + 5)
        timeout_is_solution = False
        if deadline is not None:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= timeout_seconds:
                timeout_seconds = remaining
                timeout_is_solution = True
        response = self._request(
            "execute",
            {"type": "execute", "code": code},
            "execution",
            timeout_seconds=timeout_seconds,
            solution_controlled=True,
            timeout_is_solution=timeout_is_solution,
        )
        output = response["output"]
        self._evidence["execution_count"] += 1
        self._last_observation = {"output": output}
        if output.startswith("Execution failed.") or output == "No code available to execute.":
            return self._error("execution_failed", output)
        return {"status": "ok", "observation": self._last_observation, "error": None}

    def succeed(self, completed: bool) -> bool:
        if not completed:
            self._evidence["success"] = False
            return False
        completion = self._request(
            "task_completed",
            {"type": "task_completed"},
            "task_completion",
            timeout_seconds=self.settings.shutdown_timeout_seconds,
        )
        task_completed = bool(completion["task_completed"])
        self._evidence["task_completed"] = task_completed
        self._request(
            "close",
            {"type": "close"},
            "closed",
            timeout_seconds=self.settings.shutdown_timeout_seconds,
        )
        self._worker_closed = True
        self._evidence["resources_final"] = True
        response = self._run_evaluator()
        evaluation_success = bool(response["evaluation_success"])
        self._evidence.update(
            {
                "evaluation_success": evaluation_success,
                "evaluation": response["evaluation"],
                "evaluation_report": response["report"],
                "success": bool(task_completed and evaluation_success),
            }
        )
        return bool(task_completed and evaluation_success)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[str] = []
        if self.process is not None and not self._worker_closed:
            try:
                self._request(
                    "close",
                    {"type": "close"},
                    "closed",
                    timeout_seconds=self.settings.shutdown_timeout_seconds,
                )
                self._worker_closed = True
                self._evidence["resources_final"] = True
            except Exception as exc:
                errors.append(str(exc))
        errors.extend(self._close_processes())
        self._remove_output(errors)
        self._finish_cleanup(errors, "cleanup")

    def abort(self) -> None:
        self._closed = True
        errors = self._close_processes()
        self._remove_output(errors)
        self._finish_cleanup(errors, "abort")

    def evidence(self) -> dict[str, Any]:
        return self._evidence

    def worker_log(self) -> str:
        logs = []
        if self.process is not None:
            logs.append(self.process.stderr_text())
        if self.evaluation_process is not None:
            logs.append(self.evaluation_process.stderr_text())
        return "\n".join(log for log in logs if log)

    def _request(
        self,
        stage: str,
        message: dict[str, Any],
        expected_type: str,
        *,
        timeout_seconds: float,
        solution_controlled: bool = False,
        timeout_is_solution: bool = False,
    ) -> dict[str, Any]:
        if self.process is None:
            raise BenchmarkError("AppWorld worker was not created")
        if timeout_seconds <= 0:
            if timeout_is_solution:
                raise SolutionTimeoutError("Solution exceeded the episode time limit")
            raise BenchmarkError(f"AppWorld worker {stage} deadline expired")
        message["protocol_version"] = 1
        deadline = time.monotonic() + timeout_seconds
        try:
            self.process.send(message, deadline)
            response = self.process.receive(deadline)
        except SolutionTimeoutError:
            if timeout_is_solution:
                raise
            raise BenchmarkError(f"AppWorld worker timed out during {stage}") from None
        except SolutionOutputLimitError:
            if solution_controlled:
                raise
            raise BenchmarkError(f"AppWorld worker output limit exceeded during {stage}") from None
        except (SolutionProtocolError, SolutionExitedError, OSError, ValueError) as exc:
            raise BenchmarkError(f"AppWorld worker failed during {stage}: {exc}") from exc
        if isinstance(response.get("resources"), dict):
            self._evidence["resources"] = response["resources"]
        return self._validate_response(response, expected_type, stage)

    def _run_evaluator(self) -> dict[str, Any]:
        worker = Path(__file__).resolve().with_name("appworld_worker.py")
        resources = self.resolved.profile.resources
        process = SolutionProcess(
            [
                str(self.python),
                str(worker),
                "--evaluate-task",
                self.planned.task_id,
                self.experiment_name,
            ],
            self.root,
            self._environment(self.root),
            max_output_bytes=resources.max_output_bytes,
            max_retained_log_bytes=resources.max_retained_log_bytes,
            shutdown_seconds=resources.process_shutdown_seconds,
        )
        self.evaluation_process = process
        response: dict[str, Any] | None = None
        failure: Exception | None = None
        try:
            process.start()
            response = process.receive(time.monotonic() + self.settings.evaluation_timeout_seconds)
            if isinstance(response.get("resources"), dict):
                self._evidence["evaluation_resources"] = response["resources"]
            response = self._validate_response(response, "evaluation", "evaluate")
        except Exception as exc:
            failure = BenchmarkError(f"AppWorld evaluator failed: {exc}")
        cleanup_error = process.cleanup_error
        try:
            process.close()
            cleanup_error = process.cleanup_error
        except Exception as exc:
            cleanup_error = str(exc)
        if cleanup_error:
            if failure is None:
                failure = BenchmarkError(f"AppWorld evaluator cleanup failed: {cleanup_error}")
            else:
                failure = BenchmarkError(f"{failure}; {cleanup_error}")
        if failure is not None:
            raise failure
        assert response is not None
        return response

    @staticmethod
    def _validate_response(
        response: dict[str, Any], expected_type: str, stage: str
    ) -> dict[str, Any]:
        version = response.get("protocol_version")
        if type(version) is not int or version != 1:
            raise BenchmarkError("AppWorld worker returned an invalid protocol version")
        if response.get("type") == "error":
            raise BenchmarkError(
                f"AppWorld worker {response.get('stage', stage)} failed: "
                f"{response.get('message', 'unknown error')}"
            )
        if response.get("type") != expected_type:
            raise BenchmarkError(
                f"AppWorld worker returned {response.get('type')} instead of {expected_type}"
            )
        return response

    def _environment(self, root: Path) -> dict[str, str]:
        names = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR")
        environment = {name: os.environ[name] for name in names if name in os.environ}
        environment.update(
            {
                "APPWORLD_ROOT": str(root),
                "APPWORLD_CACHE": "/tmp/appworld-cache",
                "HOME": "/tmp",
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return environment

    def _close_processes(self) -> list[str]:
        errors: list[str] = []
        for label, process in (
            ("worker", self.process),
            ("evaluator", self.evaluation_process),
        ):
            if process is None:
                continue
            try:
                process.close()
                if process.cleanup_error:
                    errors.append(f"AppWorld {label}: {process.cleanup_error}")
            except Exception as exc:
                errors.append(f"AppWorld {label}: {exc}")
        return errors

    def _remove_output(self, errors: list[str]) -> None:
        if self.output_directory is not None:
            try:
                shutil.rmtree(self.output_directory)
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f"Cannot remove AppWorld output: {exc}")
        self._evidence["output_removed"] = (
            self.output_directory is None or not self.output_directory.exists()
        )

    def _finish_cleanup(self, errors: list[str], operation: str) -> None:
        if errors:
            self._evidence["cleanup_error"] = "; ".join(errors)
            raise BenchmarkError(f"AppWorld worker {operation} failed: " + "; ".join(errors))

    @staticmethod
    def _validate_action(action: Any) -> tuple[str, str] | None:
        if not isinstance(action, dict):
            return "malformed_action", "Action must be a JSON object"
        name = action.get("name")
        arguments = action.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return "malformed_action", "Action requires a string name and an arguments object"
        if name != "execute_code":
            return "unknown_action", f"Unknown action: {name}"
        if set(arguments) != {"code"} or not isinstance(arguments.get("code"), str):
            return "malformed_action", "execute_code requires only a string code argument"
        if not arguments["code"].strip():
            return "malformed_action", "execute_code code must not be empty"
        return None

    def _error(self, error_type: str, message: str) -> dict[str, Any]:
        return {
            "status": "error",
            "observation": self.observation(),
            "error": {"type": error_type, "message": message},
        }
