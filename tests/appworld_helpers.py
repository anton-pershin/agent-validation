from __future__ import annotations

from pathlib import Path

from agent_validation.config import ResolvedProfile, load_profile, resolve_partial_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "config" / "profiles"


def appworld_configuration(
    task_id: str = "task_one", solution_module: str = "reliable_agent_appworld"
) -> dict:
    base = load_profile("appworld", PROFILE_DIR)
    configuration = base.profile.model_dump(mode="json")
    configuration["solution_module"] = solution_module
    configuration["tasks"] = [{"id": task_id, "problem": None, "split": "train"}]
    configuration["full"] = {
        "task_ids": [task_id],
        "repeats": 1,
        "prompt_variant_ids": [],
        "failure_scenario_ids": [],
        "failure_task_ids": {},
    }
    configuration["acceptance_criteria"] = ["AC1", "AC2", "AC7"]
    configuration["appworld"].update(
        {
            "max_interactions": 100,
            "execution_timeout_seconds": 5,
            "startup_timeout_seconds": 5,
            "evaluation_timeout_seconds": 5,
            "shutdown_timeout_seconds": 5,
        }
    )
    configuration["resources"].update(
        {
            "episode_concurrency": 1,
            "max_output_bytes": 100000,
            "process_shutdown_seconds": 0.5,
        }
    )
    return configuration


def resolved_appworld(task_id: str = "task_one") -> ResolvedProfile:
    base = load_profile("appworld", PROFILE_DIR)
    return resolve_partial_profile(base, appworld_configuration(task_id))


def assert_public_message(value: str) -> None:
    assert "PRIVATE_EVALUATION" not in value
    assert "ground_truth" not in value
