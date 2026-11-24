from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_validation.appworld_session import AppWorldSession
from agent_validation.config import load_profile
from agent_validation.storage import RunStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    not os.environ.get("AGENT_VALIDATION_APPWORLD_PYTHON")
    or not os.environ.get("AGENT_VALIDATION_APPWORLD_ROOT"),
    reason="Pinned real AppWorld runtime is not configured",
)
def test_real_appworld_noop_execution_evaluation_and_cleanup(tmp_path: Path) -> None:
    resolved = load_profile("appworld", ROOT / "config" / "profiles")
    planned = resolved.plan_episodes()[0]
    session = AppWorldSession(resolved, planned, run_id="real-smoke")
    try:
        result = session.execute(
            {"name": "execute_code", "arguments": {"code": "pass"}}
        )
        assert result["status"] == "ok"
        # The standard AppWorld guards alone allowed this read. Landlock must deny it.
        private_read = (
            "from pathlib import Path\n"
            "try:\n"
            f"    value = Path('data/tasks/{planned.task_id}/ground_truth/answer.json')"
            ".read_text()\n"
            "    print('PRIVATE_READ_OK', len(value))\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__)\n"
        )
        denied = session.execute(
            {"name": "execute_code", "arguments": {"code": private_read}}
        )
        assert denied["observation"]["output"].strip() == "PermissionError"
        assert "PRIVATE_READ_OK" not in denied["observation"]["output"]
        # A no-op need not solve or complete the task, but private evaluation must compute.
        assert session.succeed(True) is False
    finally:
        session.close()
    store = RunStore(tmp_path / "artifacts", "real-appworld-smoke")
    store.write_benchmark_evidence("episode", session.evidence(), session.worker_log())
    retained = json.loads(
        (store.run_directory / "episodes" / "episode" / "benchmark.json").read_text()
    )
    assert retained["runtime"]["package_version"] == "0.1.3.post1"
    assert retained["runtime"]["data_version"] == "0.1.0"
    assert retained["runtime"]["landlock_abi"] >= 1
    assert isinstance(retained["evaluation_success"], bool)
    assert retained["resources"]["peak_rss_kib"] > 0
    assert retained["resources"]["total_cpu_seconds"] > 0
    assert retained["evaluation_resources"]["peak_rss_kib"] > 0
    assert retained["output_removed"] is True
