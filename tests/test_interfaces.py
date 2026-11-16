from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_validation.api import create_app
from agent_validation.application import ValidationApplication
from agent_validation.cli import main
from agent_validation.contracts import ValidationRequest, ValidationScope
from agent_validation.errors import (
    InvalidRequestError,
    ProfileError,
    ProfileNotFoundError,
    ServiceCapacityError,
    bind_run_context,
)

COMMIT = "a" * 40
PAYLOAD = {"repo": "/tmp/solution", "commit": COMMIT, "solution_overrides": ""}
PARTIAL_PAYLOAD = {**PAYLOAD, "configuration": {"name": "test"}}
RESPONSE = {
    "run_id": "run-1",
    "artifacts_location": "/tmp/run-1",
    "profile": "test",
    "validation_scope": "partial",
    "AC1": {
        "status": "valid",
        "metrics_status": {
            "VM1/unperturbed_overall_success_rate": {
                "computation_status": "computed",
                "acceptance_status": "rejected",
                "computed_value": 0.5,
                "expected_value_or_threshold": ">= 0.95",
                "error_message": None,
            }
        },
    },
}
CONFIGURATION_RESPONSE = {
    "profile": "test",
    "schema": {"type": "object"},
    "configuration": {"name": "test"},
}


class ApplicationStub:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def validate(self, request, scope, profile_name=None, configuration=None):
        self.calls.append((request, scope, profile_name, configuration))
        if self.error:
            raise self.error
        return RESPONSE

    def profile_configuration(self, profile_name):
        if self.error:
            raise self.error
        return CONFIGURATION_RESPONSE


def test_http_endpoints_and_errors(service_settings) -> None:
    stub = ApplicationStub()
    client = TestClient(create_app(service_settings, stub))
    assert client.post("/validate", json=PAYLOAD).status_code == 200
    assert client.post("/validate_partial", json=PARTIAL_PAYLOAD).status_code == 200
    assert [call[1] for call in stub.calls] == [ValidationScope.FULL, ValidationScope.PARTIAL]
    assert stub.calls[-1][3] == {"name": "test"}
    discovered = client.get("/profiles/test/configuration")
    assert discovered.status_code == 200
    assert discovered.json() == CONFIGURATION_RESPONSE
    malformed = client.post("/validate", json={"repo": "x"})
    assert malformed.status_code == 400
    assert malformed.json()["error"]["type"] == "invalid_request"
    assert client.post("/validate", json={**PAYLOAD, "configuration": {}}).status_code == 400
    assert client.post("/validate_partial", json=PAYLOAD).status_code == 400


def test_http_maps_request_and_service_failures(service_settings) -> None:
    invalid = TestClient(create_app(service_settings, ApplicationStub(InvalidRequestError("bad"))))
    assert invalid.post("/validate", json=PAYLOAD).status_code == 400
    missing = TestClient(
        create_app(service_settings, ApplicationStub(ProfileNotFoundError("missing")))
    )
    assert missing.get("/profiles/missing/configuration").status_code == 404
    failed = TestClient(create_app(service_settings, ApplicationStub(ProfileError("bad profile"))))
    response = failed.post("/validate", json=PAYLOAD)
    assert response.status_code == 500
    assert response.json()["error"]["type"] == "ProfileError"
    unexpected = TestClient(
        create_app(service_settings, ApplicationStub(RuntimeError("unexpected"))),
        raise_server_exceptions=False,
    )
    response = unexpected.post("/validate", json=PAYLOAD)
    assert response.status_code == 500
    assert response.json()["error"]["type"] == "RuntimeError"


def test_real_configuration_discovery_is_complete_and_secret_free(
    service_settings, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "discovery-secret")
    client = TestClient(create_app(service_settings))
    response = client.get("/profiles/test/configuration")
    assert response.status_code == 200
    assert "discovery-secret" not in response.text
    document = response.json()
    assert document["profile"] == "test"
    assert document["configuration"]["name"] == "test"
    assert set(document["schema"]["required"]) == set(document["configuration"])
    assert client.get("/profiles/missing/configuration").status_code == 404


def test_post_creation_http_error_contains_run_identity(service_settings) -> None:
    error = ProfileError("failed after allocation")
    bind_run_context(error, "run-42", "/tmp/run-42")
    client = TestClient(create_app(service_settings, ApplicationStub(error)))
    response = client.post("/validate", json=PAYLOAD)
    assert response.status_code == 500
    assert response.json()["error"]["run_id"] == "run-42"
    assert response.json()["error"]["artifacts_location"] == "/tmp/run-42"


def test_cli_writes_result_and_uses_zero_for_valid_criteria(monkeypatch, tmp_path: Path) -> None:
    stub = ApplicationStub()
    monkeypatch.setattr("agent_validation.cli.ValidationApplication", lambda settings: stub)
    output = tmp_path / "nested" / "result.json"
    partial_config = tmp_path / "partial.json"
    partial_config.write_text("{}", encoding="utf-8")
    status = main(
        ValidationScope.PARTIAL,
        [
            "--repo",
            "/tmp/solution",
            "--commit",
            COMMIT,
            "--output-json",
            str(output),
            "--partial-config",
            str(partial_config),
            "--profile",
            "test",
        ],
    )
    assert status == 0
    assert json.loads(output.read_text()) == RESPONSE
    assert stub.calls[0][1] == ValidationScope.PARTIAL
    assert stub.calls[0][2] == "test"
    assert stub.calls[0][3] == {}


def test_cli_returns_one_when_operation_cannot_complete(monkeypatch, tmp_path: Path) -> None:
    stub = ApplicationStub(ProfileError("missing"))
    monkeypatch.setattr("agent_validation.cli.ValidationApplication", lambda settings: stub)
    status = main(
        ValidationScope.FULL,
        ["--repo", "/x", "--commit", COMMIT, "--output-json", str(tmp_path / "x.json")],
    )
    assert status == 1


def test_capacity_settings_must_be_positive(service_settings) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        replace(service_settings, max_total_episodes=0)
    with pytest.raises(ValueError, match="at least 1"):
        replace(service_settings, max_active_validations=0)


def test_service_wide_validation_capacity_is_explicit(service_settings) -> None:
    application = ValidationApplication(replace(service_settings, max_active_validations=1))
    application._validation_slots.acquire()
    try:
        with pytest.raises(ServiceCapacityError):
            application.validate(
                ValidationRequest(repo="/tmp/solution", commit=COMMIT),
                ValidationScope.PARTIAL,
            )
    finally:
        application._validation_slots.release()
