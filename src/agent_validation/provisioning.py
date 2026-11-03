"""Exact-revision checkout and isolated virtual-environment provisioning."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .config import ResolvedProfile
from .errors import ProvisioningError
from .process_tree import ProcessTreeTracker, terminate_process_tree
from .security import (
    parse_solution_overrides,
    provisioning_environment,
    redact_text,
    solution_environment,
    validate_commit,
    validate_repo,
)


class RevisionPreparer:
    def __init__(
        self,
        repo: str,
        commit: str,
        solution_overrides: str,
        resolved_profile: ResolvedProfile,
        temporary_parent: Path,
        secret_values: set[str] | None = None,
    ) -> None:
        self.repo = validate_repo(repo)
        self.requested_commit = validate_commit(commit)
        self.resolved_profile = resolved_profile
        self.override_arguments = parse_solution_overrides(solution_overrides)
        self.secret_values = secret_values or set()
        temporary_parent.mkdir(parents=True, exist_ok=True)
        self.temporary_root = Path(
            tempfile.mkdtemp(prefix="agent-validation-", dir=temporary_parent)
        )
        self.checkout = self.temporary_root / "checkout"
        self.virtual_environment = self.temporary_root / "venv"
        self.homes = self.temporary_root / "homes"
        self.workspaces = self.temporary_root / "workspaces"
        self.episode_runtimes = self.temporary_root / "episode-runtimes"
        self.verified_commit: str | None = None
        self.dependency_manifest = ""
        self.cleanup_error: str | None = None
        self.events: list[dict[str, object]] = []
        self._reset_lock = threading.Lock()
        self._runtime_lock = threading.Lock()
        self._episode_venvs: dict[str, Path] = {}
        self._closed = False

    def __enter__(self) -> RevisionPreparer:
        try:
            self.homes.mkdir()
            self.workspaces.mkdir()
            self.episode_runtimes.mkdir()
            self._run(
                ["git", "clone", "--no-hardlinks", "--", self.repo, str(self.checkout)],
                "clone repository",
                self.resolved_profile.profile.resources.git_timeout_seconds,
            )
            self._run(
                [
                    "git",
                    "-C",
                    str(self.checkout),
                    "checkout",
                    "--detach",
                    self.requested_commit,
                ],
                "checkout requested commit",
                self.resolved_profile.profile.resources.git_timeout_seconds,
            )
            verified = self._run(
                ["git", "-C", str(self.checkout), "rev-parse", "HEAD"],
                "verify requested commit",
                self.resolved_profile.profile.resources.git_timeout_seconds,
            ).strip()
            if verified.lower() != self.requested_commit:
                raise ProvisioningError(
                    "Checked-out commit does not match the requested full commit hash"
                )
            self.verified_commit = verified.lower()
            self._run(
                [sys.executable, "-m", "venv", str(self.virtual_environment)],
                "create virtual environment",
                self.resolved_profile.profile.resources.install_timeout_seconds,
            )
            self._run(
                [
                    str(self.python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    str(self.checkout),
                ],
                "install solution dependencies",
                self.resolved_profile.profile.resources.install_timeout_seconds,
            )
            self.dependency_manifest = self._run(
                [str(self.python), "-m", "pip", "freeze"],
                "record solution dependencies",
                self.resolved_profile.profile.resources.git_timeout_seconds,
            )
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def python(self) -> Path:
        return self.virtual_environment / "bin" / "python"

    def solution_command(self, episode_id: str) -> list[str]:
        runtime = self._episode_runtime(episode_id)
        return [
            str(runtime / "bin" / "python"),
            "-m",
            self.resolved_profile.profile.solution_module,
            *self.override_arguments,
        ]

    def episode_environment(self, episode_id: str) -> dict[str, str]:
        runtime = self._episode_runtime(episode_id)
        home = self.homes / episode_id
        home.mkdir(exist_ok=False)
        return solution_environment(self.resolved_profile.profile, runtime, home)

    def _episode_runtime(self, episode_id: str) -> Path:
        """Give every episode a mutable but independent copy of the installed runtime."""
        with self._runtime_lock:
            if episode_id in self._episode_venvs:
                return self._episode_venvs[episode_id]
            runtime = self.episode_runtimes / episode_id
            shutil.copytree(self.virtual_environment, runtime, symlinks=True)
            self._episode_venvs[episode_id] = runtime
            return runtime

    def episode_workspace(self, episode_id: str) -> Path:
        workspace = self.workspaces / episode_id
        workspace.mkdir(exist_ok=False)
        return workspace

    def reset_checkout(self) -> None:
        """Discard checkout writes; episodes execute from the installed environment."""
        with self._reset_lock:
            timeout = self.resolved_profile.profile.resources.git_timeout_seconds
            self._run(
                [
                    "git",
                    "-C",
                    str(self.checkout),
                    "reset",
                    "--hard",
                    self.requested_commit,
                ],
                "reset solution checkout",
                timeout,
            )
            self._run(
                ["git", "-C", str(self.checkout), "clean", "-fdx"],
                "clean solution checkout",
                timeout,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.temporary_root.exists():
                shutil.rmtree(self.temporary_root)
        except OSError as exc:
            self.cleanup_error = f"Cannot remove temporary resources: {exc}"

    def _run(self, command: list[str], stage: str, timeout: float) -> str:
        resources = self.resolved_profile.profile.resources
        capture = _BoundedCapture(resources.max_provisioning_output_bytes)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=provisioning_environment(),
                start_new_session=True,
            )
        except OSError as exc:
            raise ProvisioningError(f"Cannot {stage}: {self._redact(str(exc))}") from exc
        tracker = ProcessTreeTracker(process.pid)
        tracker.start()
        threads = [
            threading.Thread(
                target=capture.read,
                args=(process.stdout, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=capture.read,
                args=(process.stderr, "stderr"),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + timeout
        failure: ProvisioningError | None = None
        while process.poll() is None:
            if capture.exceeded:
                failure = ProvisioningError(f"Output limit exceeded while attempting to {stage}")
                break
            if time.monotonic() > deadline:
                failure = ProvisioningError(f"Timed out while attempting to {stage}")
                break
            time.sleep(0.01)
        if failure is None:
            try:
                process.wait(timeout=resources.process_shutdown_seconds)
            except subprocess.TimeoutExpired:
                failure = ProvisioningError(f"Cannot finish {stage}")
        cleanup_error = terminate_process_tree(
            process, resources.process_shutdown_seconds, tracker.stop()
        )
        if cleanup_error:
            message = f"Cannot clean up {stage}: {cleanup_error}"
            failure = (
                ProvisioningError(f"{failure}; {message}")
                if failure is not None
                else ProvisioningError(message)
            )
        for thread in threads:
            thread.join(timeout=resources.process_shutdown_seconds)
        if failure is None and capture.exceeded:
            failure = ProvisioningError(f"Output limit exceeded while attempting to {stage}")
        stdout, stderr = capture.outputs()
        event = {
            "stage": stage,
            "return_code": process.poll(),
            "stdout": self._redact(stdout[-8192:]),
            "stderr": self._redact(stderr[-8192:]),
        }
        self.events.append(event)
        if failure is not None:
            raise failure
        if process.returncode != 0:
            details = stderr[-8192:] or stdout[-8192:]
            raise ProvisioningError(
                f"Failed to {stage} (exit {process.returncode}): {self._redact(details)}"
            )
        return stdout

    def _redact(self, value: str) -> str:
        return redact_text(value, self.secret_values)


class _BoundedCapture:
    def __init__(self, maximum_bytes: int) -> None:
        self.maximum_bytes = maximum_bytes
        self._outputs = {"stdout": bytearray(), "stderr": bytearray()}
        self._total = 0
        self._exceeded = False
        self._lock = threading.Lock()

    @property
    def exceeded(self) -> bool:
        with self._lock:
            return self._exceeded

    def read(self, stream, name: str) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with self._lock:
                    self._total += len(chunk)
                    if self._total > self.maximum_bytes:
                        self._exceeded = True
                    remaining = self.maximum_bytes - len(self._outputs[name])
                    if remaining > 0:
                        self._outputs[name].extend(chunk[:remaining])
        except OSError:
            return

    def outputs(self) -> tuple[str, str]:
        with self._lock:
            return (
                self._outputs["stdout"].decode("utf-8", errors="replace"),
                self._outputs["stderr"].decode("utf-8", errors="replace"),
            )
