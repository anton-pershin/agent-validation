"""Configurable JSON Lines solution for integration tests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import typing_extensions  # noqa: F401  # Prove that declared dependencies are installed.

import reliable_agent

from . import REVISION_ANSWER

PLANS = {
    "stack_ab": [
        {"name": "pick_up", "arguments": {"block": "a"}},
        {"name": "stack", "arguments": {"block": "a", "other_block": "b"}},
    ],
    "unstack_ab": [
        {"name": "unstack", "arguments": {"block": "a", "other_block": "b"}},
        {"name": "put_down", "arguments": {"block": "a"}},
    ],
    "restack_abc": [
        {"name": "unstack", "arguments": {"block": "a", "other_block": "b"}},
        {"name": "put_down", "arguments": {"block": "a"}},
        {"name": "pick_up", "arguments": {"block": "b"}},
        {"name": "stack", "arguments": {"block": "b", "other_block": "c"}},
    ],
    "stack_three": [
        {"name": "pick_up", "arguments": {"block": "b"}},
        {"name": "stack", "arguments": {"block": "b", "other_block": "c"}},
        {"name": "pick_up", "arguments": {"block": "a"}},
        {"name": "stack", "arguments": {"block": "a", "other_block": "b"}},
    ],
    "move_c": [
        {"name": "pick_up", "arguments": {"block": "c"}},
        {"name": "stack", "arguments": {"block": "c", "other_block": "a"}},
    ],
}


def send(message: dict[str, Any]) -> None:
    print(json.dumps(message), flush=True)


def receive() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise SystemExit(2)
    return json.loads(line)


def final(task: dict[str, Any], likely_incorrect: bool = False) -> None:
    send(
        {
            "protocol_version": 1,
            "type": "final",
            "completed": True,
            "answer": REVISION_ANSWER,
            "confidence": 0.99 if not likely_incorrect else 0.2,
            "likely_incorrect": likely_incorrect,
            "reason": "scripted test result",
        }
    )


def execute_actions(task: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    for action in actions:
        for attempt in range(2):
            send(
                {
                    "protocol_version": 1,
                    "type": "actions",
                    "call_id": f"{action['name']}-{attempt}",
                    "actions": [action],
                }
            )
            observation = receive()
            result = observation["results"][0]
            if result["status"] == "ok":
                break
            if result["error"]["type"] != "transient_failure":
                break


def execute_batch(task: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    send(
        {
            "protocol_version": 1,
            "type": "actions",
            "call_id": "batch",
            "actions": actions,
        }
    )
    observation = receive()
    if any(result["status"] != "ok" for result in observation["results"]):
        execute_actions(task, actions)


def request_catalog() -> None:
    send(
        {
            "protocol_version": 1,
            "type": "actions",
            "call_id": "catalog-after-goal",
            "actions": [{"name": "get_action_catalog", "arguments": {}}],
        }
    )
    receive()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--behavior", default="success")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--seed", default="unset")
    args = parser.parse_args()
    behavior = args.behavior
    if behavior == "non_reading":
        time.sleep(60)
    task_message = receive()
    task = task_message["task"]
    if behavior == "echo_seed":
        send(
            {
                "protocol_version": 1,
                "type": "final",
                "completed": False,
                "answer": args.seed,
                "confidence": 0.99,
                "likely_incorrect": True,
                "reason": "scripted seed echo",
            }
        )
        return
    if behavior == "partial_line":
        sys.stdout.write('{"protocol_version": 1, "type": "final"')
        sys.stdout.flush()
        time.sleep(60)
    if behavior == "crash":
        raise SystemExit(3)
    if behavior == "timeout":
        if sys.platform != "win32":
            subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)  # agent-validation-test-child"]
            )
        time.sleep(60)
    if behavior == "timeout_escape":
        if sys.platform != "win32":
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import os, signal, time; os.setsid(); "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "time.sleep(60)  # agent-validation-test-child",
                ]
            )
        time.sleep(60)
    if behavior == "parent_exit_child":
        if sys.platform != "win32":
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import os, signal, time; os.setsid(); "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "time.sleep(60)  # agent-validation-test-child",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return
    if behavior == "malformed":
        print("not-json", flush=True)
        return
    if behavior == "output_flood":
        print("x" * 10000, flush=True)
        return
    if behavior == "stderr_flood":
        for _ in range(100):
            print("x" * 4096, file=sys.stderr, flush=True)
    if behavior == "empty_batch":
        send(
            {
                "protocol_version": 1,
                "type": "actions",
                "call_id": "empty",
                "actions": [],
            }
        )
        time.sleep(0.2)
        return
    if behavior == "missing_call_id":
        send(
            {
                "protocol_version": 1,
                "type": "actions",
                "actions": [{"name": "get_action_catalog", "arguments": {}}],
            }
        )
        time.sleep(0.2)
        return
    if behavior in {"bool_version", "float_version"}:
        send(
            {
                "protocol_version": True if behavior == "bool_version" else 1.0,
                "type": "final",
                "completed": True,
                "answer": "invalid version",
                "confidence": 0.9,
                "likely_incorrect": False,
                "reason": "invalid version",
            }
        )
        return
    if behavior == "malformed_final":
        send(
            {
                "protocol_version": 1,
                "type": "final",
                "completed": True,
                "answer": 123,
                "confidence": 0.9,
                "reason": "malformed answer",
            }
        )
        return
    if behavior == "huge_confidence":
        digits = "9" * 5000
        print(
            '{"protocol_version":1,"type":"final","completed":true,'
            f'"answer":"x","confidence":{digits},"likely_incorrect":false,"reason":"x"}}',
            flush=True,
        )
        return
    if behavior == "malformed_action_fault":
        send(
            {
                "protocol_version": 1,
                "type": "actions",
                "call_id": "malformed-fault",
                "actions": [{"name": "pick_up", "arguments": None}],
            }
        )
        receive()
        send(
            {
                "protocol_version": 1,
                "type": "final",
                "completed": False,
                "answer": "stopped",
                "confidence": 0.1,
                "likely_incorrect": True,
                "reason": "malformed action",
            }
        )
        return
    if behavior == "log_secret":
        provider_secret = os.environ.get("OPENAI_API_KEY", "")
        proxy_secret = urlsplit(os.environ.get("HTTPS_PROXY", "")).password or ""
        print(f"OPENAI_API_KEY={provider_secret}", file=sys.stderr)
        print(provider_secret, file=sys.stderr)
        print(proxy_secret, file=sys.stderr)
        print(f"api-key={args.api_key}", file=sys.stderr)
        send(
            {
                "protocol_version": 1,
                "type": "actions",
                "call_id": provider_secret,
                "actions": [
                    {
                        "name": "get_action_catalog",
                        "arguments": {"required": [provider_secret]},
                    }
                ],
            }
        )
        receive()
    if behavior == "amplification":
        send(
            {
                "protocol_version": 1,
                "type": "actions",
                "call_id": "amplification",
                "actions": [{"name": "get_action_catalog", "arguments": {}} for _ in range(3000)],
            }
        )
        time.sleep(1)
        return
    if behavior == "budget":
        send(
            {
                "protocol_version": 1,
                "type": "actions",
                "call_id": "over-budget",
                "actions": [{"name": "get_action_catalog", "arguments": {}} for _ in range(60)],
            }
        )
        time.sleep(0.2)
        return
    if behavior == "catalog":
        send(
            {
                "protocol_version": 1,
                "type": "actions",
                "call_id": "catalog",
                "actions": [{"name": "get_action_catalog", "arguments": {}}],
            }
        )
        receive()
    state_ok = True
    if behavior == "mutate_state":
        marker = Path.home() / "episode-marker"
        if marker.exists():
            state_ok = False
        else:
            marker.write_text("episode state")
        target = Path(reliable_agent.__file__)
        target.chmod(target.stat().st_mode | 0o200)
        target.write_text(target.read_text().replace(REVISION_ANSWER, "state-leak"))
    if behavior in {"batch", "batch_after_goal"}:
        execute_batch(task, PLANS.get(task["id"], PLANS["stack_ab"]))
        if behavior == "batch_after_goal":
            request_catalog()
    elif behavior in {
        "success",
        "catalog",
        "missing_flag",
        "invalid_flag",
        "log_secret",
        "mutate_state",
        "stderr_flood",
        "stop_after_goal",
    }:
        execute_actions(task, PLANS.get(task["id"], PLANS["stack_ab"]))
    if behavior == "no_final":
        return
    if behavior == "wrong":
        final(task, likely_incorrect=True)
        return
    if behavior == "unflagged_failure":
        final(task, likely_incorrect=False)
        return
    if behavior == "missing_flag":
        send(
            {
                "protocol_version": 1,
                "type": "final",
                "completed": True,
                "answer": REVISION_ANSWER,
                "confidence": 0.9,
                "reason": "missing flag",
            }
        )
        return
    if behavior == "invalid_flag":
        send(
            {
                "protocol_version": 1,
                "type": "final",
                "completed": True,
                "answer": REVISION_ANSWER,
                "confidence": 0.9,
                "likely_incorrect": "no",
                "reason": "invalid flag",
            }
        )
        return
    if behavior == "stop_after_goal":
        send(
            {
                "protocol_version": 1,
                "type": "final",
                "completed": False,
                "answer": "stopped after reaching the goal",
                "confidence": 0.2,
                "likely_incorrect": True,
                "reason": "no completion declaration",
            }
        )
        return
    if behavior == "mutate_state" and not state_ok:
        send(
            {
                "protocol_version": 1,
                "type": "final",
                "completed": True,
                "answer": "state-leak",
                "confidence": 0.1,
                "likely_incorrect": True,
                "reason": "shared mutable state detected",
            }
        )
        return
    if behavior == "log_secret":
        send(
            {
                "protocol_version": 1,
                "type": "final",
                "completed": True,
                "answer": provider_secret,
                "confidence": 0.9,
                "likely_incorrect": False,
                "reason": f"{args.api_key or ''} {proxy_secret}",
            }
        )
        return
    final(task)


if __name__ == "__main__":
    main()
