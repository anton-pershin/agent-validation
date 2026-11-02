"""Bounded JSON Lines communication with one solution subprocess."""

from __future__ import annotations

import json
import os
import select
import signal
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
        self._stdout_bytes = 0
        self._stderr = bytearray()
        self._stderr_total = 0
        self._stderr_truncated = False
        self._stderr_limit_exceeded = False
        self._stderr_lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name=f"solution-stderr-{self.process.pid}",
            daemon=True,
        )
        self._stderr_thread.start()

    def send(self, message: dict[str, Any]) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise SolutionExitedError("Solution standard input is unavailable")
        encoded = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            process.stdin.write(encoded)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise SolutionExitedError("Solution process does not accept input") from exc

    def receive(self, deadline: float) -> dict[str, Any]:
        process = self._require_process()
        if process.stdout is None:
            raise SolutionExitedError("Solution standard output is unavailable")
        while True:
            self._check_stderr_limit()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SolutionTimeoutError("Solution exceeded the episode time limit")
            readable, _, _ = select.select([process.stdout], [], [], min(0.1, remaining))
            if not readable:
                if process.poll() is not None and not process.stdout:
                    raise SolutionExitedError(f"Solution exited with status {process.returncode}")
                continue
            line = process.stdout.readline(self.max_output_bytes + 1)
            if not line:
                raise SolutionExitedError(
                    f"Solution exited with status {process.poll()} before a final response"
                )
            self._stdout_bytes += len(line)
            if len(line) > self.max_output_bytes or self._stdout_bytes > self.max_output_bytes:
                raise SolutionOutputLimitError("Solution standard output exceeded its limit")
            self.last_raw_output = line.decode("utf-8", errors="replace").rstrip("\n")
            try:
                message = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SolutionProtocolError("Solution output is not valid JSON") from exc
            if not isinstance(message, dict):
                raise SolutionProtocolError("Solution protocol message must be a JSON object")
            return message

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
        self._signal_group(signal.SIGTERM)
        try:
            process.wait(timeout=self.shutdown_seconds)
        except subprocess.TimeoutExpired:
            self._signal_group(signal.SIGKILL)
            try:
                process.wait(timeout=self.shutdown_seconds)
            except subprocess.TimeoutExpired:
                pass
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=self.shutdown_seconds)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _read_stderr(self) -> None:
        process = self._require_process()
        if process.stderr is None:
            return
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

    def _check_stderr_limit(self) -> None:
        with self._stderr_lock:
            if self._stderr_limit_exceeded:
                raise SolutionOutputLimitError("Solution standard error exceeded its limit")

    def _signal_group(self, signal_number: signal.Signals) -> None:
        process = self.process
        if process is None:
            return
        try:
            os.killpg(process.pid, signal_number)
        except (ProcessLookupError, PermissionError):
            pass

    def _require_process(self) -> subprocess.Popen[bytes]:
        if self.process is None:
            raise RuntimeError("Solution process has not been started")
        return self.process
