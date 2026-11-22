"""AppWorld benchmark session backed by an isolated stdio worker."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .benchmark import BenchmarkSession
from .config import ResolvedProfile
from .contracts import PlannedEpisode
from .errors import BenchmarkError
from .protocol import SolutionProcess
from .security import provisioning_environment

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
    def __init__(self, resolved: ResolvedProfile, planned: PlannedEpisode, *, run_id: str) -> None:
        self.resolved = resolved
        self.planned = planned
        self.settings = resolved.profile.appworld
        if self.settings is None:
            raise BenchmarkError("AppWorld profile requires its appworld configuration")
        self.action_limit = self.settings.max_interactions
        self.tools = APPWORLD_TOOL_DESCRIPTIONS
        self._last_observation: dict[str, Any] = {}
        self._closed = False
        self._worker_closed = False
        self._evidence: dict[str, Any] = {
            "benchmark": "appworld",
            "task_id": planned.task_id,
            "configured": self.settings.model_dump(mode="json"),
            "execution_count": 0,
            "success": None,
        }
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
        self.experiment_name = f"validation-{run_id}-{planned.episode_id}"
        self.output_directory = (
            self.root / "experiments" / "outputs" / self.experiment_name
        )
        self._evidence["experiment_name"] = self.experiment_name
        worker = Path(__file__).resolve().with_name("appworld_worker.py")
        environment = provisioning_environment()
        environment.update(
            {
                "APPWORLD_ROOT": str(self.root),
                "APPWORLD_CACHE": str(self.root / ".appworld-cache"),
                "PYTHONUNBUFFERED": "1",
            }
        )
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        resources = resolved.profile.resources
        self.process = SolutionProcess(
            [str(self.python), str(worker)],
            self.root,
            environment,
            max_output_bytes=resources.max_output_bytes,
            max_retained_log_bytes=resources.max_retained_log_bytes,
            shutdown_seconds=resources.process_shutdown_seconds,
        )
        try:
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
            self._evidence["runtime"] = runtime
            self.task_input = json.dumps(
                response["task"], ensure_ascii=False, sort_keys=True
            )
        except Exception as exc:
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
        timeout_seconds = self.settings.execution_timeout_seconds + 5
        if deadline is not None:
            timeout_seconds = min(timeout_seconds, max(0.0, deadline - time.monotonic()))
        response = self._request(
            "execute",
            {"type": "execute", "code": code},
            "execution",
            timeout_seconds=timeout_seconds,
            solution_controlled=True,
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
        response = self._request(
            "evaluate",
            {"type": "evaluate"},
            "evaluation",
            timeout_seconds=self.settings.evaluation_timeout_seconds,
        )
        self._evidence.update(
            {
                "task_completed": response["task_completed"],
                "evaluation_success": response["evaluation_success"],
                "evaluation": response["evaluation"],
                "evaluation_report": response["report"],
                "success": bool(response["success"]),
            }
        )
        return bool(response["success"])

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[str] = []
        if not self._worker_closed:
            try:
                response = self._request(
                    "close",
                    {"type": "close"},
                    "closed",
                    timeout_seconds=self.settings.shutdown_timeout_seconds,
                )
                self._evidence["resources"] = response["resources"]
                self._worker_closed = True
            except Exception as exc:
                errors.append(str(exc))
        try:
            self.process.close()
            if self.process.cleanup_error:
                errors.append(self.process.cleanup_error)
        except Exception as exc:
            errors.append(str(exc))
        try:
            shutil.rmtree(self.output_directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append(f"Cannot remove AppWorld output: {exc}")
        self._evidence["output_removed"] = not self.output_directory.exists()
        if errors:
            self._evidence["cleanup_error"] = "; ".join(errors)
            raise BenchmarkError("AppWorld worker cleanup failed: " + "; ".join(errors))

    def abort(self) -> None:
        self._closed = True
        errors: list[str] = []
        try:
            self.process.close()
            if self.process.cleanup_error:
                errors.append(self.process.cleanup_error)
        except Exception as exc:
            errors.append(str(exc))
        try:
            shutil.rmtree(self.output_directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append(f"Cannot remove AppWorld output: {exc}")
        self._evidence["output_removed"] = not self.output_directory.exists()
        if errors:
            self._evidence["cleanup_error"] = "; ".join(errors)
            raise BenchmarkError("AppWorld worker abort failed: " + "; ".join(errors))

    def evidence(self) -> dict[str, Any]:
        return self._evidence

    def worker_log(self) -> str:
        return self.process.stderr_text()

    def _request(
        self,
        stage: str,
        message: dict[str, Any],
        expected_type: str,
        *,
        timeout_seconds: float,
        solution_controlled: bool = False,
    ) -> dict[str, Any]:
        from .errors import (
            SolutionExitedError,
            SolutionOutputLimitError,
            SolutionProtocolError,
            SolutionTimeoutError,
        )

        if timeout_seconds <= 0:
            if solution_controlled:
                raise SolutionTimeoutError("Solution exceeded the episode time limit")
            raise BenchmarkError(f"AppWorld worker {stage} deadline expired")
        message["protocol_version"] = 1
        deadline = time.monotonic() + timeout_seconds
        try:
            self.process.send(message, deadline)
            response = self.process.receive(deadline)
        except SolutionTimeoutError:
            if solution_controlled:
                raise
            raise BenchmarkError(f"AppWorld worker timed out during {stage}") from None
        except SolutionOutputLimitError:
            if solution_controlled:
                raise
            raise BenchmarkError(f"AppWorld worker output limit exceeded during {stage}") from None
        except (SolutionProtocolError, SolutionExitedError, OSError, ValueError) as exc:
            raise BenchmarkError(f"AppWorld worker failed during {stage}: {exc}") from exc
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

    def _validate_action(self, action: Any) -> tuple[str, str] | None:
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
