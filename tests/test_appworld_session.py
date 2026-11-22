from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from appworld_helpers import assert_public_message, resolved_appworld

from agent_validation.appworld_session import AppWorldSession
from agent_validation.errors import BenchmarkError


def action(code: str) -> dict:
    return {"name": "execute_code", "arguments": {"code": code}}


def test_public_stateful_execution_completion_and_private_evaluation(
    fake_appworld_runtime: Path,
) -> None:
    resolved = resolved_appworld()
    session = AppWorldSession(resolved, resolved.plan_episodes()[0], run_id="run-one")
    worker_process = session.process.process
    assert_public_message(session.task_input)
    public_task = json.loads(session.task_input)
    assert set(public_task) == {"instruction", "supervisor", "app_descriptions"}
    assert session.action_limit == 100
    assert resolved.profile.criteria.AC6.max_action_attempts == 50
    assert session.tools[0]["name"] == "execute_code"
    assert session.execute(action("value = 41"))["status"] == "ok"
    result = session.execute(action("print(value + 1)"))
    assert result["observation"]["output"] == "42\n"
    assert session.execute({"name": "unknown", "arguments": {}})["error"]["type"] == (
        "unknown_action"
    )
    assert session.execute(action(""))["error"]["type"] == "malformed_action"
    injected = session.execute(action("print('not executed')"), "temporary")
    assert injected["error"]["type"] == "transient_failure"
    assert session.evidence()["execution_count"] == 2
    assert session.execute(action("apis.supervisor.complete_task()"))["status"] == "ok"
    # AppWorld completion does not end the episode or prevent later actions.
    assert session.execute(action("print('still running')"))["status"] == "ok"
    assert_public_message(json.dumps(result))
    assert session.succeed(True) is True
    evidence = session.evidence()
    assert evidence["task_completed"] is True
    assert evidence["runtime"]["landlock_abi"] >= 1
    assert evidence["evaluation_success"] is True
    assert evidence["evaluation"]["private"] == "PRIVATE_EVALUATION"
    assert evidence["evaluation_report"] == "PRIVATE_EVALUATION_REPORT"
    evaluator_process = session.evaluation_process.process
    session.close()
    assert worker_process is not None and worker_process.poll() is not None
    assert evaluator_process is not None and evaluator_process.poll() is not None
    assert evidence["resources"]["peak_rss_kib"] > 0
    assert evidence["resources"]["total_cpu_seconds"] >= 0
    assert evidence["output_removed"] is True
    assert not session.output_directory.exists()


@pytest.mark.parametrize("complete_in_appworld", [False, True])
@pytest.mark.parametrize("task_id", ["task_one", "eval_fail"])
def test_protocol_completion_false_skips_private_evaluation(
    fake_appworld_runtime: Path, complete_in_appworld: bool, task_id: str
) -> None:
    resolved = resolved_appworld(task_id)
    session = AppWorldSession(resolved, resolved.plan_episodes()[0], run_id="run-stop")
    if complete_in_appworld:
        session.execute(action("apis.supervisor.complete_task()"))
    assert session.succeed(False) is False
    assert "evaluation" not in session.evidence()
    session.close()


@pytest.mark.parametrize(
    ("task_id", "complete_in_appworld", "evaluation_success"),
    [
        ("task_one", False, True),
        ("eval_fail", True, False),
        ("eval_fail", False, False),
    ],
)
def test_appworld_completion_and_evaluator_success_are_both_required(
    fake_appworld_runtime: Path,
    task_id: str,
    complete_in_appworld: bool,
    evaluation_success: bool,
) -> None:
    resolved = resolved_appworld(task_id)
    session = AppWorldSession(resolved, resolved.plan_episodes()[0], run_id="run-matrix")
    if complete_in_appworld:
        session.execute(action("apis.supervisor.complete_task()"))
    assert session.succeed(True) is False
    evidence = session.evidence()
    assert evidence["task_completed"] is complete_in_appworld
    assert evidence["evaluation_success"] is evaluation_success
    session.close()


def test_evaluation_error_is_an_infrastructure_error(fake_appworld_runtime: Path) -> None:
    resolved = resolved_appworld("eval_crash")
    session = AppWorldSession(resolved, resolved.plan_episodes()[0], run_id="run-eval-error")
    with pytest.raises(BenchmarkError, match="evaluate failed"):
        session.succeed(True)
    session.abort()
    assert not session.output_directory.exists()


def test_runtime_identity_mismatch_is_rejected(fake_appworld_runtime: Path) -> None:
    resolved = resolved_appworld("data_mismatch")
    partial = []
    with pytest.raises(BenchmarkError, match="data version"):
        AppWorldSession(
            resolved,
            resolved.plan_episodes()[0],
            run_id="run-mismatch",
            on_create=partial.append,
        )
    session = partial[0]
    process = session.process.process
    assert process is not None and process.poll() is not None
    assert session.evidence()["resources"]["peak_rss_kib"] > 0
    assert session.evidence()["runtime"]["data_version"] == "other"
    assert "data version" in session.evidence()["startup_error"]
    outputs = fake_appworld_runtime / "experiments" / "outputs"
    assert not outputs.exists() or not any(outputs.iterdir())


def test_parallel_workers_have_isolated_state_and_output(fake_appworld_runtime: Path) -> None:
    first_resolved = resolved_appworld("task_one")
    second_resolved = resolved_appworld("task_two")
    first = AppWorldSession(
        first_resolved, first_resolved.plan_episodes()[0], run_id="run-parallel"
    )
    second = AppWorldSession(
        second_resolved, second_resolved.plan_episodes()[0], run_id="run-parallel"
    )
    assert first.output_directory != second.output_directory
    first.execute(action("value = 'first'"))
    second.execute(action("value = 'second'"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda session: session.execute(action("print(value)"))["observation"][
                    "output"
                ],
                [first, second],
            )
        )
    assert results == ["first\n", "second\n"]
    first.close()
    second.close()
    assert not first.output_directory.exists()
    assert not second.output_directory.exists()


def test_abort_terminates_worker_and_removes_output(fake_appworld_runtime: Path) -> None:
    resolved = resolved_appworld()
    session = AppWorldSession(resolved, resolved.plan_episodes()[0], run_id="run-abort")
    process = session.process.process
    assert process is not None and process.poll() is None
    session.abort()
    assert process.poll() is not None
    assert session.evidence()["resources"]["peak_rss_kib"] > 0
    assert not session.output_directory.exists()


def test_execution_errors_private_reads_and_environment_filtering(
    fake_appworld_runtime: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    monkeypatch.setenv("PIP_INDEX_URL", "https://user:pip-secret@example.invalid/simple")
    resolved = resolved_appworld()
    session = AppWorldSession(resolved, resolved.plan_episodes()[0], run_id="run-errors")
    failed = session.execute(action("raise ValueError('boom')"))
    assert failed["status"] == "error"
    assert failed["error"]["type"] == "execution_failed"
    assert "boom" in failed["error"]["message"]
    private_read = (
        "from pathlib import Path\n"
        "try:\n"
        "    value = Path('data/tasks/task_one/ground_truth/answer.json').read_text()\n"
        "    print('PRIVATE_READ_OK', len(value))\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__)\n"
    )
    denied = session.execute(action(private_read))
    assert denied["observation"]["output"].strip() == "PermissionError"
    assert "FAKE_GROUND_TRUTH_SECRET" not in denied["observation"]["output"]
    environment_probe = session.execute(
        action("import os; print(os.getenv('OPENAI_API_KEY'), os.getenv('PIP_INDEX_URL'))")
    )
    assert environment_probe["observation"]["output"].strip() == "None None"
    session.abort()
