"""Isolated episode execution and independent observation collection."""

from __future__ import annotations

import time
from typing import Any

from .blocks_world import (
    TOOL_DESCRIPTIONS,
    BlocksWorldEnvironment,
    TransientFailureInjector,
)
from .config import FailureScenarioConfig, ResolvedProfile
from .contracts import EpisodeRecord, PlannedEpisode
from .errors import (
    BenchmarkError,
    ProvisioningError,
    SolutionExitedError,
    SolutionOutputLimitError,
    SolutionProtocolError,
    SolutionTimeoutError,
)
from .protocol import SolutionProcess
from .provisioning import RevisionPreparer
from .storage import RunStore


class EpisodeExecutor:
    def __init__(
        self,
        resolved_profile: ResolvedProfile,
        revision: RevisionPreparer,
        store: RunStore,
    ) -> None:
        self.profile = resolved_profile.profile
        self.resolved_profile = resolved_profile
        self.revision = revision
        self.store = store

    def execute(self, planned: PlannedEpisode) -> EpisodeRecord:
        action_attempts = 0
        started_at: float | None = None
        elapsed_seconds: float | None = None
        transcript: list[dict[str, Any]] = []
        process: SolutionProcess | None = None
        injector = TransientFailureInjector(self._scenario(planned.fault_scenario_id))
        likely_incorrect: bool | None = None
        error_flag_invalid = False
        completed = False
        answer: str | None = None
        confidence: float | None = None
        reason: str | None = None
        final_response_received = False
        success = False
        termination_reason = "not_started"
        failure_kind = "infrastructure"
        error_message: str | None = None
        try:
            environment = BlocksWorldEnvironment.from_pddl(planned.problem_path)
            self.revision.reset_checkout()
            workspace = self.revision.episode_workspace(planned.episode_id)
            resources = self.profile.resources
            process = SolutionProcess(
                self.revision.solution_command(planned.episode_id),
                workspace,
                self.revision.episode_environment(planned.episode_id),
                max_output_bytes=resources.max_output_bytes,
                max_retained_log_bytes=resources.max_retained_log_bytes,
                shutdown_seconds=resources.process_shutdown_seconds,
            )
            process.start()
            task_message = {
                "protocol_version": 1,
                "type": "task",
                "episode_id": planned.episode_id,
                "task": {
                    "id": planned.task_id,
                    "instructions": planned.instructions,
                    "input": planned.task_input,
                    "tools": TOOL_DESCRIPTIONS,
                },
            }
            started_at = time.monotonic()
            deadline = started_at + self.profile.criteria.AC7.time_limit_seconds
            process.send(task_message, deadline)
            transcript.append({"direction": "service_to_solution", "message": task_message})
            while True:
                message = process.receive(deadline)
                received_at = process.last_received_at or time.monotonic()
                transcript.append({"direction": "solution_to_service", "message": message})
                self._check_deadline(deadline, received_at)
                version = message.get("protocol_version")
                if (
                    type(version) is not int
                    or version != 1
                    or not isinstance(message.get("type"), str)
                ):
                    raise SolutionProtocolError(
                        "Message requires integer protocol_version 1 and a string type"
                    )
                if message["type"] == "actions":
                    if not isinstance(message.get("call_id"), str):
                        raise SolutionProtocolError("Action messages require a string call_id")
                    raw_actions = message.get("actions")
                    actions = raw_actions if isinstance(raw_actions, list) else [raw_actions]
                    if not actions:
                        raise SolutionProtocolError("Action batches must not be empty")
                    action_attempts += len(actions)
                    results = []
                    budget_exceeded = False
                    for offset, action in enumerate(actions):
                        self._check_deadline(deadline)
                        attempt_number = action_attempts - len(actions) + offset + 1
                        if attempt_number > self.profile.criteria.AC6.max_action_attempts:
                            budget_exceeded = True
                            results.append(
                                {
                                    "status": "rejected",
                                    "observation": environment.observation(),
                                    "error": {
                                        "type": "action_budget_exceeded",
                                        "message": (
                                            "Action rejected by the configured action budget"
                                        ),
                                    },
                                }
                            )
                            continue
                        action_name = (
                            action.get("name", "")
                            if isinstance(action, dict) and isinstance(action.get("name"), str)
                            else ""
                        )
                        injected_failure = injector.failure_for(action_name)
                        result = environment.execute(action, injected_failure)
                        injector.confirm(result)
                        results.append(result)
                        self._check_deadline(deadline)
                    observation = {
                        "protocol_version": 1,
                        "type": "observation",
                        "call_id": message["call_id"],
                        "results": results,
                    }
                    self._check_deadline(deadline)
                    try:
                        process.send(observation, deadline, solution_controlled=True)
                    except SolutionExitedError:
                        if not budget_exceeded:
                            raise
                    transcript.append({"direction": "service_to_solution", "message": observation})
                    if budget_exceeded:
                        termination_reason = "action_budget_exhausted"
                        failure_kind = "solution"
                        break
                    continue
                if message["type"] == "final":
                    response_elapsed = max(0.0, received_at - started_at)
                    completed_value = message.get("completed")
                    answer_value = message.get("answer")
                    confidence_value = message.get("confidence")
                    reason_value = message.get("reason")
                    answer = answer_value if isinstance(answer_value, str) else None
                    reason = reason_value if isinstance(reason_value, str) else None
                    confidence = self._confidence(confidence_value)
                    valid_envelope = (
                        isinstance(completed_value, bool)
                        and answer is not None
                        and reason is not None
                        and confidence is not None
                        and 0 <= confidence <= 1
                    )
                    if not valid_envelope:
                        termination_reason = "malformed_output"
                        failure_kind = "solution"
                        error_message = "Final response has invalid required fields"
                        answer = None
                        confidence = None
                        reason = None
                        break
                    completed = completed_value
                    final_response_received = True
                    elapsed_seconds = response_elapsed
                    raw_flag = message.get("likely_incorrect")
                    if isinstance(raw_flag, bool):
                        likely_incorrect = raw_flag
                    else:
                        error_flag_invalid = True
                    success = completed and environment.goal_satisfied()
                    termination_reason = "completed" if completed else "stopped"
                    failure_kind = "none"
                    break
                raise SolutionProtocolError(f"Unknown solution message type: {message['type']}")
        except SolutionTimeoutError as exc:
            termination_reason = "timeout"
            failure_kind = "solution"
            error_message = str(exc)
        except SolutionOutputLimitError as exc:
            termination_reason = "output_limit_exceeded"
            failure_kind = "solution"
            error_message = str(exc)
        except (SolutionProtocolError, SolutionExitedError) as exc:
            termination_reason = (
                "malformed_output" if isinstance(exc, SolutionProtocolError) else "solution_exit"
            )
            failure_kind = "solution"
            error_message = str(exc)
        except (BenchmarkError, ProvisioningError, OSError) as exc:
            termination_reason = "infrastructure_error"
            failure_kind = "infrastructure"
            error_message = str(exc)
        except Exception as exc:  # Preserve an infrastructure failure as auditable evidence.
            termination_reason = "infrastructure_error"
            failure_kind = "infrastructure"
            error_message = f"{type(exc).__name__}: {exc}"
        finally:
            if elapsed_seconds is None and started_at is not None:
                elapsed_seconds = max(0.0, time.monotonic() - started_at)
            if process is not None:
                process.close()
                if process.output_limit_exceeded and failure_kind == "none":
                    success = False
                    termination_reason = "output_limit_exceeded"
                    failure_kind = "solution"
                    error_message = "Solution standard error exceeded its limit"
                if process.cleanup_error:
                    success = False
                    termination_reason = "infrastructure_error"
                    failure_kind = "infrastructure"
                    error_message = process.cleanup_error
                self.store.write_solution_log(planned.episode_id, process.stderr_text())
                if process.last_raw_output and termination_reason == "malformed_output":
                    self.store.write_raw_protocol_error(planned.episode_id, process.last_raw_output)
            self.store.write_protocol(planned.episode_id, transcript)

        infrastructure_failure = failure_kind == "infrastructure"
        record = EpisodeRecord(
            episode_id=planned.episode_id,
            task_id=planned.task_id,
            condition_id=planned.condition_id,
            condition_type=planned.condition_type,
            repeat=planned.repeat,
            success=None if infrastructure_failure else success,
            likely_incorrect=likely_incorrect,
            error_flag_invalid=error_flag_invalid,
            action_attempts=None if infrastructure_failure else action_attempts,
            final_response_received=(None if infrastructure_failure else final_response_received),
            elapsed_seconds=elapsed_seconds,
            completed=completed,
            answer=answer,
            confidence=confidence,
            reason=reason,
            termination_reason=termination_reason,
            failure_kind=failure_kind,
            error_message=error_message[:8192] if error_message else None,
            fault_exposure=(
                None
                if infrastructure_failure and planned.fault_scenario_id
                else injector.exposed
                if planned.fault_scenario_id
                else None
            ),
        )
        self.store.append_record(record)
        return record

    @staticmethod
    def _confidence(value: Any) -> float | None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (OverflowError, ValueError, TypeError):
            return None

    @staticmethod
    def _check_deadline(deadline: float, observed_at: float | None = None) -> None:
        if (observed_at or time.monotonic()) > deadline:
            raise SolutionTimeoutError("Solution exceeded the episode time limit")

    def _scenario(self, scenario_id: str | None) -> FailureScenarioConfig | None:
        if scenario_id is None:
            return None
        return next(
            scenario for scenario in self.profile.failure_scenarios if scenario.id == scenario_id
        )
