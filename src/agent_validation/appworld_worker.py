"""Isolated AppWorld worker. Run with the AppWorld runtime's Python.

This module intentionally imports only the standard library outside the pinned AppWorld
runtime. It is executed by path, not imported by the validator environment.
"""

from __future__ import annotations

import json
import resource
import sys
import traceback
from typing import Any

PROTOCOL_VERSION = 1


def send(message: dict[str, Any]) -> None:
    message["protocol_version"] = PROTOCOL_VERSION
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def receive() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("Validator closed the AppWorld worker input")
    message = json.loads(line)
    if not isinstance(message, dict) or message.get("protocol_version") != PROTOCOL_VERSION:
        raise RuntimeError("Invalid AppWorld worker protocol message")
    return message


def resource_usage() -> dict[str, float | int]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "peak_rss_kib": int(usage.ru_maxrss),
        "user_cpu_seconds": float(usage.ru_utime),
        "system_cpu_seconds": float(usage.ru_stime),
        "total_cpu_seconds": float(usage.ru_utime + usage.ru_stime),
    }


def public_task(world: Any) -> dict[str, Any]:
    task = world.task
    supervisor = task.supervisor
    return {
        "instruction": task.instruction,
        "supervisor": {
            "first_name": supervisor["first_name"],
            "last_name": supervisor["last_name"],
            "email": supervisor["email"],
            "phone_number": supervisor["phone_number"],
        },
        "app_descriptions": dict(task.app_descriptions),
    }


def run() -> int:
    world: Any = None
    closed = False
    stage = "initialize"
    try:
        initial = receive()
        if initial.get("type") != "initialize":
            raise RuntimeError("The first worker message must initialize a task")
        settings = initial["settings"]
        import appworld
        from appworld import AppWorld

        world = AppWorld(
            task_id=initial["task_id"],
            experiment_name=initial["experiment_name"],
            max_interactions=settings["max_interactions"],
            max_api_calls_per_interaction=settings["max_api_calls_per_interaction"],
            raise_on_unsafe_syntax=True,
            null_patch_unsafe_execution=True,
            raise_on_failure=False,
            random_seed=settings["random_seed"],
            timeout_seconds=settings["execution_timeout_seconds"],
            ground_truth_mode="minimal",
        )
        send(
            {
                "type": "initialized",
                "task": public_task(world),
                "runtime": {
                    "package_version": appworld.__version__,
                    "data_version": world.task.db_version,
                    "python_version": sys.version,
                },
            }
        )
        while True:
            message = receive()
            message_type = message.get("type")
            if message_type == "execute":
                stage = "execute"
                output = world.execute(message["code"])
                send({"type": "execution", "output": output})
                continue
            if message_type == "evaluate":
                stage = "evaluate"
                task_completed = bool(world.task_completed())
                tracker = world.evaluate()
                evaluation = tracker.to_dict(stats_only=False)
                report = tracker.report(print_it=False, colorize=False)
                send(
                    {
                        "type": "evaluation",
                        "task_completed": task_completed,
                        "evaluation_success": bool(tracker.success),
                        "success": bool(task_completed and tracker.success),
                        "evaluation": evaluation,
                        "report": report,
                    }
                )
                continue
            if message_type == "close":
                stage = "close"
                world.close()
                closed = True
                send({"type": "closed", "resources": resource_usage()})
                return 0
            raise RuntimeError(f"Unknown AppWorld worker message type: {message_type}")
    except Exception as exc:
        if world is not None and not closed:
            try:
                world.close()
                closed = True
            except Exception:
                print(traceback.format_exc(), file=sys.stderr)
        try:
            send({"type": "error", "stage": stage, "message": str(exc)})
        except Exception:
            pass
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
