"""Configurable JSON Lines solution for integration tests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any

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
    args = parser.parse_args()
    task_message = receive()
    task = task_message["task"]
    behavior = args.behavior
    if behavior == "crash":
        raise SystemExit(3)
    if behavior == "timeout":
        if sys.platform != "win32":
            subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)  # agent-validation-test-child"]
            )
        time.sleep(60)
    if behavior == "malformed":
        print("not-json", flush=True)
        return
    if behavior == "output_flood":
        print("x" * 10000, flush=True)
        return
    if behavior == "log_secret":
        print(f"OPENAI_API_KEY={os.environ.get('OPENAI_API_KEY')}", file=sys.stderr)
        print(f"api-key={args.api_key}", file=sys.stderr)
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
    if behavior in {"batch", "batch_after_goal"}:
        execute_batch(task, PLANS.get(task["id"], PLANS["stack_ab"]))
        if behavior == "batch_after_goal":
            request_catalog()
    elif behavior in {"success", "catalog", "missing_flag", "invalid_flag", "log_secret"}:
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
    final(task)


if __name__ == "__main__":
    main()
