"""Run-specific, auditable, redacted filesystem persistence."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contracts import EpisodeRecord, PlannedEpisode, RunManifest, ValidationResult
from .errors import RunDirectoryExistsError, StorageError
from .security import redact_data, redact_text


class RunStore:
    def __init__(
        self,
        artifact_root: Path,
        run_id: str,
        secret_values: set[str] | None = None,
    ) -> None:
        self.run_id = run_id
        self.run_directory = (artifact_root.resolve() / run_id).resolve()
        self.artifacts_location = str(self.run_directory)
        self.secret_values = secret_values or set()
        self._lock = threading.RLock()
        self._terminal = False
        try:
            self.run_directory.mkdir(parents=True, exist_ok=False)
            (self.run_directory / "episodes").mkdir()
        except FileExistsError as exc:
            raise RunDirectoryExistsError(
                f"Run evidence directory already exists: {run_id}"
            ) from exc
        except OSError as exc:
            raise StorageError(f"Cannot create run evidence directory: {exc}") from exc

    def _write_bytes(self, relative_path: str, content: bytes) -> None:
        with self._lock:
            if self._terminal:
                raise StorageError(f"Cannot modify terminal run evidence: {relative_path}")
            destination = self.run_directory / relative_path
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(
                    f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp"
                )
                temporary.write_bytes(content)
                os.replace(temporary, destination)
            except OSError as exc:
                raise StorageError(f"Cannot write evidence {relative_path}: {exc}") from exc

    def write_json(self, relative_path: str, value: Any, *, redact: bool = False) -> None:
        if redact:
            value = redact_data(value, self.secret_values)
        content = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        self._write_bytes(relative_path, content.encode("utf-8"))

    def write_run_configuration(
        self, configuration: RunManifest | dict[str, Any], *, terminal: bool = False
    ) -> None:
        if isinstance(configuration, RunManifest):
            configuration.updated_at = datetime.now(UTC).isoformat()
            value = configuration.model_dump(mode="json")
        else:
            configuration["updated_at"] = datetime.now(UTC).isoformat()
            value = configuration
        with self._lock:
            self.write_json("run.json", value, redact=True)
            if terminal:
                self._terminal = True

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
        with self._lock:
            self.write_json(f"episodes/{record.episode_id}/record.json", serialized)
            line = json.dumps(serialized, ensure_ascii=False, sort_keys=True) + "\n"
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
        run_configuration: RunManifest,
    ) -> None:
        run_configuration.artifacts.update(
            {
                "result": "result.json",
                "metrics": "metrics.json",
                "intermediates": "intermediates.json",
            }
        )
        self.write_json("result.json", result.to_response(), redact=True)
        self.write_json("metrics.json", metrics, redact=True)
        self.write_json("intermediates.json", intermediates, redact=True)
        run_configuration.status = "completed"
        run_configuration.error_message = None
        self.write_run_configuration(run_configuration, terminal=True)

    def mark_failed(self, run_configuration: RunManifest, message: str) -> None:
        run_configuration.status = "failed"
        run_configuration.error_message = redact_text(message, self.secret_values)
        self.write_run_configuration(run_configuration, terminal=True)

    def mark_aborted(self, run_configuration: RunManifest, message: str) -> None:
        run_configuration.status = "aborted"
        run_configuration.error_message = redact_text(message, self.secret_values)
        self.write_run_configuration(run_configuration, terminal=True)
