"""Exact-revision checkout and isolated virtual-environment provisioning."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .config import ResolvedProfile
from .errors import ProvisioningError
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
    ) -> None:
        self.repo = validate_repo(repo)
        self.requested_commit = validate_commit(commit)
        self.resolved_profile = resolved_profile
        self.override_arguments = parse_solution_overrides(solution_overrides)
        temporary_parent.mkdir(parents=True, exist_ok=True)
        self.temporary_root = Path(
            __import__("tempfile").mkdtemp(prefix="agent-validation-", dir=temporary_parent)
        )
        self.checkout = self.temporary_root / "checkout"
        self.virtual_environment = self.temporary_root / "venv"
        self.home = self.temporary_root / "home"
        self.workspaces = self.temporary_root / "workspaces"
        self.verified_commit: str | None = None
        self._reset_lock = threading.Lock()
        self._closed = False

    def __enter__(self) -> RevisionPreparer:
        try:
            self.home.mkdir()
            self.workspaces.mkdir()
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
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def python(self) -> Path:
        return self.virtual_environment / "bin" / "python"

    def solution_command(self) -> list[str]:
        return [
            str(self.python),
            "-m",
            self.resolved_profile.profile.solution_module,
            *self.override_arguments,
        ]

    def episode_environment(self) -> dict[str, str]:
        return solution_environment(
            self.resolved_profile.profile, self.virtual_environment, self.home
        )

    def episode_workspace(self, episode_id: str) -> Path:
        workspace = self.workspaces / episode_id
        workspace.mkdir(exist_ok=False)
        return workspace

    def reset_checkout(self) -> None:
        """Discard solution writes before an episode without touching the external venv."""
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
        shutil.rmtree(self.temporary_root, ignore_errors=True)

    @staticmethod
    def _run(command: list[str], stage: str, timeout: float) -> str:
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=provisioning_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ProvisioningError(f"Timed out while attempting to {stage}") from exc
        except OSError as exc:
            raise ProvisioningError(f"Cannot {stage}: {redact_text(str(exc))}") from exc
        if completed.returncode != 0:
            details = completed.stderr[-8192:] or completed.stdout[-8192:]
            raise ProvisioningError(
                f"Failed to {stage} (exit {completed.returncode}): {redact_text(details)}"
            )
        return completed.stdout
