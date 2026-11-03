"""Linux process-group and descendant supervision."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path


class ProcessTreeTracker:
    """Track descendants during a process lifetime, including new sessions."""

    def __init__(self, root_pid: int) -> None:
        self.root_pid = root_pid
        self._known: set[int] = set()
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._track,
            name=f"process-tree-tracker-{root_pid}",
            daemon=True,
        )

    def start(self) -> None:
        self._refresh()
        self._thread.start()

    def stop(self) -> set[int]:
        self._stopped.set()
        self._thread.join(timeout=1)
        self._refresh()
        return self.tracked()

    def tracked(self) -> set[int]:
        with self._lock:
            return set(self._known)

    def _track(self) -> None:
        while not self._stopped.wait(0.02):
            self._refresh()

    def _refresh(self) -> None:
        descendants = descendant_pids(self.root_pid)
        with self._lock:
            self._known.update(descendants)


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


def terminate_process_tree(
    process: subprocess.Popen,
    shutdown_seconds: float,
    tracked_pids: set[int] | None = None,
) -> str | None:
    """Kill and verify the root, its group, and every tracked descendant."""
    descendants = (tracked_pids or set()) | descendant_pids(process.pid)
    _signal_group(process.pid, signal.SIGTERM)
    _signal_pids(descendants, signal.SIGTERM)
    root_timed_out = False
    try:
        process.wait(timeout=shutdown_seconds)
    except subprocess.TimeoutExpired:
        root_timed_out = True

    # Descendants get no free pass when the root exits first or ignores SIGTERM.
    remaining = _living_pids(descendants)
    if root_timed_out or remaining or process.poll() is None:
        _signal_group(process.pid, signal.SIGKILL)
        _signal_pids(remaining | descendant_pids(process.pid), signal.SIGKILL)
    if process.poll() is None:
        try:
            process.wait(timeout=shutdown_seconds)
        except subprocess.TimeoutExpired:
            return f"Process {process.pid} could not be reaped after SIGKILL"
    time.sleep(0.02)
    survivors = _living_pids(descendants | descendant_pids(process.pid))
    if survivors:
        return f"Solution descendants survived SIGKILL: {sorted(survivors)}"
    return None


def _living_pids(pids: set[int]) -> set[int]:
    living = set()
    for pid in pids:
        try:
            content = Path(f"/proc/{pid}/stat").read_text()
            state = content[content.rfind(")") + 2 :].split()[0]
            if state != "Z":
                living.add(pid)
        except (OSError, IndexError):
            continue
    return living


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
