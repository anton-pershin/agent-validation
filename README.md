# Agent validation service

This service evaluates an exact solution commit against either a registered full-validation profile or a complete caller-supplied partial configuration. The `default` profile evaluates `reliable-agent` with PDDL Blocks World tasks, repeated runs, prompt variants, and transient action failures. It is authoritative for project-level acceptance; `blocksworld_hard` provides supplementary full-validation evidence and cannot substitute for `default`.

Validation computes VM1–VM10 and evaluates AC1–AC7. Metric keys combine stable VM identifiers with informative names, such as `VM1/unperturbed_overall_success_rate` and `VM5/prompt_variant_overall_success_rate_loss/concise`. VM5 is the signed unperturbed-minus-variant success-rate difference: positive values are losses and negative values are gains. VM2 and VM3 are computed as `1.0` when their denominators are zero; retained evidence lists these instances in `vacuous_metrics`.

## Requirements

- Python 3.11 or later
- Git
- Network access to install solution dependencies and call the solution's model provider, when required

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run commands from the repository root. When running elsewhere, set `AGENT_VALIDATION_PROFILE_DIR` to the absolute path of `config/profiles` and `AGENT_VALIDATION_SOURCE_DIR` to the validator Git checkout used for provenance.

## CLI validation

Use a full 40- or 64-character commit hash. Partial validation requires a complete YAML or JSON configuration; start with an unregistered example or a fetched profile configuration:

```bash
.venv/bin/python validation_cli_partial.py \
  --repo https://github.com/anton-pershin/reliable-agent.git \
  --commit FULL_COMMIT_HASH \
  --partial-config config/examples/default_partial.yaml \
  --output-json artifacts/partial-result.json \
  --profile default
```

`--partial-config` is required. The file must be a complete modified copy of the registered base selected by `--profile`, not a patch. Missing or unknown fields, malformed content, invalid values, and invalid references are rejected rather than filled from defaults. Problem paths in the copy remain relative to the registered base profile. For a CLI fetch–modify–submit workflow, retrieve `GET /profiles/{profile}/configuration` as shown below, save its complete `configuration` value as YAML or JSON, edit that file, and submit it with `--partial-config`.

For full validation:

```bash
.venv/bin/python validation_cli.py \
  --repo https://github.com/anton-pershin/reliable-agent.git \
  --commit FULL_COMMIT_HASH \
  --output-json artifacts/full-result.json \
  --solution-overrides '--model openai:gpt-5' \
  --profile default
```

Full validation always uses the selected registered profile unchanged. Both commands write the complete response to `--output-json`, identical to the run's `result.json`, and print its `run_id` and `artifacts_location`. They return status 0 when the service produces a validation result, including a result with `valid` or `invalid` criteria, and status 1 when the operation fails. Errors after run creation also report the run identity. Partial results are diagnostic and never certify acceptance, even when all reported criteria are accepted or the configured population is as large as a full run.

## HTTP validation

Start the service on port 8587 with the `default` active profile:

```bash
.venv/bin/python validation_service.py port=8587 profile=default
```

The equivalent console entry point is `.venv/bin/agent-validation-api port=8587 profile=default`. The `profile` argument defaults to `default`; the console entry point also accepts `AGENT_VALIDATION_PORT` and `AGENT_VALIDATION_PROFILE` for these settings.

For a complete Caila-backed example on port 8587, set `CAILA_API_KEY` through your secret-management setup and run this command from the repository root:

```bash
export OPENAI_BASE_URL="https://caila.io/api/openai/v1"
export OPENAI_API_KEY="${CAILA_API_KEY:?Set CAILA_API_KEY first}"

PYTHONPATH=src \
AGENT_VALIDATION_PROFILE_DIR="$PWD/config/profiles" \
AGENT_VALIDATION_ARTIFACT_DIR="/tmp/opencode/validation-artifacts-8587" \
AGENT_VALIDATION_TEMP_DIR="/tmp/opencode/validation-temp-8587" \
AGENT_VALIDATION_HOST="127.0.0.1" \
AGENT_VALIDATION_PORT=8587 \
/home/tony/venvs/agent-validation/bin/agent-validation-api
```

Use `.venv/bin/agent-validation-api` or the equivalent executable in your environment. Keep credentials out of the command itself and do not commit them. Check that the service is available with:

```bash
curl --fail http://127.0.0.1:8587/openapi.json >/dev/null
```

### Fetch, modify, and submit a partial configuration

Discover the active base profile's complete normalized configuration and JSON Schema with `GET /profiles/{profile}/configuration`:

```bash
mkdir -p artifacts
curl --fail http://127.0.0.1:8587/profiles/default/configuration \
  > artifacts/default-discovery.json

.venv/bin/python - <<'PY'
import json
from pathlib import Path

discovery = json.loads(Path("artifacts/default-discovery.json").read_text())
request = {
    "repo": "https://github.com/anton-pershin/reliable-agent.git",
    "commit": "FULL_COMMIT_HASH",
    "solution_overrides": "",
    "profile": discovery["profile"],
    "configuration": discovery["configuration"],
}
Path("artifacts/partial-request.json").write_text(json.dumps(request, indent=2) + "\n")
PY
```

The discovery response contains `profile`, `schema`, and `configuration`, without provider secrets. Edit `artifacts/partial-request.json` to replace `FULL_COMMIT_HASH` and modify any configuration settings needed for diagnostic feedback. Keep the configuration complete and valid against the returned schema; submitting it unchanged is also a valid partial run. Then submit it:

```bash
curl -sS -X POST http://127.0.0.1:8587/validate_partial \
  -H 'Content-Type: application/json' \
  --data-binary @artifacts/partial-request.json
```

`POST /validate` performs full validation with the service's active registered profile unchanged. Its request contains `repo`, `commit`, and optional `solution_overrides`; inline configuration is rejected. `POST /validate_partial` additionally requires the complete `configuration` object. Its `profile` is optional and defaults to the service's active profile.

A completed validation returns HTTP 200, including when criteria are valid or invalid. An unknown profile returns 404; a malformed request or invalid configuration returns 400; and a request-level failure returns 500 with a JSON error object. Results include `run_id` and `artifacts_location`, as do errors raised after run creation.

## Solution contract

The service clones the repository into a temporary directory, checks out and verifies the exact commit, creates a fresh virtual environment, and installs the solution without editable mode. It starts `python -m reliable_agent` once for each episode. Arguments from `solution_overrides` are parsed without a shell. The `{repeat}` substring is replaced with the episode's one-based repeat number, so `--seed {repeat}` supplies a different seed for each repeat.

The process communicates through versioned JSON Lines on standard input and output. Standard output carries protocol messages only. Standard error carries solution logs. See [the solution interface](docs/solution-interface.md).

## Profiles and environment

Registered profiles are full-validation-only YAML files in `config/profiles`; the available names are `default` and `blocksworld_hard`. They have no predefined partial subsections. A profile defines task files, conditions, repeats, thresholds, time and action limits, resource limits, and environment variables forwarded to the solution. Problem paths are relative to the registered profile file. Full validation never applies caller modifications.

The `default` full profile has 20 distinct tasks and 10 repeats per condition, producing 840 episodes. `pick_up_failure` is eligible for 15 tasks that require pick-up, and `unstack_failure` is eligible for nine stacked tasks. It is the authoritative profile for `reliable-agent` project-level acceptance.

The `blocksworld_hard` full profile has the same acceptance thresholds as `default` and produces 890 episodes. Its 20 distinct tasks have six eight-action, eight ten-action, and six twelve-action shortest successful plans, giving a mean optimal plan length of ten actions. Optimal plan length counts only state-changing movement actions. It excludes action-catalog requests, rejected actions, injected failures, and retries. Tests verify these lengths with an independent breadth-first search. `pick_up_failure` applies to all 20 tasks and `unstack_failure` to nine. This profile is supplementary and cannot replace `default` when certifying project acceptance.

The former 42-episode populations are preserved as unregistered, complete example configurations:

- `config/examples/default_partial.yaml`, based on `default`;
- `config/examples/blocksworld_hard_partial.yaml`, based on `blocksworld_hard`.

These files are starting points, not registered profiles or fixed partial presets. A partial copy may change any configuration setting, including its population, thresholds, criterion scope, timing, resources, variants, or failure scenarios. Partial does not necessarily mean smaller or faster. Editing an example does not change its registered base profile; creating or changing a partial copy does not revise the validation spec or solution constitution.

Configuration environment variables:

- `AGENT_VALIDATION_PROFILE`: active registered profile used by launchers and by CLI/HTTP requests that do not select one; initially `default`.
- `AGENT_VALIDATION_PROFILE_DIR`: registered profile directory; initially `config/profiles` under the working directory.
- `AGENT_VALIDATION_ARTIFACT_DIR`: evidence root; initially `artifacts/runs`.
- `AGENT_VALIDATION_TEMP_DIR`: parent for temporary resources; initially `/tmp`.
- `AGENT_VALIDATION_SOURCE_DIR`: validator Git checkout used for exact source provenance; auto-detected from the working directory or installed source when omitted.
- `AGENT_VALIDATION_MAX_ACTIVE`: maximum active validations per application; initially `4`.
- `AGENT_VALIDATION_MAX_EPISODES`: maximum concurrent episodes per application; initially `32`.
- `AGENT_VALIDATION_HOST` and `AGENT_VALIDATION_PORT`: HTTP listen address; initially `127.0.0.1:8000`.

Only provider variables named in the profile and required system variables reach the solution. Do not put credentials in the repository URL, `solution_overrides`, or partial configuration. The service redacts common credential forms from public responses and retained evidence.

## Evidence and cleanup

Every validation that passes request and configuration validation receives a unique `run_id` and a new directory at `artifacts_location` before provisioning. The service immediately writes `run.json` with status `running`; malformed requests rejected before allocation do not receive synthetic run identifiers. Each evidence directory contains:

- `run.json`: the run manifest, including requested and verified solution commits, repository and overrides, validation scope, registered base profile and content hash, complete resolved configuration and hash, exact validator Git revision and clean/dirty state, staged, unstaged, and untracked changes with their diff, runtime, platform, validator and resolved solution dependency versions, artifact references, and run status;
- `planned_episodes.json` and `episodes.jsonl`: the complete population and observations;
- `provisioning.json`: bounded provisioning logs, with the resolved solution dependency manifest in `run.json`;
- `episodes/<id>/`: protocol messages, solution logs, and the episode record;
- `metrics.json` and `intermediates.json`: values and calculation evidence, including overall and per-task fault-exposure and exposed-recovery rates for each transient-failure scenario;
- `result.json`: the complete CLI/HTTP response for every completed run, including all in-scope AC and VM outcomes.

The terminal run status is `completed`, `failed`, or cooperatively `aborted`. Run status describes service execution, not implementation acceptance: a completed run can contain invalid criteria. `result.json` is written before the `completed` transition and is not required for failed or aborted runs. Terminal evidence is sealed against later service writes, and every execution uses a new run record rather than overwriting earlier evidence. Secrets are redacted from manifests, diffs, responses, logs, and artifacts.

The evidence directory remains after success or failure. Temporary checkouts, virtual environments, and workspaces are removed. Each episode has a separate HOME, workspace, and virtual-environment runtime copy. Changes to one episode's installed files cannot affect another episode. Linux child-subreaper supervisors contain solution and provisioning process trees, including children that create new sessions or become orphaned. Process groups and tracked descendants are terminated after each episode.

A criterion with `accepted` status in a partial result is accepted only over that diagnostic configuration. No partial result certifies the solution as accepted. Only a completed full-validation result from the unchanged, authoritative `default` profile with every in-scope criterion accepted can certify project-level acceptance. `blocksworld_hard` remains supplementary. Project completion additionally requires the accepted implementation to be integrated into the solution's main branch.

The validation service does not own the solution's full-validation history. In the solution management repository, append every completed, failed, or aborted full run to `.internal/validation-results.md`, recording its run ID, related spec when applicable, solution commit, run status, and the returned `artifacts_location`. Partial runs do not belong in that history.

## Tests

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

The tests use a local scripted solution and do not call a model provider.
