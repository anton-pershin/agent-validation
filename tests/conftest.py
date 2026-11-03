from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from agent_validation.application import ServiceSettings

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SOLUTION = ROOT / "tests" / "fixtures" / "scripted_solution"
STACK_PROBLEM = ROOT / "config" / "blocks_world" / "problems" / "stack_ab.pddl"


def profile_data(problem_path: str = "../problem.pddl") -> dict:
    return {
        "name": "test",
        "solution_module": "reliable_agent",
        "random_seed": 7,
        "original_instructions": "Solve the supplied task.",
        "tasks": [{"id": "stack_ab", "problem": problem_path}],
        "prompt_variants": [{"id": "concise", "instructions": "Complete this same task."}],
        "failure_scenarios": [
            {
                "id": "pick_up_failure",
                "action_name": "pick_up",
                "occurrence": 1,
                "error_message": "Temporary failure",
                "task_ids": ["stack_ab"],
            }
        ],
        "full": {
            "task_ids": ["stack_ab"],
            "repeats": 2,
            "prompt_variant_ids": ["concise"],
            "failure_scenario_ids": ["pick_up_failure"],
        },
        "partial": {
            "task_ids": ["stack_ab"],
            "repeats": 2,
            "prompt_variant_ids": ["concise"],
            "failure_scenario_ids": ["pick_up_failure"],
        },
        "acceptance_criteria": ["AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7"],
        "criteria": {"AC7": {"time_limit_seconds": 5, "min_timely_response_rate": 0.95}},
        "resources": {
            "episode_concurrency": 1,
            "max_output_bytes": 100000,
            "max_retained_log_bytes": 4096,
            "install_timeout_seconds": 120,
            "git_timeout_seconds": 30,
            "process_shutdown_seconds": 0.5,
        },
        "environment": {"passthrough": ["OPENAI_API_KEY"]},
    }


@pytest.fixture
def test_profile(tmp_path: Path) -> tuple[Path, dict]:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    shutil.copy(STACK_PROBLEM, tmp_path / "problem.pddl")
    data = profile_data()
    (profile_dir / "test.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return profile_dir, data


@pytest.fixture
def service_settings(test_profile, tmp_path: Path) -> ServiceSettings:
    profile_dir, _ = test_profile
    return ServiceSettings(
        profile_dir=profile_dir,
        artifact_root=tmp_path / "artifacts",
        temporary_root=tmp_path / "temporary",
        active_profile="test",
    )


@pytest.fixture
def git_solution(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "source-solution"
    shutil.copytree(FIXTURE_SOLUTION, repo)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Validation Tests")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "First scripted revision")
    first = _git(repo, "rev-parse", "HEAD")
    init_file = repo / "src" / "reliable_agent" / "__init__.py"
    init_file.write_text(
        init_file.read_text(encoding="utf-8").replace("revision-one", "revision-two"),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "Second scripted revision")
    second = _git(repo, "rev-parse", "HEAD")
    return repo, first, second


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
