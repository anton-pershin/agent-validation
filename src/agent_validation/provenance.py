"""Exact validator source and runtime provenance collection."""

from __future__ import annotations

import difflib
import importlib.metadata
import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import ProvenanceError


def collect_validator_provenance(source_dir: Path | None = None) -> dict[str, Any]:
    root = _validator_repository(source_dir)
    revision = _git(root, "rev-parse", "HEAD").strip()
    status = _git(root, "status", "--porcelain=v1")
    diff = _git(root, "diff", "--no-ext-diff", "--no-color", "HEAD", "--")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    for relative_name in untracked.split("\0"):
        if not relative_name:
            continue
        path = root / relative_name
        if not path.is_file():
            continue
        content = path.read_bytes()
        if b"\0" in content:
            diff += f"\nBinary untracked file {relative_name} is not text-diffable.\n"
            continue
        text = content.decode("utf-8", errors="replace")
        diff += "".join(
            difflib.unified_diff(
                [],
                text.splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=f"b/{relative_name}",
            )
        )
    return {
        "repository": str(root),
        "revision": revision,
        "clean": not bool(status.strip()),
        "changes": status,
        "diff": diff,
    }


def dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        try:
            name = distribution.metadata["Name"]
            if name:
                versions[name] = distribution.version
        except (AttributeError, KeyError, ValueError):
            continue
    return dict(sorted(versions.items(), key=lambda item: item[0].casefold()))


def _validator_repository(source_dir: Path | None) -> Path:
    configured = source_dir or (
        Path(os.environ["AGENT_VALIDATION_SOURCE_DIR"]).resolve()
        if os.environ.get("AGENT_VALIDATION_SOURCE_DIR")
        else None
    )
    candidates = [
        configured,
        Path.cwd(),
        Path(__file__).resolve().parents[2],
    ]
    attempted: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            completed = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        attempted.append(str(candidate))
        if completed.returncode == 0:
            return Path(completed.stdout.strip()).resolve()
    raise ProvenanceError(
        "Cannot establish validator Git provenance from: " + ", ".join(attempted)
    )


def _git(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvenanceError(f"Cannot inspect validator repository: {exc}") from exc
    if completed.returncode != 0:
        raise ProvenanceError(
            f"Cannot inspect validator repository: {completed.stderr.strip()[:1000]}"
        )
    return completed.stdout
