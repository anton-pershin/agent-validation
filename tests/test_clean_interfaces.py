from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from agent_validation.api import create_app
from agent_validation.application import ServiceSettings
from agent_validation.contracts import ValidationScope

ROOT = Path(__file__).resolve().parents[1]


def test_partial_cli_and_http_against_real_test_solution(
    service_settings: ServiceSettings, git_solution, tmp_path: Path
) -> None:
    repo, first, _ = git_solution
    output = tmp_path / "cli-result.json"
    environment = os.environ.copy()
    environment.update(
        {
            "AGENT_VALIDATION_PROFILE": "test",
            "AGENT_VALIDATION_PROFILE_DIR": str(service_settings.profile_dir),
            "AGENT_VALIDATION_ARTIFACT_DIR": str(service_settings.artifact_root),
            "AGENT_VALIDATION_TEMP_DIR": str(service_settings.temporary_root),
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "validation_cli_partial.py"),
            "--repo",
            str(repo),
            "--commit",
            first,
            "--output-json",
            str(output),
            "--solution-overrides",
            "--behavior success",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert_informative_names(json.loads(output.read_text()), ValidationScope.PARTIAL)

    client = TestClient(create_app(service_settings))
    response = client.post(
        "/validate_partial",
        json={
            "repo": str(repo),
            "commit": first,
            "solution_overrides": "--behavior success",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["profile"] == "test"
    assert_informative_names(response.json(), ValidationScope.PARTIAL)

    full_output = tmp_path / "cli-full-result.json"
    full_completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "validation_cli.py"),
            "--repo",
            str(repo),
            "--commit",
            first,
            "--output-json",
            str(full_output),
            "--solution-overrides",
            "--behavior success",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert full_completed.returncode == 0, full_completed.stderr
    assert_informative_names(json.loads(full_output.read_text()), ValidationScope.FULL)

    full_response = client.post(
        "/validate",
        json={
            "repo": str(repo),
            "commit": first,
            "solution_overrides": "--behavior success",
        },
    )
    assert full_response.status_code == 200, full_response.text
    assert_informative_names(full_response.json(), ValidationScope.FULL)
    assert len(list(service_settings.artifact_root.iterdir())) == 4


def assert_informative_names(result: dict, scope: ValidationScope) -> None:
    assert result["validation_scope"] == scope.value
    assert "VM1/unperturbed_overall_success_rate" in result["AC1"]["metrics_status"]
    assert "VM1" not in result["AC1"]["metrics_status"]
    assert "VM5/prompt_variant_overall_success_rate_loss/concise" in result["AC4"]["metrics_status"]
    assert (
        "VM7/transient_failure_overall_success_rate_loss/pick_up_failure"
        in result["AC5"]["metrics_status"]
    )
