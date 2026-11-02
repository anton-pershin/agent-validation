"""Command-line presentation layer."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .application import ServiceSettings, ValidationApplication, error_details
from .contracts import ValidationRequest, ValidationScope
from .errors import ValidationServiceError


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
        help="Validation profile name",
    )
    return parser


def main(scope: ValidationScope, arguments: Sequence[str] | None = None) -> int:
    args = build_parser(scope).parse_args(arguments)
    settings = ServiceSettings.from_environment()
    application = ValidationApplication(settings)
    try:
        request = ValidationRequest(
            repo=args.repo,
            commit=args.commit,
            solution_overrides=args.solution_overrides,
        )
        result = application.validate(request, scope, args.profile)
        destination = Path(args.output_json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return 0
    except (ValidationServiceError, OSError, ValueError) as exc:
        print(json.dumps({"error": error_details(exc)}), file=sys.stderr)
        return 1


def full_main() -> int:
    return main(ValidationScope.FULL)


def partial_main() -> int:
    return main(ValidationScope.PARTIAL)
