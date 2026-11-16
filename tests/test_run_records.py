from __future__ import annotations

import json
import subprocess
from datetime import UTC
from datetime import datetime as RealDateTime
from pathlib import Path

import pytest
from validation_helpers import configuration_for

from agent_validation.application import ValidationApplication
from agent_validation.config import load_profile
from agent_validation.contracts import (
    RunManifest,
    ValidationRequest,
    ValidationResult,
    ValidationScope,
)
from agent_validation.errors import (
    InvalidRequestError,
    ProvisioningError,
    StorageError,
)
from agent_validation.provenance import collect_validator_provenance
from agent_validation.provisioning import RevisionPreparer
from agent_validation.security import collect_secret_values, redact_data, redact_text
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


def test_scope_configuration_is_required_before_run_allocation(service_settings) -> None:
    application = ValidationApplication(service_settings)
    request = ValidationRequest(repo="/unused", commit=COMMIT)
    with pytest.raises(InvalidRequestError, match="requires a complete configuration"):
        application.validate(request, ValidationScope.PARTIAL)
    with pytest.raises(InvalidRequestError, match="does not accept"):
        application.validate(request, ValidationScope.FULL, configuration={})
    assert not service_settings.artifact_root.exists()


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
