from __future__ import annotations

import json
import shlex
import subprocess
import sys
import threading
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC
from datetime import datetime as RealDateTime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from validation_helpers import configuration_for

import agent_validation.application as application_module
import agent_validation.provenance as provenance_module
import agent_validation.provisioning as provisioning_module
from agent_validation.api import create_app
from agent_validation.application import ValidationApplication
from agent_validation.cli import main as cli_main
from agent_validation.config import load_profile
from agent_validation.contracts import (
    RunManifest,
    ValidationRequest,
    ValidationResult,
    ValidationScope,
)
from agent_validation.errors import (
    InvalidRequestError,
    ProfileError,
    ProvenanceError,
    ProvisioningError,
    StorageError,
)
from agent_validation.provenance import collect_validator_provenance
from agent_validation.provisioning import RevisionPreparer
from agent_validation.security import (
    collect_secret_values,
    embedded_secret_values,
    redact_data,
    redact_text,
)
from agent_validation.storage import RunStore

COMMIT = "a" * 40


def test_manifest_exists_before_provisioning_and_failure_keeps_identity(
    service_settings, monkeypatch
) -> None:
    observed: dict = {}

    def stop_before_clone(revision: RevisionPreparer):
        run_directory = next(service_settings.artifact_root.iterdir())
        manifest = json.loads((run_directory / "run.json").read_text())
        observed.update(manifest)
        assert manifest["status"] == "running"
        assert manifest["run_id"] == run_directory.name
        assert manifest["artifacts_location"] == str(run_directory)
        assert manifest["implementation"]["validator"]["revision"]
        assert manifest["artifacts"]["episode_records"] == "episodes.jsonl"
        assert manifest["artifacts"]["episode_artifacts"] == "episodes"
        assert "fastapi" in manifest["implementation"]["dependencies"]
        assert "uvicorn" in manifest["implementation"]["dependencies"]
        raise ProvisioningError("stop before clone")

    monkeypatch.setattr(RevisionPreparer, "__enter__", stop_before_clone)
    request = ValidationRequest(repo="/unused", commit=COMMIT)
    with pytest.raises(ProvisioningError, match="stop before clone") as caught:
        ValidationApplication(service_settings).validate(
            request,
            ValidationScope.PARTIAL,
            configuration=configuration_for(service_settings),
        )
    assert caught.value.run_id == observed["run_id"]
    assert caught.value.artifacts_location == observed["artifacts_location"]
    terminal = json.loads(
        (Path(observed["artifacts_location"]) / "run.json").read_text()
    )
    assert terminal["status"] == "failed"
    assert terminal["resolved_configuration_sha256"]
    assert not (Path(terminal["artifacts_location"]) / "result.json").exists()


def test_keyboard_interrupt_marks_run_aborted(service_settings, monkeypatch) -> None:
    def abort(revision: RevisionPreparer):
        raise KeyboardInterrupt("interrupted for test")

    monkeypatch.setattr(RevisionPreparer, "__enter__", abort)
    request = ValidationRequest(repo="/unused", commit=COMMIT)
    with pytest.raises(KeyboardInterrupt) as caught:
        ValidationApplication(service_settings).validate(
            request,
            ValidationScope.PARTIAL,
            configuration=configuration_for(service_settings),
        )
    run_directory = next(service_settings.artifact_root.iterdir())
    manifest = json.loads((run_directory / "run.json").read_text())
    assert manifest["status"] == "aborted"
    assert manifest["artifacts"]["episode_records"] == "episodes.jsonl"
    assert caught.value.run_id == run_directory.name
    assert caught.value.artifacts_location == str(run_directory)


def test_terminal_run_rejects_writes_and_later_run_preserves_it(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "artifacts", "terminal-run")
    manifest = _manifest(store)
    store.write_run_configuration(manifest)
    result = ValidationResult(
        run_id=store.run_id,
        artifacts_location=store.artifacts_location,
        profile="test",
        validation_scope=ValidationScope.PARTIAL,
        criteria={},
    )
    store.finalize(result, {}, {}, manifest)
    before = _evidence_bytes(store.run_directory)
    assert json.loads((store.run_directory / "result.json").read_text()) == result.to_response()
    with pytest.raises(StorageError, match="terminal"):
        store.write_json("late.json", {"not": "allowed"})
    with pytest.raises(StorageError, match="terminal"):
        store.mark_failed(manifest, "late failure")
    later = RunStore(tmp_path / "artifacts", "later-run")
    later.write_json("other.json", {"run": "later"})
    assert _evidence_bytes(store.run_directory) == before


def test_run_id_collision_is_retried(service_settings, monkeypatch) -> None:
    class FixedDateTime:
        @classmethod
        def now(cls, timezone):
            return RealDateTime(2026, 1, 1, tzinfo=timezone)

    class Identifier:
        def __init__(self, value: str) -> None:
            self.hex = value

    values = iter([Identifier("a" * 12), Identifier("b" * 12)])
    monkeypatch.setattr("agent_validation.application.datetime", FixedDateTime)
    monkeypatch.setattr("agent_validation.application.uuid4", lambda: next(values))
    first_id = "20260101T000000-000000Z-" + "a" * 12
    RunStore(service_settings.artifact_root, first_id)
    store = ValidationApplication(service_settings)._allocate_run(set())
    assert store.run_id == "20260101T000000-000000Z-" + "b" * 12
    assert store.run_id != first_id


def test_dirty_validator_provenance_includes_all_change_kinds(tmp_path: Path) -> None:
    repository = tmp_path / "validator"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "Validation Tests")
    tracked = repository / "tracked.py"
    tracked.write_text("value = 'clean'\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "Initial")
    revision = _git(repository, "rev-parse", "HEAD")
    tracked.write_text("value = 'staged'\n", encoding="utf-8")
    _git(repository, "add", "tracked.py")
    tracked.write_text("value = 'unstaged'\n", encoding="utf-8")
    (repository / "untracked.py").write_text("untracked = True\n", encoding="utf-8")
    provenance = collect_validator_provenance(repository)
    assert provenance["revision"] == revision
    assert provenance["clean"] is False
    assert "staged" in provenance["diff"]
    assert "unstaged" in provenance["diff"]
    assert "untracked = True" in provenance["diff"]
    assert "untracked.py" in provenance["changes"]


def test_secret_redaction_preserves_token_count_settings(service_settings) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    request = ValidationRequest(
        repo="/tmp/solution",
        commit=COMMIT,
        solution_overrides="--plan-max-output-tokens 3000 --api-key actual-secret",
    )
    secrets = collect_secret_values(request, resolved.profile)
    assert "actual-secret" in secrets
    assert "3000" not in secrets
    redacted = redact_text(request.solution_overrides, secrets)
    assert "3000" in redacted
    assert "actual-secret" not in redacted
    assert redact_data({"max_output_tokens": 3000}) == {"max_output_tokens": 3000}
    assert redact_data(
        {"solution_overrides": "--plan-max-output-tokens 3000"}, {"3"}
    ) == {"solution_overrides": "--plan-max-output-tokens 3000"}
    overlapping = redact_data(
        {"solution_overrides": "--plan-max-output-tokens 3000 --api-key 3000"},
        {"3000"},
    )
    assert shlex.split(overlapping["solution_overrides"]) == [
        "--plan-max-output-tokens",
        "3000",
        "--api-key",
        "<redacted>",
    ]
    label = redact_data(
        {"solution_overrides": "--label provider-secret --plan-max-output-tokens 3000"},
        {"provider-secret", "3"},
    )["solution_overrides"]
    assert "provider-secret" not in label
    assert "3000" in label
    for text, expected in (
        ("api_key: yaml-secret", "yaml-secret"),
        ('{"api_key": "json-secret"}', "json-secret"),
    ):
        assert expected in embedded_secret_values(text)
        assert expected not in redact_text(text, embedded_secret_values(text))


def test_scope_configuration_is_required_before_run_allocation(service_settings) -> None:
    application = ValidationApplication(service_settings)
    request = ValidationRequest(repo="/unused", commit=COMMIT)
    with pytest.raises(InvalidRequestError, match="requires a complete configuration"):
        application.validate(request, ValidationScope.PARTIAL)
    with pytest.raises(InvalidRequestError, match="does not accept"):
        application.validate(request, ValidationScope.FULL, configuration={})
    malformed = ValidationRequest(
        repo="/unused", commit=COMMIT, solution_overrides="'unterminated"
    )
    with pytest.raises(InvalidRequestError, match="Cannot parse solution_overrides"):
        application.validate(
            malformed,
            ValidationScope.PARTIAL,
            configuration=configuration_for(service_settings),
        )
    assert not service_settings.artifact_root.exists()


def test_public_http_and_cli_errors_use_run_scoped_redaction(
    service_settings, monkeypatch, tmp_path: Path, capsys
) -> None:
    secret = "run-scoped-secret"
    configuration_secret = "config-embedded-secret"

    def fail_provisioning(revision: RevisionPreparer):
        raise ProvisioningError(f"clone failed with {secret} and {configuration_secret}")

    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setattr(RevisionPreparer, "__enter__", fail_provisioning)
    configuration = configuration_for(service_settings)
    configuration["original_instructions"] = (
        '{"api_key": "config-embedded-secret"}'
    )
    request = {
        "repo": "/unused",
        "commit": COMMIT,
        "configuration": configuration,
    }
    response = TestClient(create_app(service_settings)).post(
        "/validate_partial", json=request
    )
    assert response.status_code == 500
    assert secret not in response.text
    assert configuration_secret not in response.text
    assert response.json()["error"]["run_id"]

    environment = {
        "AGENT_VALIDATION_PROFILE": "test",
        "AGENT_VALIDATION_PROFILE_DIR": str(service_settings.profile_dir),
        "AGENT_VALIDATION_ARTIFACT_DIR": str(tmp_path / "cli-artifacts"),
        "AGENT_VALIDATION_TEMP_DIR": str(tmp_path / "cli-temporary"),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    partial_path = tmp_path / "partial.json"
    partial_path.write_text(json.dumps(request["configuration"]), encoding="utf-8")
    status = cli_main(
        ValidationScope.PARTIAL,
        [
            "--repo",
            "/unused",
            "--commit",
            COMMIT,
            "--output-json",
            str(tmp_path / "result.json"),
            "--partial-config",
            str(partial_path),
        ],
    )
    assert status == 1
    error_output = capsys.readouterr().err
    assert secret not in error_output
    assert configuration_secret not in error_output
    assert "run_id" in error_output
    assert "artifacts_location" in error_output
    for artifact_root in (service_settings.artifact_root, tmp_path / "cli-artifacts"):
        for path in artifact_root.rglob("*"):
            if path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace")
                assert secret not in content, path
                assert configuration_secret not in content, path


@pytest.mark.parametrize(
    "assignment", ["api_key: diff-secret", '{"api_key": "diff-secret"}']
)
def test_validator_diff_secret_is_redacted_from_public_and_retained_evidence(
    service_settings, tmp_path: Path, monkeypatch, assignment: str
) -> None:
    repository = tmp_path / "validator-with-secret"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "Validation Tests")
    source = repository / "source.py"
    source.write_text("value = 'clean'\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "Initial")
    source.write_text(assignment + "\n", encoding="utf-8")
    settings = replace(service_settings, validator_source_dir=repository)

    def fail(revision: RevisionPreparer):
        raise ProvisioningError("diff-secret")

    monkeypatch.setattr(RevisionPreparer, "__enter__", fail)
    response = TestClient(create_app(settings)).post(
        "/validate_partial",
        json={
            "repo": "/unused",
            "commit": COMMIT,
            "configuration": configuration_for(settings),
        },
    )
    assert response.status_code == 500
    assert "diff-secret" not in response.text
    for path in settings.artifact_root.rglob("*"):
        if path.is_file():
            assert "diff-secret" not in path.read_text(
                encoding="utf-8", errors="replace"
            ), path


def test_missing_problem_after_validation_is_run_aware(service_settings) -> None:
    configuration = configuration_for(service_settings)
    configuration["tasks"][0]["problem"] = "missing.pddl"
    with pytest.raises(ProfileError, match="Cannot read problem") as caught:
        ValidationApplication(service_settings).validate(
            ValidationRequest(repo="/unused", commit=COMMIT),
            ValidationScope.PARTIAL,
            configuration=configuration,
        )
    assert caught.value.run_id
    manifest = json.loads(
        (Path(caught.value.artifacts_location) / "run.json").read_text()
    )
    assert manifest["status"] == "failed"
    assert manifest["artifacts"]["episode_records"] == "episodes.jsonl"
    assert manifest["artifacts"]["episode_artifacts"] == "episodes"


def test_explicit_invalid_validator_source_does_not_fall_back(tmp_path: Path) -> None:
    with pytest.raises(ProvenanceError, match="configured source"):
        collect_validator_provenance(tmp_path)


def test_unrelated_working_directory_is_not_validator_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _git(unrelated, "init", "-q")
    _git(unrelated, "config", "user.email", "tests@example.invalid")
    _git(unrelated, "config", "user.name", "Validation Tests")
    (unrelated / "file.txt").write_text("unrelated", encoding="utf-8")
    _git(unrelated, "add", ".")
    _git(unrelated, "commit", "-qm", "Unrelated")
    unrelated_revision = _git(unrelated, "rev-parse", "HEAD")
    monkeypatch.chdir(unrelated)
    provenance = collect_validator_provenance()
    assert provenance["revision"] != unrelated_revision
    module_root = Path(__file__).resolve().parents[1]
    assert Path(provenance["repository"]) == module_root


def test_installed_package_inside_unrelated_repository_is_not_source(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "solution-with-venv"
    repository.mkdir()
    _git(repository, "init", "-q")
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "unrelated-solution"\n', encoding="utf-8"
    )
    installed = (
        repository
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "agent_validation"
        / "provenance.py"
    )
    installed.parent.mkdir(parents=True)
    installed.write_text("# installed copy", encoding="utf-8")
    monkeypatch.setattr(provenance_module, "__file__", str(installed))
    monkeypatch.chdir(repository)
    with pytest.raises(ProvenanceError, match="running source"):
        collect_validator_provenance()


def test_concurrent_interrupt_cancels_pending_episodes(service_settings, monkeypatch) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    resolved.profile.resources.episode_concurrency = 2

    class FakeExecutor:
        def __init__(self) -> None:
            self.cancel_event = threading.Event()
            self.started_event = threading.Event()
            self.started = 0
            self.lock = threading.Lock()

        def execute(self, episode):
            with self.lock:
                if self.cancel_event.is_set():
                    return object()
                self.started += 1
                if self.started == 2:
                    self.started_event.set()
            self.cancel_event.wait(5)
            return object()

        def cancel(self) -> None:
            self.cancel_event.set()

    executor = FakeExecutor()
    monkeypatch.setattr(
        application_module,
        "EpisodeExecutor",
        lambda resolved, revision, store: executor,
    )

    def interrupt_result(future):
        assert executor.started_event.wait(2)
        raise KeyboardInterrupt

    monkeypatch.setattr(Future, "result", interrupt_result)
    application = ValidationApplication(service_settings)
    with pytest.raises(KeyboardInterrupt):
        application._execute_episodes(
            resolved,
            object(),
            object(),
            [object() for _ in range(20)],
        )
    assert executor.cancel_event.is_set()
    assert executor.started == 2


def test_interrupt_during_provisioning_terminates_process_tree(
    service_settings, tmp_path: Path, monkeypatch
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    revision = RevisionPreparer(
        "/unused", COMMIT, "", resolved, tmp_path / "temporary"
    )
    terminated = threading.Event()
    original = provisioning_module.terminate_process_tree

    def tracked(*args, **kwargs):
        terminated.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(provisioning_module, "terminate_process_tree", tracked)

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(provisioning_module.time, "sleep", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            revision._run(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                "interrupt test",
                30,
            )
        assert terminated.is_set()
    finally:
        revision.close()


def _manifest(store: RunStore) -> RunManifest:
    now = RealDateTime.now(UTC).isoformat()
    return RunManifest(
        run_id=store.run_id,
        artifacts_location=store.artifacts_location,
        created_at=now,
        updated_at=now,
        requested_repo="/tmp/solution",
        requested_commit=COMMIT,
        solution_overrides="",
        base_profile="test",
        profile_path="/tmp/test.yaml",
        profile_sha256="base",
        resolved_configuration={"name": "test"},
        resolved_configuration_sha256="effective",
        validation_scope=ValidationScope.PARTIAL,
        random_seed=0,
        temporary_root="/tmp/temporary",
        implementation={},
        environment_variables_forwarded=[],
    )


def _evidence_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
