"""Command-line presentation layer."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from .application import ServiceSettings, ValidationApplication, error_details
from .config import load_partial_configuration
from .contracts import ValidationRequest, ValidationScope


def build_parser(scope: ValidationScope) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Run {scope.value} validation for an exact solution commit"
    )
    parser.add_argument("--repo", required=True, help="Clonable solution repository URL or path")
    parser.add_argument("--commit", required=True, help="Full solution commit hash")
    parser.add_argument("--output-json", required=True, help="Result JSON destination")
    parser.add_argument("--solution-overrides", default="", help="Solution command arguments")
    parser.add_argument(
        "--profile",
        default=os.environ.get("AGENT_VALIDATION_PROFILE", "default"),
        help="Registered base-profile name",
    )
    if scope == ValidationScope.PARTIAL:
        parser.add_argument(
            "--partial-config",
            required=True,
            help="Complete modified base-profile configuration (YAML or JSON)",
        )
    return parser


def main(scope: ValidationScope, arguments: Sequence[str] | None = None) -> int:
    args = build_parser(scope).parse_args(arguments)
    settings = ServiceSettings.from_environment()
    application = ValidationApplication(settings)
    result: dict | None = None
    try:
        request = ValidationRequest(
            repo=args.repo,
            commit=args.commit,
            solution_overrides=args.solution_overrides,
        )
        configuration = (
            load_partial_configuration(Path(args.partial_config))
            if scope == ValidationScope.PARTIAL
            else None
        )
        result = application.validate(request, scope, args.profile, configuration)
        _write_result(result, Path(args.output_json))
        print(
            json.dumps(
                {
                    "run_id": result["run_id"],
                    "artifacts_location": result["artifacts_location"],
                }
            ),
            file=sys.stderr,
        )
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        details = error_details(exc)
        if result:
            details.setdefault("run_id", result.get("run_id"))
            details.setdefault("artifacts_location", result.get("artifacts_location"))
        print(json.dumps({"error": details}), file=sys.stderr)
        return 1


def _write_result(result: dict, destination: Path) -> None:
    current_run = Path(result["artifacts_location"]).resolve()
    source = current_run / "result.json"
    resolved_destination = destination.resolve()
    if source.exists() and source.resolve() == resolved_destination:
        return
    for parent in (resolved_destination.parent, *resolved_destination.parents):
        if (parent / "run.json").is_file():
            raise OSError(f"Refusing to overwrite validation run evidence: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    if source.exists():
        shutil.copyfile(source, temporary)
    else:
        temporary.write_text(
            json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    os.replace(temporary, destination)


def full_main() -> int:
    return main(ValidationScope.FULL)


def partial_main() -> int:
    return main(ValidationScope.PARTIAL)
