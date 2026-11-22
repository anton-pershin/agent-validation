from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_validation.appworld_session import AppWorldSession
from agent_validation.config import load_profile

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    not os.environ.get("AGENT_VALIDATION_APPWORLD_PYTHON")
    or not os.environ.get("AGENT_VALIDATION_APPWORLD_ROOT"),
    reason="Pinned real AppWorld runtime is not configured",
)
def test_real_appworld_noop_execution_evaluation_and_cleanup() -> None:
    resolved = load_profile("appworld", ROOT / "config" / "profiles")
    session = AppWorldSession(resolved, resolved.plan_episodes()[0], run_id="real-smoke")
    try:
        result = session.execute(
            {"name": "execute_code", "arguments": {"code": "pass"}}
        )
        assert result["status"] == "ok"
        # A no-op need not solve or complete the task, but private evaluation must compute.
        assert session.succeed(True) is False
    finally:
        session.close()
    evidence = session.evidence()
    assert evidence["runtime"]["package_version"] == "0.1.3.post1"
    assert evidence["runtime"]["data_version"] == "0.1.0"
    assert isinstance(evidence["evaluation_success"], bool)
    assert evidence["resources"]["peak_rss_kib"] > 0
    assert evidence["resources"]["total_cpu_seconds"] > 0
    assert evidence["output_removed"] is True
