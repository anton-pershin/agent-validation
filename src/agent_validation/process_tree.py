"""Best-effort Linux process-group and descendant supervision."""

from __future__ import annotations

import os
import signal
import subprocess
from collections import defaultdict
from pathlib import Path


def descendant_pids(root_pid: int) -> set[int]:
    """Return current descendants, including children that changed process group."""
    children: dict[int, list[int]] = defaultdict(list)
    try:
        entries = list(Path("/proc").glob("[0-9]*/stat"))
    except OSError:
        return set()
    for entry in entries:
        try:
            content = entry.read_text()
            fields = content[content.rfind(")") + 2 :].split()
            parent = int(fields[1])
            pid = int(entry.parent.name)
        except (OSError, ValueError, IndexError):
            continue
        children[parent].append(pid)
    descendants: set[int] = set()
    pending = list(children[root_pid])
    while pending:
        pid = pending.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        pending.extend(children[pid])
    return descendants


def terminate_process_tree(process: subprocess.Popen, shutdown_seconds: float) -> str | None:
    """Terminate a process group and known escaped descendants; return cleanup errors."""
    descendants = descendant_pids(process.pid)
    _signal_group(process.pid, signal.SIGTERM)
    _signal_pids(descendants, signal.SIGTERM)
    try:
        process.wait(timeout=shutdown_seconds)
    except subprocess.TimeoutExpired:
        _signal_group(process.pid, signal.SIGKILL)
        _signal_pids(descendants | descendant_pids(process.pid), signal.SIGKILL)
        try:
            process.wait(timeout=shutdown_seconds)
        except subprocess.TimeoutExpired:
            return f"Process {process.pid} could not be reaped after SIGKILL"
    return None


def _signal_group(process_group: int, signal_number: signal.Signals) -> None:
    try:
        os.killpg(process_group, signal_number)
    except (ProcessLookupError, PermissionError):
        pass


def _signal_pids(pids: set[int], signal_number: signal.Signals) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal_number)
        except (ProcessLookupError, PermissionError):
            pass
