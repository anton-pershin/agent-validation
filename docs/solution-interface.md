# Solution interface

## Process and transport

The validation service starts one process for each episode:

```bash
python -m reliable_agent [solution_overrides]
```

The process runs in the fresh virtual environment created for the validation request. The service uses no shell to parse overrides. Each episode has a new process, HOME, workspace, and environment state. The installed virtual environment is read-only during episode execution.

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

The service checks the final world state independently. Reaching a goal does not end the episode. Success requires `completed=true` and a goal-satisfying final state. A missing or invalid `likely_incorrect` makes error-awareness metrics fail to compute. Process exit without a final message, malformed output, timeout, and budget exhaustion remain unsuccessful episodes.

## Environment and limits

The profile lists provider environment variables that the solution can receive. Other caller variables are not forwarded, except required system and certificate variables. Model waits, tool waits, retries, and self-assessment count toward the episode time limit. Dependency installation and setup before task delivery do not count.
