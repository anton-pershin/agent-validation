from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from validation_helpers import configuration_for

from agent_validation.application import ServiceSettings, ValidationApplication
from agent_validation.config import load_profile
from agent_validation.contracts import ValidationRequest, ValidationScope
from agent_validation.errors import ProvisioningError
from agent_validation.provisioning import RevisionPreparer


def test_solution_command_expands_repeat_in_override_arguments(
    service_settings: ServiceSettings, tmp_path: Path
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    revision = RevisionPreparer(
        "/unused",
        "a" * 40,
        "--seed {repeat} --label=repeat-{repeat} --temperature 0.7",
        resolved,
        tmp_path / "temporary",
    )
    (revision.virtual_environment / "bin").mkdir(parents=True)
    try:
        for repeat in (1, 2, 10):
            command = revision.solution_command("episode", repeat)
            assert command[1:] == [
                "-m",
                "reliable_agent",
                "--seed",
                str(repeat),
                f"--label=repeat-{repeat}",
                "--temperature",
                "0.7",
            ]
    finally:
        revision.close()


def test_failed_clone_preserves_failure_evidence_and_cleans_temporary_root(
    service_settings: ServiceSettings, tmp_path: Path
) -> None:
    request = ValidationRequest(
        repo=str(tmp_path / "missing-repository"),
        commit="a" * 40,
        solution_overrides="",
    )
    with pytest.raises(ProvisioningError, match="clone repository"):
        ValidationApplication(service_settings).validate(
            request,
            ValidationScope.PARTIAL,
            configuration=configuration_for(service_settings),
        )
    run_directory = next(service_settings.artifact_root.iterdir())
    configuration = json.loads((run_directory / "run.json").read_text())
    assert configuration["status"] == "failed"
    assert not Path(configuration["temporary_root"]).exists()


def test_install_failure_preserves_verified_commit_and_provisioning_evidence(
    service_settings: ServiceSettings, git_solution
) -> None:
    repo, _, _ = git_solution
    project = repo / "pyproject.toml"
    project.write_text(
        project.read_text().replace(
            'dependencies = ["typing-extensions>=4.8"]',
            'dependencies = ["missing-dep @ file:///definitely/missing"]',
        )
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "Broken dependencies"], check=True)
    broken = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with pytest.raises(ProvisioningError, match="install solution dependencies"):
        ValidationApplication(service_settings).validate(
            ValidationRequest(repo=str(repo), commit=broken),
            ValidationScope.PARTIAL,
            configuration=configuration_for(service_settings),
        )
    run_directory = next(service_settings.artifact_root.iterdir())
    configuration = json.loads((run_directory / "run.json").read_text())
    assert configuration["verified_commit"] == broken
    assert configuration["status"] == "failed"
    assert (run_directory / "provisioning.json").exists()
    assert not Path(configuration["temporary_root"]).exists()


def test_provisioning_output_limit_is_enforced_even_for_fast_processes(
    service_settings: ServiceSettings, tmp_path: Path
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    resolved.profile.resources.max_provisioning_output_bytes = 1024
    revision = RevisionPreparer("/unused", "a" * 40, "", resolved, tmp_path / "temporary")
    with pytest.raises(ProvisioningError, match="Output limit exceeded"):
        revision._run([sys.executable, "-c", "print('x' * 4096)"], "test output", 5)
    revision.close()


def test_provisioning_timeout_kills_escaped_descendants(
    service_settings: ServiceSettings, tmp_path: Path
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    revision = RevisionPreparer("/unused", "a" * 40, "", resolved, tmp_path / "temporary")
    child = (
        "import os,signal,time; os.setsid(); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)  # agent-validation-provisioning-child"
    )
    command = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    )
    with pytest.raises(ProvisioningError, match="Timed out"):
        revision._run([sys.executable, "-c", command], "test timeout", 0.1)
    revision.close()
    children = []
    for command_file in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            content = command_file.read_bytes()
        except OSError:
            continue
        if b"agent-validation-provisioning-child" in content:
            children.append(command_file)
    assert not children


def test_provisioning_kills_escaped_child_when_parent_exits_first(
    service_settings: ServiceSettings, tmp_path: Path
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    revision = RevisionPreparer("/unused", "a" * 40, "", resolved, tmp_path / "temporary")
    child = (
        "import os,signal,time; os.setsid(); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)  # agent-validation-provisioning-child"
    )
    command = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    )
    revision._run([sys.executable, "-c", command], "test parent exit", 5)
    revision.close()
    children = []
    for command_file in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            content = command_file.read_bytes()
        except OSError:
            continue
        if b"agent-validation-provisioning-child" in content:
            children.append(command_file)
    assert not children


def test_cleanup_failure_is_reported(
    service_settings: ServiceSettings, tmp_path: Path, monkeypatch
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    revision = RevisionPreparer("/unused", "a" * 40, "", resolved, tmp_path / "temporary")

    def fail_remove(path):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr("agent_validation.provisioning.shutil.rmtree", fail_remove)
    revision.close()
    assert "simulated cleanup failure" in revision.cleanup_error


def test_episode_cleanup_removes_only_episode_resources(
    service_settings: ServiceSettings, tmp_path: Path
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    revision = RevisionPreparer("/unused", "a" * 40, "", resolved, tmp_path / "temporary")
    episode_id = "episode-to-clean"
    parents = (revision.episode_runtimes, revision.homes, revision.workspaces)
    revision.checkout.mkdir()
    revision.virtual_environment.mkdir()
    for parent in parents:
        parent.mkdir()
        (parent / episode_id).mkdir()
    revision._episode_venvs[episode_id] = revision.episode_runtimes / episode_id

    try:
        revision.cleanup_episode(episode_id)
        assert all(not (parent / episode_id).exists() for parent in parents)
        assert all(parent.exists() for parent in parents)
        assert revision.checkout.exists()
        assert revision.virtual_environment.exists()
        assert episode_id not in revision._episode_venvs
        assert revision.cleanup_error is None

        revision.cleanup_episode(episode_id)
        assert revision.cleanup_error is None

        partial_id = "episode-with-partial-setup"
        (revision.workspaces / partial_id).mkdir()
        revision.cleanup_episode(partial_id)
        assert not (revision.workspaces / partial_id).exists()
        assert revision.cleanup_error is None
    finally:
        revision.close()


def test_episode_cleanup_failure_is_recorded_and_other_removals_continue(
    service_settings: ServiceSettings, tmp_path: Path, monkeypatch
) -> None:
    resolved = load_profile("test", service_settings.profile_dir)
    revision = RevisionPreparer("/unused", "a" * 40, "", resolved, tmp_path / "temporary")
    episode_id = "episode-with-cleanup-failure"
    parents = (revision.episode_runtimes, revision.homes, revision.workspaces)
    for parent in parents:
        parent.mkdir()
        (parent / episode_id).mkdir()
    runtime = revision.episode_runtimes / episode_id
    revision._episode_venvs[episode_id] = runtime
    original_rmtree = shutil.rmtree

    def fail_runtime(path, *args, **kwargs):
        if Path(path) == runtime:
            raise OSError("simulated episode cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("agent_validation.provisioning.shutil.rmtree", fail_runtime)
    try:
        revision.cleanup_episode(episode_id)
        assert runtime.exists()
        assert not (revision.homes / episode_id).exists()
        assert not (revision.workspaces / episode_id).exists()
        assert episode_id not in revision._episode_venvs
        assert episode_id in revision.cleanup_error
        assert "simulated episode cleanup failure" in revision.cleanup_error
    finally:
        revision.close()


def test_episode_cleanup_failure_preserves_episode_evidence(
    service_settings: ServiceSettings, git_solution, monkeypatch
) -> None:
    configuration = configuration_for(service_settings)
    configuration["full"] = {
        "task_ids": ["stack_ab"],
        "repeats": 1,
        "prompt_variant_ids": [],
        "failure_scenario_ids": [],
        "failure_task_ids": {},
    }
    repo, commit, _ = git_solution
    original_rmtree = shutil.rmtree
    failed = False

    def fail_first_episode_runtime(path, *args, **kwargs):
        nonlocal failed
        candidate = Path(path)
        if not failed and candidate.parent.name == "episode-runtimes":
            failed = True
            raise OSError("simulated episode cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        "agent_validation.provisioning.shutil.rmtree", fail_first_episode_runtime
    )
    request = ValidationRequest(
        repo=str(repo), commit=commit, solution_overrides="--behavior success"
    )
    with pytest.raises(ProvisioningError, match="simulated episode cleanup failure"):
        ValidationApplication(service_settings).validate(
            request, ValidationScope.PARTIAL, configuration=configuration
        )

    run_directory = next(service_settings.artifact_root.iterdir())
    configuration = json.loads((run_directory / "run.json").read_text())
    assert configuration["status"] == "failed"
    assert "simulated episode cleanup failure" in configuration["cleanup_error"]
    assert not Path(configuration["temporary_root"]).exists()
    record = json.loads((run_directory / "episodes.jsonl").read_text().strip())
    episode_evidence = run_directory / "episodes" / record["episode_id"]
    assert record["episode_id"] in configuration["cleanup_error"]
    assert (episode_evidence / "record.json").exists()
    assert (episode_evidence / "protocol.jsonl").exists()
    assert (episode_evidence / "solution-stderr.log").exists()
