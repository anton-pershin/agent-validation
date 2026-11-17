## Terminology

### General

| Term | Definition |
|------|------------|
| Task | One benchmark-defined objective that an agent is asked to complete. |
| Episode | One attempt to complete one task under one evaluation condition and repeat. |
| Evaluation condition | A configuration for running episodes, such as the unperturbed configuration, a prompt variant, or a transient-failure scenario. |
| Task success | Satisfaction of a task's correctness conditions, determined independently by the benchmark. For Blocks World, success requires an explicit completion declaration and a goal-satisfying final state. |
| Task success rate | For one task and one evaluation condition, the number of successful planned episodes divided by the total number of planned episodes for that task and condition. Failed, timed-out, or crashed episodes remain in the denominator. |
| Overall success rate | The arithmetic mean of task success rates across the benchmark task set for one evaluation condition. Each task has equal weight. |
| Prompt-variant overall-success-rate loss | The signed difference `unperturbed overall success rate - variant overall success rate`. A negative value is a gain. |
| Transient-failure overall-success-rate loss | `max(0, unperturbed overall success rate - failure-scenario overall success rate)`. An improvement is reported as zero loss. |
| Overall success rate range | The highest overall success rate minus the lowest across the specified evaluation conditions or repeated runs. |
| Unperturbed evaluation condition | The active resolved configuration's original task inputs, prompts, model configuration, and execution settings, without meaning-preserving prompt changes or deliberately injected transient failures. |
| Prompt variant | A prompt changed without changing its intended meaning. |
| Transient-failure scenario | A configured temporary tool or environment failure introduced during an episode. |
| Validation profile | A named, registered configuration that defines the complete full-validation procedure for one solution subproject. |
| Registered base profile | The unchanged registered profile from which a full run directly proceeds or a partial run derives its effective configuration. The result field `profile` contains this identity. |
| Active resolved configuration | The complete configuration used for a run: the unchanged registered profile for full validation or a validated complete modified copy for partial validation. |
| Complete modified configuration | A complete copy of a registered base configuration with caller-selected changes. It is not a patch and does not inherit omitted fields during submission. |
| Evaluation run | The execution of the episode population and conditions defined by an active resolved configuration. |
| Validation run | One full or partial validation execution with a unique run ID and run directory. |
| Full validation | Validation using an unchanged registered profile and its complete acceptance contract. Only full validation can certify implementation acceptance. |
| Partial validation | Diagnostic validation using a complete modified configuration derived from a registered base profile. It need not be reduced and cannot certify implementation acceptance. |
| Validation metric | A value computed from independently collected episode records to assess one or more requirements. |
| Acceptance criterion | A condition on one or more validation metrics, using thresholds resolved from the active resolved configuration. |
| Criterion status | `accepted` means all required metrics are computed and meet their conditions; `valid` means all required metrics are computed but at least one fails its condition; `invalid` means at least one required metric cannot be computed. |
| Implementation classification | From the applicable registered full result: `accepted` means every AC is accepted; `valid` means no AC is invalid and at least one is valid; `invalid` means at least one AC is invalid. Partial results do not establish this classification. |
| Metric instance | One computed value for a specified condition, repeat, or scenario where the metric definition requires separate values. |
| `likely_incorrect` flag | The agent's boolean report that its result is likely incorrect, supplied before the benchmark reveals correctness. |
| Completion declaration | An explicit statement by the agent that it has completed the task. It does not establish that the task was successful. |
| Action attempt | One requested environment action, including an action-catalog request, a failed or rejected action, or a repeated action. Each action in a batch is one action attempt. |
| End-to-end time | Elapsed wall time from task delivery until receipt of the final response or forced termination, including model and tool waits. |
| Planned episode | An episode in the active resolved configuration's evaluation population, whether or not the solution completes it. |
| Fault exposure | Evidence that an episode reached the configured injection point and the transient failure occurred. |
| Run ID | The unique identifier assigned to one validation run. |
| Run directory | The new, run-specific directory containing the manifest and retained evidence for one validation run. |
| `artifacts_location` | The run-directory path or URI returned in validation responses and post-creation errors. |
| Run manifest | `run.json`, created immediately with status `running` and updated with provenance, resolved configuration, evidence references, and terminal status. |
| Run status | The service execution state: `running`, `completed`, `failed`, or `aborted`. It is distinct from criterion status and implementation classification. |
| Full-validation history | The solution management repo's append-only index of completed, failed, and aborted full runs, citing run IDs and run directories rather than duplicating complete results. |

### Presentation layer

| Term | Definition |
|------|------------|
| Full-validation CLI | `validation_cli.py`, which requests full validation with an unchanged registered profile and writes the complete run-aware JSON result. |
| Partial-validation CLI | `validation_cli_partial.py`, which requires a complete modified configuration file and requests diagnostic validation against a registered base profile. |
| Configuration-discovery endpoint | `GET /profiles/{profile}/configuration`, which returns a registered profile's complete normalized configuration and JSON Schema without secrets. |
| HTTP endpoint | A URL path that accepts an HTTP request for configuration discovery or full or partial validation. |
| HTTP service launcher | `validation_service.py` or the equivalent `agent-validation-api` console entry point. |

### Evaluation orchestration layer

| Term | Definition |
|------|------------|
| Registered-base resolution | Loading a registered profile and retaining its identity, content hash, and base directory independently of the effective configuration. |
| Complete-configuration validation | Validation of every required field, nested value, and reference in a submitted partial configuration without filling omitted fields from defaults. |
| Effective configuration | The active resolved configuration together with the immutable registered-base identity and base-directory context used to plan and execute a run. |
| Run-aware error | An error raised after run allocation that carries its run ID and artifact location for CLI or HTTP reporting. |

### Evaluation and metric layer

| Term | Definition |
|------|------------|
| Run-aware result | The common JSON object containing run ID, artifact location, registered base profile, validation scope, and every in-scope AC and VM outcome. |
| Diagnostic criterion outcome | An AC/VM outcome computed under partial configuration. It retains its ordinary criterion or metric status but cannot certify implementation acceptance. |

### Integration and persistence layer

| Term | Definition |
|------|------------|
| Solution interface | The versioned JSON Lines interaction protocol between the validation service and one solution process. It exchanges public task data, action requests, observations, and final responses without private correctness information. |
| Validator provenance | The exact validator Git revision, clean/dirty state and uncommitted diff, runtime, platform, and dependency versions retained in the run manifest. |
| Terminal evidence protection | Rejection of service writes after a run becomes completed, failed, or aborted, and preservation of earlier run evidence during later executions. |
