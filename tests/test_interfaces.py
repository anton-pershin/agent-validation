from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_validation.api import create_app
from agent_validation.cli import main
from agent_validation.contracts import ValidationScope
from agent_validation.errors import InvalidRequestError, ProfileError

COMMIT = "a" * 40
PAYLOAD = {"repo": "/tmp/solution", "commit": COMMIT, "solution_overrides": ""}
RESPONSE = {
    "profile": "test",
    "validation_scope": "partial",
    "AC1": {
        "status": "valid",
        "metrics_status": {
            "VM1": {
                "computation_status": "computed",
                "acceptance_status": "rejected",
                "computed_value": 0.5,
                "expected_value_or_threshold": ">= 0.95",
                "error_message": None,
            }
        },
    },
}


class ApplicationStub:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def validate(self, request, scope, profile_name=None):
        self.calls.append((request, scope, profile_name))
        if self.error:
            raise self.error
        return RESPONSE


def test_http_endpoints_and_errors(service_settings) -> None:
    stub = ApplicationStub()
    client = TestClient(create_app(service_settings, stub))
    assert client.post("/validate", json=PAYLOAD).status_code == 200
    assert client.post("/validate_partial", json=PAYLOAD).status_code == 200
    assert [call[1] for call in stub.calls] == [ValidationScope.FULL, ValidationScope.PARTIAL]
    malformed = client.post("/validate", json={"repo": "x"})
    assert malformed.status_code == 400
    assert malformed.json()["error"]["type"] == "invalid_request"


def test_http_maps_request_and_service_failures(service_settings) -> None:
    invalid = TestClient(create_app(service_settings, ApplicationStub(InvalidRequestError("bad"))))
    assert invalid.post("/validate", json=PAYLOAD).status_code == 400
    failed = TestClient(create_app(service_settings, ApplicationStub(ProfileError("bad profile"))))
    response = failed.post("/validate", json=PAYLOAD)
    assert response.status_code == 500
    assert response.json()["error"]["type"] == "ProfileError"


def test_cli_writes_result_and_uses_zero_for_valid_criteria(monkeypatch, tmp_path: Path) -> None:
    stub = ApplicationStub()
    monkeypatch.setattr("agent_validation.cli.ValidationApplication", lambda settings: stub)
    output = tmp_path / "nested" / "result.json"
    status = main(
        ValidationScope.PARTIAL,
        [
            "--repo",
            "/tmp/solution",
            "--commit",
            COMMIT,
            "--output-json",
            str(output),
            "--profile",
            "test",
        ],
    )
    assert status == 0
    assert json.loads(output.read_text()) == RESPONSE
    assert stub.calls[0][1] == ValidationScope.PARTIAL
    assert stub.calls[0][2] == "test"


def test_cli_returns_one_when_operation_cannot_complete(monkeypatch, tmp_path: Path) -> None:
    stub = ApplicationStub(ProfileError("missing"))
    monkeypatch.setattr("agent_validation.cli.ValidationApplication", lambda settings: stub)
    status = main(
        ValidationScope.FULL,
        ["--repo", "/x", "--commit", COMMIT, "--output-json", str(tmp_path / "x.json")],
    )
    assert status == 1
