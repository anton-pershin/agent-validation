# Agent validation service

This service evaluates an exact solution commit against a validation profile. The `default` profile evaluates `reliable-agent` with PDDL Blocks World tasks, repeated runs, prompt variants, and transient action failures. It computes VM1–VM10 and evaluates AC1–AC7. Metric keys combine stable VM identifiers with informative names, such as `VM1/unperturbed_overall_success_rate` and `VM5/prompt_variant_overall_success_rate_loss/concise`. VM2 and VM3 are computed as `1.0` when their denominators are zero; retained evidence lists these instances in `vacuous_metrics`.

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

The service clones the repository into a temporary directory, checks out and verifies the exact commit, creates a fresh virtual environment, and installs the solution without editable mode. It starts `python -m reliable_agent` once for each episode. Arguments from `solution_overrides` are parsed without a shell. The `{repeat}` substring is replaced with the episode's one-based repeat number, so `--seed {repeat}` supplies a different seed for each repeat.

The process communicates through versioned JSON Lines on standard input and output. Standard output carries protocol messages only. Standard error carries solution logs. See [the solution interface](docs/solution-interface.md).

## Profiles and environment

Profiles are YAML files in `config/profiles`. A profile defines task files, conditions, repeats, thresholds, time and action limits, resource limits, and environment variables forwarded to the solution. Problem paths are relative to the profile file. The `default` full profile has 20 distinct tasks and 10 repeats per condition, producing 890 episodes. The partial profile has the former five-task, two-repeat population with 42 episodes. `pick_up_failure` is eligible for all 20 full tasks and retains its original four tasks in partial validation. `unstack_failure` is eligible for nine stacked tasks.

Configuration environment variables:

- `AGENT_VALIDATION_PROFILE`: default profile name; initially `default`.
- `AGENT_VALIDATION_PROFILE_DIR`: profile directory; initially `config/profiles` under the working directory.
- `AGENT_VALIDATION_ARTIFACT_DIR`: evidence root; initially `artifacts/runs`.
- `AGENT_VALIDATION_TEMP_DIR`: parent for temporary resources; initially `/tmp`.
- `AGENT_VALIDATION_MAX_ACTIVE`: maximum active validations per application; initially `4`.
- `AGENT_VALIDATION_MAX_EPISODES`: maximum concurrent episodes per application; initially `32`.
- `AGENT_VALIDATION_HOST` and `AGENT_VALIDATION_PORT`: HTTP listen address; initially `127.0.0.1:8000`.

Only provider variables named in the profile and required system variables reach the solution. Do not put credentials in the repository URL or `solution_overrides`. The service redacts common credential forms from retained configuration and logs.

## Evidence and cleanup

Each validation has a unique evidence directory containing:

- `run.json`: resolved profile, commits, scope, versions, and run status;
- `planned_episodes.json` and `episodes.jsonl`: the complete population and observations;
- `provisioning.json`: bounded provisioning logs and the resolved solution dependency manifest in `run.json`;
- `episodes/<id>/`: protocol messages, solution logs, and the episode record;
- `metrics.json` and `intermediates.json`: values and calculation evidence;
- `result.json`: the common validation result.

The evidence directory remains after success or failure. Temporary checkouts, virtual environments, and workspaces are removed. Each episode has a separate HOME, workspace, and virtual-environment runtime copy. Changes to one episode's installed files cannot affect another episode. Linux child-subreaper supervisors contain solution and provisioning process trees, including children that create new sessions or become orphaned. Process groups and tracked descendants are terminated after each episode.

A criterion with `accepted` status in a partial result is accepted only over that diagnostic subset. No partial result certifies the solution as accepted. Only a full result with every criterion in scope accepted certifies acceptance.

## Tests

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

The tests use a local scripted solution and do not call a model provider.
