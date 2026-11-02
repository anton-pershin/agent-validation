from __future__ import annotations

from conftest import profile_data

from agent_validation.config import ValidationProfile
from agent_validation.security import redact_data, redact_text, solution_environment


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
