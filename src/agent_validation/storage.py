"""Run-specific, auditable, redacted filesystem persistence."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .contracts import EpisodeRecord, PlannedEpisode, ValidationResult
from .errors import StorageError
from .security import redact_data, redact_text


class RunStore:
    def __init__(
        self,
        artifact_root: Path,
        run_id: str,
        secret_values: set[str] | None = None,
    ) -> None:
        self.run_directory = artifact_root.resolve() / run_id
        self.secret_values = secret_values or set()
        self._lock = threading.Lock()
        try:
            self.run_directory.mkdir(parents=True, exist_ok=False)
            (self.run_directory / "episodes").mkdir()
        except OSError as exc:
            raise StorageError(f"Cannot create run evidence directory: {exc}") from exc

    def _write_bytes(self, relative_path: str, content: bytes) -> None:
        destination = self.run_directory / relative_path
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, destination)
        except OSError as exc:
            raise StorageError(f"Cannot write evidence {relative_path}: {exc}") from exc

    def write_json(self, relative_path: str, value: Any, *, redact: bool = False) -> None:
        if redact:
            value = redact_data(value, self.secret_values)
        content = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        self._write_bytes(relative_path, content.encode("utf-8"))

    def write_run_configuration(self, configuration: dict[str, Any]) -> None:
        self.write_json("run.json", configuration, redact=True)

    def write_planned_episodes(self, episodes: list[PlannedEpisode]) -> None:
        self.write_json(
            "planned_episodes.json",
            [episode.model_dump(mode="json") for episode in episodes],
            redact=True,
        )

    def write_provisioning_events(self, events: list[dict[str, object]]) -> None:
        self.write_json("provisioning.json", events, redact=True)

    def write_protocol(self, episode_id: str, messages: list[dict[str, Any]]) -> None:
        lines = [
            json.dumps(
                redact_data(message, self.secret_values, trusted_structure=False),
                ensure_ascii=False,
                sort_keys=True,
            )
            for message in messages
        ]
        self._write_bytes(
            f"episodes/{episode_id}/protocol.jsonl", ("\n".join(lines) + "\n").encode()
        )

    def write_raw_protocol_error(self, episode_id: str, raw_output: str) -> None:
        self._write_bytes(
            f"episodes/{episode_id}/invalid-output.txt",
            (redact_text(raw_output, self.secret_values) + "\n").encode("utf-8", errors="replace"),
        )

    def write_solution_log(self, episode_id: str, log: str) -> None:
        self._write_bytes(
            f"episodes/{episode_id}/solution-stderr.log",
            redact_text(log, self.secret_values).encode("utf-8", errors="replace"),
        )

    def append_record(self, record: EpisodeRecord) -> None:
        serialized = redact_data(record.model_dump(mode="json"), self.secret_values)
        self.write_json(f"episodes/{record.episode_id}/record.json", serialized)
        line = json.dumps(serialized, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock:
            try:
                with (self.run_directory / "episodes.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(line)
            except OSError as exc:
                raise StorageError(f"Cannot append episode record: {exc}") from exc

    def finalize(
        self,
        result: ValidationResult,
        metrics: dict[str, Any],
        intermediates: dict[str, Any],
        run_configuration: dict[str, Any],
    ) -> None:
        self.write_json("result.json", result.to_response())
        self.write_json("metrics.json", metrics)
        self.write_json("intermediates.json", intermediates)
        completed = {**run_configuration, "status": "completed", "error_message": None}
        self.write_run_configuration(completed)

    def mark_failed(self, run_configuration: dict[str, Any], message: str) -> None:
        failed = {
            **run_configuration,
            "status": "failed",
            "error_message": redact_text(message, self.secret_values),
        }
        self.write_run_configuration(failed)
