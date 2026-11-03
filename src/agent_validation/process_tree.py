"""Linux process-group and descendant supervision."""

from __future__ import annotations

import os
import signal
import struct
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path


class SupervisedStatus:
    """Nonblocking direct-child status reported by the subreaper."""

    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor
        self._buffer = bytearray()
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        try:
            chunk = os.read(self.descriptor, 4 - len(self._buffer))
        except BlockingIOError:
            return None
        except OSError:
            return None
        if chunk:
            self._buffer.extend(chunk)
        if len(self._buffer) == 4:
            self.returncode = struct.unpack("i", self._buffer)[0]
            self.close()
        return self.returncode

    def close(self) -> None:
        if self.descriptor >= 0:
            try:
                os.close(self.descriptor)
            except OSError:
                pass
            self.descriptor = -1


def spawn_supervised(
    command: list[str], **popen_arguments
) -> tuple[subprocess.Popen[bytes], SupervisedStatus]:
    """Start a command under a subreaper and return its root and status channel."""
    status_read, status_write = os.pipe()
    os.set_blocking(status_read, False)
    environment = dict(popen_arguments.pop("env", None) or os.environ)
    environment["AGENT_VALIDATION_STATUS_FD"] = str(status_write)
    pass_fds = tuple(popen_arguments.pop("pass_fds", ())) + (status_write,)
    popen_arguments.pop("start_new_session", None)
    supervisor_command = [sys.executable, "-m", "agent_validation.supervisor", *command]
    try:
        process = subprocess.Popen(
            supervisor_command,
            **popen_arguments,
            env=environment,
            pass_fds=pass_fds,
            start_new_session=True,
        )
    except Exception:
        os.close(status_read)
        os.close(status_write)
        raise
    os.close(status_write)
    return process, SupervisedStatus(status_read)


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
    *,
    root_completed: bool = False,
) -> str | None:
    """Kill and verify the root, its group, and every tracked descendant."""
    descendants = (tracked_pids or set()) | descendant_pids(process.pid)
    _signal_group(process.pid, signal.SIGTERM)
    _signal_pids(descendants, signal.SIGTERM)
    root_timed_out = root_completed
    if not root_completed:
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
