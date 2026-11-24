"""Isolated AppWorld worker. Run with the AppWorld runtime's Python.

This module intentionally imports only the standard library outside the pinned AppWorld
runtime. It is executed by path, not imported by the validator environment.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import traceback
from typing import Any

PROTOCOL_VERSION = 1


def resource_usage() -> dict[str, float | int]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "peak_rss_kib": int(usage.ru_maxrss),
        "user_cpu_seconds": float(usage.ru_utime),
        "system_cpu_seconds": float(usage.ru_stime),
        "total_cpu_seconds": float(usage.ru_utime + usage.ru_stime),
    }


def send(message: dict[str, Any]) -> None:
    message["protocol_version"] = PROTOCOL_VERSION
    message["resources"] = resource_usage()
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


def run_evaluation(task_id: str, experiment_name: str) -> int:
    try:
        from appworld import evaluate_task

        tracker = evaluate_task(task_id=task_id, experiment_name=experiment_name)
        send(
            {
                "type": "evaluation",
                "evaluation_success": bool(tracker.success),
                "evaluation": tracker.to_dict(stats_only=False),
                "report": tracker.report(print_it=False, colorize=False),
            }
        )
        return 0
    except Exception as exc:
        try:
            send({"type": "error", "stage": "evaluate", "message": str(exc)})
        except Exception:
            pass
        print(traceback.format_exc(), file=sys.stderr)
        return 1


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
            load_ground_truth=False,
            raise_on_failure=False,
            random_seed=settings["random_seed"],
            timeout_seconds=settings["execution_timeout_seconds"],
            ground_truth_mode="minimal",
        )
        from pathlib import Path

        from landlock_sandbox import restrict_filesystem

        root = Path(os.environ["APPWORLD_ROOT"])
        task_data = root / "data" / "tasks" / initial["task_id"]
        temporary = Path(world.output_directory) / "runtime-tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        for name in ("TMPDIR", "HOME", "APPWORLD_CACHE"):
            os.environ[name] = str(temporary)
        landlock_abi = restrict_filesystem(
            read_paths=[
                Path(sys.prefix),
                Path(sys.base_prefix),
                Path("/usr"),
                Path("/etc/ssl"),
                Path("/etc/pki"),
                Path("/etc/localtime"),
                Path("/proc/self"),
                Path("/proc/sys"),
                root / "data" / "api_docs",
                root / "data" / "base_dbs",
                root / "data" / "datasets",
                task_data / "dbs",
                task_data / "specs.json",
            ],
            write_paths=[
                Path(world.output_directory),
                Path("/dev/null"),
                Path("/dev/urandom"),
                Path("/dev/zero"),
            ],
            denied_path=task_data / "ground_truth",
        )
        send(
            {
                "type": "initialized",
                "task": public_task(world),
                "runtime": {
                    "package_version": appworld.__version__,
                    "data_version": world.task.db_version,
                    "python_version": sys.version,
                    "landlock_abi": landlock_abi,
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
            if message_type == "task_completed":
                stage = "task_completed"
                send(
                    {
                        "type": "task_completion",
                        "task_completed": bool(world.task_completed()),
                    }
                )
                continue
            if message_type == "close":
                stage = "close"
                world.close()
                closed = True
                send({"type": "closed"})
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
    if len(sys.argv) == 4 and sys.argv[1] == "--evaluate-task":
        raise SystemExit(run_evaluation(sys.argv[2], sys.argv[3]))
    raise SystemExit(run())
