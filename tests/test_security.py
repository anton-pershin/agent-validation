from __future__ import annotations

from conftest import profile_data

from agent_validation.config import ValidationProfile
from agent_validation.contracts import ValidationRequest
from agent_validation.security import (
    collect_secret_values,
    redact_data,
    redact_text,
    solution_environment,
)


def test_redaction_covers_urls_options_assignments_and_nested_keys() -> None:
    text = (
        "https://user:secret@example.invalid/repo --api-key abc123 "
        "OPENAI_API_KEY=xyz Bearer token-value"
    )
    redacted = redact_text(text)
    assert "secret" not in redacted
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "token-value" not in redacted
    data = redact_data({"api_key": "hidden", "nested": {"password": "hidden"}})
    assert data == {"api_key": "<redacted>", "nested": {"password": "<redacted>"}}


def test_solution_environment_uses_allowlist(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "allowed")
    monkeypatch.setenv("UNLISTED_SECRET", "not-allowed")
    monkeypatch.setenv("PYTHONPATH", "not-allowed")
    profile = ValidationProfile.model_validate(profile_data())
    environment = solution_environment(profile, tmp_path / "venv", tmp_path / "home")
    assert environment["OPENAI_API_KEY"] == "allowed"
    assert "UNLISTED_SECRET" not in environment
    assert "PYTHONPATH" not in environment
    assert environment["VIRTUAL_ENV"] == str(tmp_path / "venv")


def test_exact_secret_collection_and_additional_credential_forms(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "bare-provider-secret")
    profile = ValidationProfile.model_validate(profile_data())
    request = ValidationRequest(
        repo="https://token-user@example.invalid/repo",
        commit="a" * 40,
        solution_overrides="--token override-token",
    )
    secrets = collect_secret_values(request, profile)
    assert "bare-provider-secret" in secrets
    assert "token-user" in secrets
    assert "override-token" in secrets
    text = (
        "bare-provider-secret override-token "
        "https://token-user@example.invalid/repo --token override-token"
    )
    redacted = redact_text(text, secrets)
    assert "bare-provider-secret" not in redacted
    assert "override-token" not in redacted
    assert "token-user" not in redacted


def test_provisioning_only_variables_do_not_reach_solution(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PIP_INDEX_URL", "https://user:password@example.invalid/simple")
    profile = ValidationProfile.model_validate(profile_data())
    environment = solution_environment(profile, tmp_path / "venv", tmp_path / "home")
    assert "PIP_INDEX_URL" not in environment
