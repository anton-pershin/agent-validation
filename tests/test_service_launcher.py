from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent_validation.api as api_module

ROOT = Path(__file__).resolve().parents[1]


def test_http_launcher_accepts_port_and_profile(service_settings, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_VALIDATION_PROFILE_DIR", str(service_settings.profile_dir))
    monkeypatch.setenv("AGENT_VALIDATION_ARTIFACT_DIR", str(service_settings.artifact_root))
    monkeypatch.setenv("AGENT_VALIDATION_TEMP_DIR", str(service_settings.temporary_root))
    captured = {}

    def fake_run(app, host, port):
        captured.update(app=app, host=host, port=port)

    monkeypatch.setattr(api_module.uvicorn, "run", fake_run)
    monkeypatch.setenv("AGENT_VALIDATION_HOST", "127.0.0.1")
    api_module.run(["port=9123", "profile=test"])
    assert captured["port"] == 9123
    assert captured["host"] == "127.0.0.1"
    response = TestClient(captured["app"]).get("/profiles/test/configuration")
    assert response.status_code == 200
    assert response.json()["profile"] == "test"


def test_http_launcher_defaults_to_default_profile(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_VALIDATION_PROFILE_DIR", str(ROOT / "config" / "profiles"))
    monkeypatch.delenv("AGENT_VALIDATION_PROFILE", raising=False)
    captured = {}
    monkeypatch.setattr(
        api_module.uvicorn,
        "run",
        lambda app, host, port: captured.update(app=app, port=port),
    )
    api_module.run([])
    assert captured["port"] == 8000
    response = TestClient(captured["app"]).get("/profiles/default/configuration")
    assert response.status_code == 200
    assert response.json()["profile"] == "default"


def test_http_launcher_rejects_unknown_arguments(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_VALIDATION_PROFILE_DIR", str(ROOT / "config" / "profiles"))
    with pytest.raises(SystemExit, match="Unknown service argument"):
        api_module.run(["unknown"])
