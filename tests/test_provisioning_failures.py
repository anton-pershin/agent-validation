from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_validation.application import ServiceSettings, ValidationApplication
from agent_validation.contracts import ValidationRequest, ValidationScope
from agent_validation.errors import ProvisioningError


def test_failed_clone_preserves_failure_evidence_and_cleans_temporary_root(
    service_settings: ServiceSettings, tmp_path: Path
) -> None:
    request = ValidationRequest(
        repo=str(tmp_path / "missing-repository"),
        commit="a" * 40,
        solution_overrides="",
    )
    with pytest.raises(ProvisioningError, match="clone repository"):
        ValidationApplication(service_settings).validate(request, ValidationScope.PARTIAL)
    run_directory = next(service_settings.artifact_root.iterdir())
    configuration = json.loads((run_directory / "run.json").read_text())
    assert configuration["status"] == "failed"
    assert not Path(configuration["temporary_root"]).exists()
