"""Input parsing, environment filtering, and run-specific secret redaction."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .config import ValidationProfile
from .contracts import ValidationRequest
from .errors import InvalidRequestError

_COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[-_]?key|(?:access[-_]?|auth[-_]?)?token(?!s|[-_]?(?:count|limit|limits|max|maximum|budget|size|usage))|password|passwd|secret|authorization)",
    re.IGNORECASE,
)
_URL_CREDENTIALS_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^\s/@]+@")
_OPTION_SECRET_RE = re.compile(
    r"(--?(?:api[-_]?key|(?:access[-_]?|auth[-_]?)?token|password|passwd|secret)"
    r"(?:\s+|=))([^\s]+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"((?:API[-_]?KEY|(?:ACCESS[-_]?|AUTH[-_]?)?TOKEN(?!S|[-_]?(?:COUNT|LIMIT|LIMITS|MAX|MAXIMUM|BUDGET|SIZE|USAGE))|PASSWORD|PASSWD|SECRET|AUTHORIZATION)"
    r"\s*=\s*)([^\s]+)",
    re.IGNORECASE,
)
_COLON_QUOTED_SECRET_RE = re.compile(
    r"((?:['\"])?(?:API[-_]?KEY|(?:ACCESS[-_]?|AUTH[-_]?)?TOKEN(?!S|[-_]?(?:COUNT|LIMIT|LIMITS|MAX|MAXIMUM|BUDGET|SIZE|USAGE))|PASSWORD|PASSWD|SECRET|AUTHORIZATION)(?:['\"])?\s*:\s*)(['\"])([^'\"\r\n]+)\2",
    re.IGNORECASE,
)
_COLON_BARE_SECRET_RE = re.compile(
    r"((?:['\"])?(?:API[-_]?KEY|(?:ACCESS[-_]?|AUTH[-_]?)?TOKEN(?!S|[-_]?(?:COUNT|LIMIT|LIMITS|MAX|MAXIMUM|BUDGET|SIZE|USAGE))|PASSWORD|PASSWD|SECRET|AUTHORIZATION)(?:['\"])?\s*:\s*)(?!['\"])([^\s,}\]]+)",
    re.IGNORECASE,
)

_PROVISIONING_ONLY_ENV_NAMES = {
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_CERT",
    "PIP_NO_INPUT",
}
_RUNTIME_SYSTEM_ENV_NAMES = {
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
}
_SYSTEM_ENV_NAMES = _RUNTIME_SYSTEM_ENV_NAMES | _PROVISIONING_ONLY_ENV_NAMES
_CREDENTIAL_URL_ENV_NAMES = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
}
_STRUCTURAL_STRING_KEYS = {
    "acceptance_status",
    "action",
    "artifacts_location",
    "base_profile",
    "action_name",
    "computation_status",
    "condition_id",
    "condition_type",
    "direction",
    "episode_id",
    "expected_value_or_threshold",
    "failure_kind",
    "fault_scenario_id",
    "id",
    "metric_id",
    "name",
    "profile",
    "protocol_version",
    "repeat",
    "run_id",
    "solution_module",
    "stage",
    "status",
    "task_id",
    "termination_reason",
    "type",
    "validation_scope",
}
_STRUCTURAL_LIST_KEYS = {
    "acceptance_criteria",
    "environment_variables_forwarded",
    "failure_scenario_ids",
    "passthrough",
    "prompt_variant_ids",
    "required",
    "task_ids",
}
_ENUM_KEY_VALUES = {
    "action": {"get_action_catalog", "pick_up", "put_down", "stack", "unstack"},
    "condition_type": {"unperturbed", "prompt", "failure"},
    "direction": {"service_to_solution", "solution_to_service"},
    "failure_kind": {"none", "solution", "infrastructure"},
    "name": {"get_action_catalog", "pick_up", "put_down", "stack", "unstack"},
    "status": {
        "aborted",
        "accepted",
        "valid",
        "invalid",
        "running",
        "completed",
        "failed",
        "ok",
        "error",
        "rejected",
        "computed",
        "failed_to_compute",
    },
    "type": {
        "task",
        "actions",
        "observation",
        "final",
        "malformed_action",
        "unknown_action",
        "precondition_failed",
        "transient_failure",
        "action_budget_exceeded",
    },
    "validation_scope": {"full", "partial"},
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


def redact_text(value: str, secret_values: set[str] | None = None) -> str:
    value = _URL_CREDENTIALS_RE.sub(r"\1<redacted>@", value)
    value = _OPTION_SECRET_RE.sub(r"\1<redacted>", value)
    value = _BEARER_RE.sub(r"\1<redacted>", value)
    value = _ASSIGNMENT_SECRET_RE.sub(r"\1<redacted>", value)
    value = _COLON_QUOTED_SECRET_RE.sub(r"\1\2<redacted>\2", value)
    value = _COLON_BARE_SECRET_RE.sub(r"\1<redacted>", value)
    for secret in sorted(secret_values or set(), key=len, reverse=True):
        if secret:
            value = value.replace(secret, "<redacted>")
    return value


def redact_solution_overrides(value: str, secret_values: set[str] | None = None) -> str:
    """Redact option values by position without corrupting unrelated numeric arguments."""
    try:
        arguments = shlex.split(value, posix=True)
    except ValueError:
        return redact_text(value, secret_values)
    redacted: list[str] = []
    redact_next = False
    preserve_next = False
    for argument in arguments:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if preserve_next:
            redacted.append(argument)
            preserve_next = False
            continue
        if argument.startswith("--") and _is_sensitive_option(argument):
            if "=" in argument:
                redacted.append(argument.split("=", 1)[0] + "=<redacted>")
            else:
                redacted.append(argument)
                redact_next = True
            continue
        if argument.startswith("--") and _is_token_count_option(argument):
            redacted.append(argument)
            if "=" not in argument:
                preserve_next = True
            continue
        redacted.append(redact_text(argument, secret_values))
    return value if redacted == arguments else shlex.join(redacted)


def redact_data(
    value: Any,
    secret_values: set[str] | None = None,
    *,
    _parent_key: str | None = None,
    trusted_structure: bool = True,
) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_RE.search(key_text):
                redacted[key_text] = "<redacted>"
            elif key_text == "solution_overrides" and isinstance(item, str):
                redacted[key_text] = redact_solution_overrides(item, secret_values)
            elif isinstance(item, str) and _protect_string(key_text, item, trusted_structure):
                redacted[key_text] = redact_text(item)
            else:
                redacted[key_text] = redact_data(
                    item,
                    secret_values,
                    _parent_key=key_text,
                    trusted_structure=trusted_structure,
                )
        return redacted
    if isinstance(value, (list, tuple)):
        return [
            redact_data(
                item,
                secret_values,
                _parent_key=_parent_key,
                trusted_structure=trusted_structure,
            )
            for item in value
        ]
    if isinstance(value, str):
        if _parent_key in _STRUCTURAL_LIST_KEYS and trusted_structure:
            return redact_text(value)
        if _parent_key == "required" and value in {"block", "other_block"}:
            return redact_text(value)
        return redact_text(value, secret_values)
    return value


def _protect_string(key: str, value: str, trusted_structure: bool) -> bool:
    if value in _ENUM_KEY_VALUES.get(key, set()):
        return True
    return trusted_structure and key in _STRUCTURAL_STRING_KEYS


def embedded_secret_values(value: str) -> set[str]:
    """Extract exact secrets from common assignments, options, bearer values, and URLs."""
    secrets = _url_userinfo(value)
    for pattern in (_ASSIGNMENT_SECRET_RE, _OPTION_SECRET_RE, _COLON_BARE_SECRET_RE):
        for match in pattern.finditer(value):
            secrets.add(match.group(2).strip("'\""))
    for match in _COLON_QUOTED_SECRET_RE.finditer(value):
        secrets.add(match.group(3))
    for match in _BEARER_RE.finditer(value):
        secrets.add(match.group(0)[len(match.group(1)):])
    return {secret for secret in secrets if secret}


def configuration_secret_values(profile: ValidationProfile) -> set[str]:
    secrets: set[str] = set()
    for value in _string_values(profile.model_dump(mode="json")):
        secrets.update(embedded_secret_values(value))
    return secrets


def collect_secret_values(
    request: ValidationRequest | None, profile: ValidationProfile
) -> set[str]:
    """Collect exact values that must not appear in public or retained evidence."""
    secrets = configuration_secret_values(profile)
    for name in (
        set(profile.environment.passthrough)
        | _PROVISIONING_ONLY_ENV_NAMES
        | _CREDENTIAL_URL_ENV_NAMES
    ):
        value = os.environ.get(name)
        if value:
            secrets.add(value)
            secrets.update(_url_userinfo(value))
    if request is not None:
        secrets.update(_url_userinfo(request.repo))
        try:
            arguments = parse_solution_overrides(request.solution_overrides)
        except InvalidRequestError:
            arguments = []
        index = 0
        while index < len(arguments):
            argument = arguments[index]
            if argument.startswith("--") and _is_sensitive_option(argument):
                if "=" in argument:
                    secrets.add(argument.split("=", 1)[1])
                elif index + 1 < len(arguments):
                    secrets.add(arguments[index + 1])
                    index += 1
            index += 1
    return {secret for secret in secrets if secret}


def _string_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)
    elif isinstance(value, str):
        yield value


def _is_sensitive_option(argument: str) -> bool:
    name = argument.split("=", 1)[0].lstrip("-")
    parts = [part for part in re.split(r"[-_]", name.lower()) if part]
    if "tokens" in parts:
        return False
    for index, part in enumerate(parts):
        if part in {"password", "passwd", "secret", "authorization"}:
            return True
        if part == "key" and index > 0 and parts[index - 1] == "api":
            return True
        if part == "token":
            previous = parts[index - 1] if index else ""
            following = parts[index + 1] if index + 1 < len(parts) else ""
            if previous not in {"input", "output", "max", "maximum"} and following not in {
                "budget",
                "count",
                "limit",
                "limits",
                "max",
                "maximum",
                "size",
                "usage",
            }:
                return True
    return False


def _is_token_count_option(argument: str) -> bool:
    name = argument.split("=", 1)[0].lstrip("-")
    parts = [part for part in re.split(r"[-_]", name.lower()) if part]
    if "tokens" in parts:
        return True
    for index, part in enumerate(parts):
        if part != "token":
            continue
        previous = parts[index - 1] if index else ""
        following = parts[index + 1] if index + 1 < len(parts) else ""
        if previous in {"input", "output", "max", "maximum"} or following in {
            "budget",
            "count",
            "limit",
            "limits",
            "max",
            "maximum",
            "size",
            "usage",
        }:
            return True
    return False


def provisioning_environment() -> dict[str, str]:
    environment = {name: os.environ[name] for name in _SYSTEM_ENV_NAMES if name in os.environ}
    environment["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return environment


def solution_environment(
    profile: ValidationProfile, virtual_environment: Path, home: Path
) -> dict[str, str]:
    allowed = _RUNTIME_SYSTEM_ENV_NAMES | set(profile.environment.passthrough)
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


def _url_userinfo(value: str) -> set[str]:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return set()
    secrets = set()
    for item in (parsed.username, parsed.password):
        if item:
            secrets.update((item, unquote(item)))
    return secrets
