"""Bounded, deadline-aware JSON Lines communication with one solution subprocess."""

from __future__ import annotations

import json
import os
import select
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .errors import (
    SolutionExitedError,
    SolutionOutputLimitError,
    SolutionProtocolError,
    SolutionTimeoutError,
)
from .process_tree import (
    ProcessTreeTracker,
    SupervisedStatus,
    spawn_supervised,
    terminate_process_tree,
)


class SolutionProcess:
    def __init__(
        self,
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
        *,
        max_output_bytes: int,
        max_retained_log_bytes: int,
        shutdown_seconds: float,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.environment = environment
        self.max_output_bytes = max_output_bytes
        self.max_retained_log_bytes = max_retained_log_bytes
        self.shutdown_seconds = shutdown_seconds
        self.process: subprocess.Popen[bytes] | None = None
        self.last_raw_output = ""
        self.last_received_at: float | None = None
        self.cleanup_error: str | None = None
        self._stdout_buffer = bytearray()
        self._stdout_bytes = 0
        self._last_stdout_read_at: float | None = None
        self._stderr = bytearray()
        self._stderr_total = 0
        self._stderr_truncated = False
        self._stderr_limit_exceeded = False
        self._stderr_lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None
        self._tree_tracker: ProcessTreeTracker | None = None
        self._status: SupervisedStatus | None = None

    def start(self) -> None:
        self.process, self._status = spawn_supervised(
            self.command,
            cwd=self.cwd,
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._tree_tracker = ProcessTreeTracker(self.process.pid)
        self._tree_tracker.start()
        if self.process.stdin is not None:
            os.set_blocking(self.process.stdin.fileno(), False)
        if self.process.stdout is not None:
            os.set_blocking(self.process.stdout.fileno(), False)
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name=f"solution-stderr-{self.process.pid}",
            daemon=True,
        )
        self._stderr_thread.start()

    def send(
        self,
        message: dict[str, Any],
        deadline: float,
        *,
        solution_controlled: bool = False,
    ) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise SolutionExitedError("Solution standard input is unavailable")
        encoded = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        if len(encoded) > max(self.max_output_bytes, 1_000_000):
            if solution_controlled:
                raise SolutionOutputLimitError(
                    "Solution-induced observation exceeds the configured byte limit"
                )
            raise ValueError("Outgoing protocol message exceeds the configured byte limit")
        remaining = memoryview(encoded)
        while remaining:
            self._check_stderr_limit()
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0:
                raise SolutionTimeoutError("Timed out while delivering a protocol message")
            try:
                _, writable, _ = select.select([], [process.stdin], [], min(0.1, seconds_left))
                if not writable:
                    if self._solution_returncode() is not None:
                        raise SolutionExitedError(
                            f"Solution exited with status {self._solution_returncode()}"
                        )
                    continue
                written = os.write(process.stdin.fileno(), remaining)
                remaining = remaining[written:]
            except BlockingIOError:
                continue
            except (BrokenPipeError, OSError) as exc:
                raise SolutionExitedError("Solution process does not accept input") from exc

    def receive(self, deadline: float) -> dict[str, Any]:
        process = self._require_process()
        if process.stdout is None:
            raise SolutionExitedError("Solution standard output is unavailable")
        while True:
            self._check_stderr_limit()
            newline = self._stdout_buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._stdout_buffer[: newline + 1])
                del self._stdout_buffer[: newline + 1]
                self.last_received_at = self._last_stdout_read_at or time.monotonic()
                return self._parse_line(line)
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0:
                raise SolutionTimeoutError("Solution exceeded the episode time limit")
            readable, _, _ = select.select([process.stdout], [], [], min(0.1, seconds_left))
            if not readable:
                continue
            try:
                chunk = os.read(process.stdout.fileno(), 65536)
            except BlockingIOError:
                continue
            except OSError as exc:
                raise SolutionExitedError("Cannot read solution output") from exc
            self._last_stdout_read_at = time.monotonic()
            if not chunk:
                if self._stdout_buffer:
                    self.last_raw_output = self._stdout_buffer.decode("utf-8", errors="replace")
                    raise SolutionProtocolError(
                        "Solution exited with an incomplete JSON Lines message"
                    )
                raise SolutionExitedError(
                    f"Solution exited with status {self._solution_returncode()} "
                    "before a final response"
                )
            self._stdout_bytes += len(chunk)
            self._stdout_buffer.extend(chunk)
            if (
                self._stdout_bytes > self.max_output_bytes
                or len(self._stdout_buffer) > self.max_output_bytes
            ):
                raise SolutionOutputLimitError("Solution standard output exceeded its limit")

    @property
    def output_limit_exceeded(self) -> bool:
        with self._stderr_lock:
            return self._stderr_limit_exceeded

    def stderr_text(self) -> str:
        with self._stderr_lock:
            text = self._stderr.decode("utf-8", errors="replace")
            if self._stderr_truncated:
                text += "\n[log truncated by validation service]"
            return text

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        tracked = self._tree_tracker.stop() if self._tree_tracker else set()
        self.cleanup_error = terminate_process_tree(
            process,
            self.shutdown_seconds,
            tracked,
            root_completed=self._solution_returncode() is not None,
        )
        if self._status is not None:
            self._status.close()
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=self.shutdown_seconds)
            if self._stderr_thread.is_alive():
                self.cleanup_error = (
                    self.cleanup_error or "Solution standard-error reader did not terminate"
                )
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _parse_line(self, line: bytes) -> dict[str, Any]:
        self.last_raw_output = line.decode("utf-8", errors="replace").rstrip("\n")
        try:
            message = json.loads(line)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise SolutionProtocolError("Solution output is not valid JSON") from exc
        if not isinstance(message, dict):
            raise SolutionProtocolError("Solution protocol message must be a JSON object")
        return message

    def _read_stderr(self) -> None:
        process = self._require_process()
        if process.stderr is None:
            return
        try:
            while True:
                chunk = process.stderr.read(4096)
                if not chunk:
                    return
                with self._stderr_lock:
                    self._stderr_total += len(chunk)
                    if self._stderr_total > self.max_output_bytes:
                        self._stderr_limit_exceeded = True
                    remaining = self.max_retained_log_bytes - len(self._stderr)
                    if remaining > 0:
                        self._stderr.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self._stderr_truncated = True
        except OSError:
            return

    def _check_stderr_limit(self) -> None:
        if self.output_limit_exceeded:
            raise SolutionOutputLimitError("Solution standard error exceeded its limit")

    def _solution_returncode(self) -> int | None:
        return self._status.poll() if self._status is not None else None

    def _require_process(self) -> subprocess.Popen[bytes]:
        if self.process is None:
            raise RuntimeError("Solution process has not been started")
        return self.process
