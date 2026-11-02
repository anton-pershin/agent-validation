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
| Overall success rate loss | For a perturbed condition, the decrease from the unperturbed overall success rate, with improvements reported as zero loss. |
| Overall success rate range | The highest overall success rate minus the lowest across the specified evaluation conditions or repeated runs. |
| Unperturbed evaluation condition | The profile's original task inputs, prompts, model configuration, and execution settings, without meaning-preserving prompt changes or deliberately injected transient failures. |
| Prompt variant | A prompt changed without changing its intended meaning. |
| Transient-failure scenario | A configured temporary tool or environment failure introduced during an episode. |
| Evaluation run | The execution of a profile-defined set of episodes under specified conditions and repeats. |
| Validation profile | A named configuration that binds one solution subproject to a benchmark, evaluation population, criteria, and threshold values. |
| Full validation | Evaluation over the complete population and conditions required by the active profile. |
| Partial validation | A reduced evaluation used for diagnostic feedback, which cannot certify the complete requirements. |
| Validation metric | A value computed from independently collected episode records to assess one or more requirements. |
| Acceptance criterion | A condition on one or more validation metrics, using thresholds resolved from the active profile. |
| Criterion status | `accepted` means all required metrics are computed and meet their conditions; `valid` means all required metrics are computed but at least one fails its condition; `invalid` means at least one required metric cannot be computed. |
| Metric instance | One computed value for a specified condition, repeat, or scenario where the metric definition requires separate values. |
| `likely_incorrect` flag | The agent's boolean report that its result is likely incorrect, supplied before the benchmark reveals correctness. |
| Completion declaration | An explicit statement by the agent that it has completed the task. It does not establish that the task was successful. |
| Action attempt | One requested environment action, including an action-catalog request, a failed or rejected action, or a repeated action. Each action in a batch is one action attempt. |
| End-to-end time | Elapsed wall time from task delivery until receipt of the final response or forced termination, including model and tool waits. |
| Planned episode | An episode in the profile-defined evaluation population, whether or not the solution completes it. |
| Fault exposure | Evidence that an episode reached the configured injection point and the transient failure occurred. |

### Presentation layer

| Term | Definition |
|------|------------|
| Full-validation CLI | `validation_cli.py`, which requests full validation and writes the JSON result to the specified output file. |
| Partial-validation CLI | `validation_cli_partial.py`, which requests diagnostic validation and writes the JSON result to the specified output file. |
| HTTP endpoint | A URL path that accepts an HTTP request for full or partial validation. |

### Integration and persistence layer

| Term | Definition |
|------|------------|
| Solution interface | The versioned JSON Lines interaction protocol between the validation service and one solution process. It exchanges public task data, action requests, observations, and final responses without private correctness information. |