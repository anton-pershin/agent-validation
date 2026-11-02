"""Input parsing, environment filtering, and secret redaction."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

from .config import ValidationProfile
from .errors import InvalidRequestError

_COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[-_]?key|access[-_]?token|auth[-_]?token|password|passwd|secret|authorization)",
    re.IGNORECASE,
)
_URL_CREDENTIALS_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
_OPTION_SECRET_RE = re.compile(
    r"(--?(?:api[-_]?key|access[-_]?token|auth[-_]?token|password|passwd|secret)"
    r"(?:\s+|=))([^\s]+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"((?:API[-_]?KEY|ACCESS[-_]?TOKEN|AUTH[-_]?TOKEN|PASSWORD|PASSWD|SECRET|AUTHORIZATION)"
    r"\s*=\s*)([^\s]+)",
    re.IGNORECASE,
)

_SYSTEM_ENV_NAMES = {
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_CERT",
    "PIP_NO_INPUT",
}


def validate_commit(commit: str) -> str:
    if not _COMMIT_RE.fullmatch(commit):
        raise InvalidRequestError("Commit must be a full 40- or 64-character hexadecimal hash")
    return commit.lower()


def validate_repo(repo: str) -> str:
    if not repo.strip() or any(ord(character) < 32 for character in repo):
        raise InvalidRequestError("Repository must be nonempty and contain no control characters")
    return repo


def parse_solution_overrides(overrides: str) -> list[str]:
    try:
        arguments = shlex.split(overrides, posix=True)
    except ValueError as exc:
        raise InvalidRequestError(f"Cannot parse solution_overrides: {exc}") from exc
    if len(arguments) > 128:
        raise InvalidRequestError("solution_overrides contains more than 128 arguments")
    return arguments


def redact_text(value: str) -> str:
    value = _URL_CREDENTIALS_RE.sub(r"\1<redacted>@", value)
    value = _OPTION_SECRET_RE.sub(r"\1<redacted>", value)
    value = _BEARER_RE.sub(r"\1<redacted>", value)
    return _ASSIGNMENT_SECRET_RE.sub(r"\1<redacted>", value)


def redact_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if _SENSITIVE_KEY_RE.search(str(key)) else redact_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def provisioning_environment() -> dict[str, str]:
    environment = {name: os.environ[name] for name in _SYSTEM_ENV_NAMES if name in os.environ}
    environment["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return environment


def solution_environment(
    profile: ValidationProfile, virtual_environment: Path, home: Path
) -> dict[str, str]:
    allowed = _SYSTEM_ENV_NAMES | set(profile.environment.passthrough)
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    bin_path = virtual_environment / "bin"
    original_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    environment.update(
        {
            "PATH": f"{bin_path}:{original_path}",
            "HOME": str(home),
            "VIRTUAL_ENV": str(virtual_environment),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    return environment
