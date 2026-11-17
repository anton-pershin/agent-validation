from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from validation_helpers import configuration_for

from agent_validation.api import create_app
from agent_validation.application import ServiceSettings
from agent_validation.contracts import ValidationScope

ROOT = Path(__file__).resolve().parents[1]


def test_partial_cli_and_http_against_real_test_solution(
    service_settings: ServiceSettings, git_solution, tmp_path: Path
) -> None:
    repo, first, _ = git_solution
    output = tmp_path / "cli-result.json"
    partial_config = tmp_path / "partial-config.json"
    partial_config.write_text(
        json.dumps(configuration_for(service_settings)), encoding="utf-8"
    )
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
            "--partial-config",
            str(partial_config),
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
            "configuration": configuration_for(service_settings),
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


def test_clean_environment_acceptance(git_solution, tmp_path: Path) -> None:
    repo, commit, _ = git_solution
    clean_venv = tmp_path / "clean-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(clean_venv)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    python = clean_venv / "bin" / "python"
    source_copy = tmp_path / "clean-source"
    shutil.copytree(
        ROOT,
        source_copy,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "artifacts",
            "build",
            "*.egg-info",
        ),
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["AGENT_VALIDATION_SOURCE_DIR"] = str(ROOT)
    environment["AGENT_VALIDATION_PROFILE_DIR"] = str(source_copy / "config" / "profiles")
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    installed = subprocess.run(
        [str(python), "-m", "pip", "install", str(source_copy)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr

    example = source_copy / "config" / "examples" / "default_partial.yaml"
    configuration = yaml.safe_load(example.read_text())
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server_environment = environment.copy()
    server_environment.update(
        {
            "AGENT_VALIDATION_ARTIFACT_DIR": str(tmp_path / "server-artifacts"),
            "AGENT_VALIDATION_TEMP_DIR": str(tmp_path / "server-temporary"),
            "AGENT_VALIDATION_HOST": "127.0.0.1",
        }
    )
    server = subprocess.Popen(
        [
            str(python),
            str(source_copy / "validation_service.py"),
            f"port={port}",
            "profile=default",
        ],
        cwd=tmp_path,
        env=server_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_http_service(server, port)
        output = tmp_path / "clean-cli-result.json"
        cli_environment = environment.copy()
        cli_environment.update(
            {
                "AGENT_VALIDATION_ARTIFACT_DIR": str(tmp_path / "cli-artifacts"),
                "AGENT_VALIDATION_TEMP_DIR": str(tmp_path / "cli-temporary"),
            }
        )
        completed = subprocess.run(
            [
                str(python),
                str(source_copy / "validation_cli_partial.py"),
                "--repo",
                str(repo),
                "--commit",
                commit,
                "--output-json",
                str(output),
                "--partial-config",
                str(example),
                "--solution-overrides",
                "--behavior success",
            ],
            cwd=tmp_path,
            env=cli_environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        cli_result = json.loads(output.read_text())
        assert cli_result["profile"] == "default"
        assert cli_result["validation_scope"] == "partial"
        assert cli_result["run_id"]
        assert len(
            (Path(cli_result["artifacts_location"]) / "episodes.jsonl")
            .read_text()
            .splitlines()
        ) == 42

        payload = json.dumps(
            {
                "repo": str(repo),
                "commit": commit,
                "solution_overrides": "--behavior success",
                "configuration": configuration,
            }
        ).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/validate_partial",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            assert response.status == 200
            http_result = json.load(response)
        assert http_result["profile"] == "default"
        assert http_result["validation_scope"] == "partial"
        assert http_result["run_id"]
        assert len(
            (Path(http_result["artifacts_location"]) / "episodes.jsonl")
            .read_text()
            .splitlines()
        ) == 42
    finally:
        server.terminate()
        try:
            server.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate(timeout=10)


def _wait_for_http_service(server: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.poll() is not None:
            _, stderr = server.communicate()
            raise AssertionError(f"HTTP service exited during startup: {stderr}")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/openapi.json", timeout=1
            ) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("HTTP service did not start within 30 seconds")


def assert_informative_names(result: dict, scope: ValidationScope) -> None:
    assert result["run_id"]
    assert Path(result["artifacts_location"]).is_dir()
    stored_result = Path(result["artifacts_location"]) / "result.json"
    assert json.loads(stored_result.read_text()) == result
    assert result["validation_scope"] == scope.value
    assert "VM1/unperturbed_overall_success_rate" in result["AC1"]["metrics_status"]
    assert "VM1" not in result["AC1"]["metrics_status"]
    assert "VM5/prompt_variant_overall_success_rate_loss/concise" in result["AC4"]["metrics_status"]
    assert (
        "VM7/transient_failure_overall_success_rate_loss/pick_up_failure"
        in result["AC5"]["metrics_status"]
    )
