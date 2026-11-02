# Agent validation service

This service evaluates an exact solution commit against a validation profile. The `default` profile evaluates `reliable-agent` with PDDL Blocks World tasks, repeated runs, prompt variants, and transient action failures. It computes VM1–VM10 and evaluates AC1–AC7.

## Requirements

- Python 3.11 or later
- Git
- Network access to install solution dependencies and call the solution's model provider, when required

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run commands from the repository root, or set `AGENT_VALIDATION_PROFILE_DIR` to the absolute path of `config/profiles`.

## CLI validation

Use a full 40- or 64-character commit hash:

```bash
.venv/bin/python validation_cli_partial.py \
  --repo https://github.com/anton-pershin/reliable-agent.git \
  --commit FULL_COMMIT_HASH \
  --output-json artifacts/partial-result.json
```

For full validation:

```bash
.venv/bin/python validation_cli.py \
  --repo https://github.com/anton-pershin/reliable-agent.git \
  --commit FULL_COMMIT_HASH \
  --output-json artifacts/full-result.json \
  --solution-overrides '--model openai:gpt-5' \
  --profile default
```

Both commands return status 0 when the service produces a validation result, including a result with `valid` or `invalid` criteria. They return status 1 when the service cannot complete the operation. Partial results are diagnostic and do not certify acceptance.

## HTTP validation

Start the service:

```bash
AGENT_VALIDATION_PROFILE=default .venv/bin/agent-validation-api
```

Send a request:

```bash
curl -sS -X POST http://127.0.0.1:8000/validate_partial \
  -H 'Content-Type: application/json' \
  -d '{
    "repo": "https://github.com/anton-pershin/reliable-agent.git",
    "commit": "FULL_COMMIT_HASH",
    "solution_overrides": ""
  }'
```

Use `/validate` for full validation and `/validate_partial` for partial validation. A completed validation returns HTTP 200. A malformed request returns HTTP 400. A request-level failure returns HTTP 500 with a JSON error object.

## Solution contract

The service clones the repository into a temporary directory, checks out and verifies the exact commit, creates a fresh virtual environment, and installs the solution without editable mode. It starts `python -m reliable_agent` once for each episode. Arguments from `solution_overrides` are parsed without a shell.

The process communicates through versioned JSON Lines on standard input and output. Standard output carries protocol messages only. Standard error carries solution logs. See [the solution interface](docs/solution-interface.md).

## Profiles and environment

Profiles are YAML files in `config/profiles`. A profile defines task files, conditions, repeats, thresholds, time and action limits, resource limits, and environment variables forwarded to the solution. Problem paths are relative to the profile file.

Configuration environment variables:

- `AGENT_VALIDATION_PROFILE`: default profile name; initially `default`.
- `AGENT_VALIDATION_PROFILE_DIR`: profile directory; initially `config/profiles` under the working directory.
- `AGENT_VALIDATION_ARTIFACT_DIR`: evidence root; initially `artifacts/runs`.
- `AGENT_VALIDATION_TEMP_DIR`: parent for temporary resources; initially `/tmp`.
- `AGENT_VALIDATION_HOST` and `AGENT_VALIDATION_PORT`: HTTP listen address; initially `127.0.0.1:8000`.

Only provider variables named in the profile and required system variables reach the solution. Do not put credentials in the repository URL or `solution_overrides`. The service redacts common credential forms from retained configuration and logs.

## Evidence and cleanup

Each validation has a unique evidence directory containing:

- `run.json`: resolved profile, commits, scope, versions, and run status;
- `planned_episodes.json` and `episodes.jsonl`: the complete population and observations;
- `episodes/<id>/`: protocol messages, solution logs, and the episode record;
- `metrics.json` and `intermediates.json`: values and calculation evidence;
- `result.json`: the common validation result.

The evidence directory remains after success or failure. Temporary checkouts, virtual environments, and workspaces are removed. Solution process groups are terminated after each episode.

## Tests

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

The tests use a local scripted solution and do not call a model provider.
