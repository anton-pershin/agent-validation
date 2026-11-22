# Solution interface

## Process and transport

The validation service starts one process for each episode:

```bash
python -m <solution_module> [solution_overrides]
```

The module name comes from the active profile's `solution_module` setting. Both registered PDDL profiles use `reliable_agent_pddl`; the AppWorld profile uses `reliable_agent_appworld`.

The process runs in the fresh virtual environment created for the validation request. The service uses no shell to parse or execute overrides. After parsing, it replaces each `{repeat}` substring with the planned episode's one-based repeat number. For example, `--seed {repeat}` supplies seed 1 for repeat 1 and seed 10 for repeat 10. Each episode has a new process, HOME, workspace, environment state, and virtual-environment runtime copy. Changes to installed files in one episode do not affect another episode.

Standard input and standard output carry one JSON object per line. Standard output must not contain logs. Write logs to standard error. One action call can be outstanding at a time. A call can contain one action or a batch.

Every message has:

- `protocol_version`: integer `1`;
- `type`: `task`, `actions`, `observation`, or `final`.

Identifiers are opaque. The service does not send the evaluation condition, repeat number, private score, reference plan, or a goal-achieved signal.

## Task message

The first message is from the service:

```json
{
  "protocol_version": 1,
  "type": "task",
  "episode_id": "opaque-episode-id",
  "task": {
    "id": "stack_ab",
    "instructions": "public instructions",
    "input": "public PDDL problem",
    "tools": [
      {
        "name": "pick_up",
        "description": "public description",
        "arguments_schema": {
          "type": "object",
          "properties": {"block": {"type": "string"}},
          "required": ["block"],
          "additionalProperties": false
        }
      }
    ]
  }
}
```

The Blocks World tools are `get_action_catalog`, `pick_up`, `put_down`, `stack`, and `unstack`. `stack` and `unstack` require `block` and `other_block`. The other actions require either no arguments or `block`, as shown by their schemas.

### AppWorld task data and tool

For AppWorld, `task.input` is a JSON string containing only public data:

- `instruction`: the task instruction;
- `supervisor`: public first/last name, email, and phone number;
- `app_descriptions`: public application descriptions.

The only AppWorld tool is `execute_code`. Its schema requires one string argument, `code`, and no other arguments. The code runs in a stateful Python shell with `apis` and `requester` objects. Use API Docs APIs for public schemas and Supervisor APIs for permitted account information. Print values that must be observed. Ground truth, private evaluation outcomes, evaluation reports, validation condition, and repeat number are not included in the task message.

```json
{
  "name": "execute_code",
  "arguments": {
    "code": "response = apis.supervisor.show_active_task()\nprint(response)"
  }
}
```

Call `apis.supervisor.complete_task(...)` when the AppWorld task is complete, supplying an answer when required. This AppWorld call does not end the validation episode. Continue to observe results and send the normal final protocol message afterward.

## Action requests

Send one action or a batch:

```json
{
  "protocol_version": 1,
  "type": "actions",
  "call_id": "call-17",
  "actions": [
    {"name": "pick_up", "arguments": {"block": "a"}}
  ]
}
```

Use `{"name": "get_action_catalog", "arguments": {}}` to request the action catalog. `call_id` is a required string. `actions` must be a nonempty array. An empty array or invalid envelope is a protocol error with no manufactured action attempts. Every submitted action is an action attempt. Each batch member counts separately. Malformed, unknown, rejected, and failed actions also count.

## Observations

The service returns one result for each submitted action:

```json
{
  "protocol_version": 1,
  "type": "observation",
  "call_id": "call-17",
  "results": [
    {
      "status": "ok",
      "observation": {
        "action": "pick_up",
        "state": ["(clear b)", "(hand-empty)", "(holding a)", "(on-table b)"]
      },
      "error": null
    }
  ]
}
```

`status` is `ok`, `error`, or `rejected`. An error result has an `error` object with `type` and `message`. Error types include `malformed_action`, `unknown_action`, `precondition_failed`, `transient_failure`, and `action_budget_exceeded`. Observations contain public world predicates only. A transient failure does not change the world state. Use the observation to retry, revise the approach, or stop.

For AppWorld, a successful `execute_code` result has `observation.output` containing the stateful shell's printed output or `Execution successful.` AppWorld syntax/runtime failures are returned as an `execution_failed` error with the public execution traceback; these are ordinary observations, not validator infrastructure errors. AppWorld malformed/unknown actions and injected transient failures use the same error envelope. Private evaluation data is never included.

Batch actions run in order until the action budget rejects an action. A rejected action and all later members of that batch receive rejected results. The episode then ends without a final response.

## Final response

End the episode with one final message:

```json
{
  "protocol_version": 1,
  "type": "final",
  "completed": true,
  "answer": "Block a is on block b.",
  "confidence": 0.97,
  "likely_incorrect": false,
  "reason": "The final public state satisfies the supplied goal."
}
```

`completed` is the explicit completion declaration. Set it to `false` when stopping without a completion claim. `answer` and `reason` are strings. `confidence` is a number from 0 through 1. `likely_incorrect` must be a boolean and must report the solution's own assessment before private scoring.

The service checks the final benchmark state independently. For Blocks World, reaching a goal does not end the episode, and success requires `completed=true` and a goal-satisfying final state. For AppWorld, calling `complete_task` does not end the episode, and success requires `completed=true`, AppWorld task completion, and a successful private `world.evaluate()` result. `completed=false` is unsuccessful without private evaluation. A missing or invalid `likely_incorrect` makes error-awareness metrics fail to compute. Process exit without a final message, malformed output, timeout, and configured interaction-budget exhaustion remain unsuccessful episodes. AppWorld initialization, worker, evaluator, or cleanup failures that prevent scoring are infrastructure failures instead.

## Environment and limits

The profile lists provider environment variables that the solution can receive. Other caller variables are not forwarded, except required system and certificate variables. AppWorld workers do not receive provider variables. Model waits, tool/code waits, retries, and self-assessment count toward the episode time limit. Dependency installation and setup before task delivery do not count. AppWorld evaluation after the final response also does not extend the measured solution response time.

## Validation result

The validation result contains run identity, the registered base profile, scope, and one entry for each in-scope acceptance criterion. CLI output, HTTP responses, and the completed run's `result.json` use the same complete shape. Metric keys combine stable VM identifiers with informative names:

```json
{
  "run_id": "20261116T010203-040506Z-0123456789ab",
  "artifacts_location": "/absolute/path/to/artifacts/runs/20261116T010203-040506Z-0123456789ab",
  "profile": "default",
  "validation_scope": "full",
  "AC1": {
    "status": "accepted",
    "metrics_status": {
      "VM1/unperturbed_overall_success_rate": {
        "computation_status": "computed",
        "acceptance_status": "accepted",
        "computed_value": 1.0,
        "expected_value_or_threshold": ">= 0.95",
        "error_message": null
      }
    }
  }
}
```

Other single-instance keys include `VM2/error_flag_precision`, `VM3/error_flag_recall`, and `VM10/timely_final_response_rate`. Multiple-instance keys preserve the variant or scenario identifier after the descriptive name, for example:

- `VM5/prompt_variant_overall_success_rate_loss/concise`
- `VM7/transient_failure_overall_success_rate_loss/pick_up_failure`

Bare VM keys are not used in the result. A computed metric has `acceptance_status` of `accepted` or `rejected`. A metric that cannot be computed has `computation_status` of `failed_to_compute`, a null `computed_value`, and an explanation in `error_message`.

For partial validation, `profile` still identifies the registered base rather than the modified copy, and `validation_scope` is `partial`. The complete effective configuration is retained in run evidence. Partial criteria remain diagnostic and do not certify implementation acceptance.

`run_id` and `artifacts_location` locate the unique run directory. Errors after run creation include the same identifiers. The directory's `run.json` manifest has status `running`, `completed`, `failed`, or `aborted`; this execution status is separate from criterion status, and the result has no overall implementation-outcome field. The manifest records the requested and verified solution commits, base and effective configuration hashes, validator source revision and uncommitted changes, runtime and dependency provenance, and retained-artifact references. `result.json` is mandatory for completed runs but not for failed or aborted runs.
