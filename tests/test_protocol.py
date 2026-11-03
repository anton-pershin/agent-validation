from __future__ import annotations

from pathlib import Path

import pytest

from agent_validation.errors import SolutionProtocolError
from agent_validation.protocol import SolutionProcess


def test_deeply_nested_json_is_a_solution_protocol_error(tmp_path: Path) -> None:
    process = SolutionProcess(
        ["unused"],
        tmp_path,
        {},
        max_output_bytes=1_000_000,
        max_retained_log_bytes=4096,
        shutdown_seconds=0.1,
    )
    nested = b"[" * 2000 + b"0" + b"]" * 2000
    with pytest.raises(SolutionProtocolError):
        process._parse_line(nested)
