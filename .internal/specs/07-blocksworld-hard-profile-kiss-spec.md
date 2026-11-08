## Blocks World hard profile

### 1. Requirement analysis

- **R1. Separate hard profile.** Add the `blocksworld_hard` validation profile for `reliable-agent`. Keep the `default` profile unchanged. Use the existing original instructions, `concise` and `formal` prompt variants, AC1–AC7, and default acceptance thresholds.
- **R2. Full population.** Use 20 distinct Blocks World tasks and 10 repeats. Include the unperturbed condition, both prompt variants, `pick_up_failure` for all 20 tasks, and `unstack_failure` for nine selected tasks. The full population must contain exactly 890 episodes: 200 unperturbed, 200 concise, 200 formal, 200 `pick_up_failure`, and 90 `unstack_failure` episodes.
- **R3. Partial population.** Use five tasks and two repeats. Include both prompt variants, `pick_up_failure` for four tasks, and `unstack_failure` for two tasks. The partial population must contain exactly 42 episodes and remain diagnostic.
- **R4. Planning difficulty.** Use tasks whose shortest successful plans contain eight, ten, or twelve state-changing movement actions. Include six eight-action tasks, eight ten-action tasks, and six twelve-action tasks. The arithmetic mean of the 20 shortest-plan lengths must be exactly ten actions. Do not count action-catalog requests, rejected actions, injected failures, or retries as plan actions.
- **R5. Valid and distinct tasks.** Use only the PDDL predicates and public actions supported by the existing Blocks World adapter. Give every task a distinct initial-state and goal pair. Every task must have a successful shortest plan within the 50-action-attempt limit.
- **R6. Failure coverage.** Every task must have a successful shortest plan that contains `pick_up`. Each of the nine tasks selected for `unstack_failure` must have a successful shortest plan that contains `unstack`.
- **R7. Documentation.** Document the new profile, its difficulty measure, and its population sizes in the README. Add the profile to Section 4 of the validation spec through the manual approval workflow.

Expected behavior variants:

- **B1. Existing default validation.** The `default` profile retains its current tasks, thresholds, and 890-episode full and 42-episode partial populations.
- **B2. Hard full validation.** The `blocksworld_hard` full configuration plans 890 episodes with the condition counts in R2.
- **B3. Hard partial validation.** The `blocksworld_hard` partial configuration plans 42 episodes with the condition counts in R3.
- **B4. Measured planning difficulty.** An independent shortest-path search confirms every required plan length and the exact mean of ten actions.
- **B5. Compatible evaluation.** Existing episode execution, metric calculation, and criterion evaluation support the new profile without structural changes.

### 2. Tests

- **T1. Exact hard populations.** Load `blocksworld_hard` and assert 890 full episodes and 42 partial episodes. Assert all per-condition counts and repeat identifiers from R2 and R3. Covers B2, B3, R2, and R3.
- **T2. Shortest-plan difficulty.** Parse all hard tasks and use an independent breadth-first search over Blocks World states to find each shortest successful plan. Assert six eight-action plans, eight ten-action plans, six twelve-action plans, and a mean length of ten. Covers B4 and R4.
- **T3. Task validity and failure coverage.** Assert 20 unique task identifiers and 20 unique initial-state and goal pairs. Execute every shortest plan through `BlocksWorldEnvironment`, assert goal satisfaction, and assert the action coverage from R6. Covers R5 and R6.
- **T4. Metric compatibility.** Compute all metrics and criteria over the complete planned hard population with scripted successful episode records. Assert that every metric computes and every criterion is accepted. Covers B5.
- **T5. Service and regression checks.** Run an actual partial validation with `blocksworld_hard` and the local scripted solution. Assert 42 successful episode records, 12 fault exposures, and accepted AC1–AC7 results. Assert that the default population counts remain 890 and 42. Check the profile documentation. Run the complete test suite, Ruff, and whitespace checks. Covers B1, B3, B5, and R7.

### 3. Implementation plan

#### 3.1 Implementation repos

https://github.com/anton-pershin/agent-validation — management and implementation repository.

#### 3.2 High-level design

The change adds profile configuration and PDDL task data. The existing presentation, orchestration, metric, and solution-adapter components remain unchanged. The profile resolver and Blocks World benchmark adapter consume the new configuration through their existing interfaces.

```mermaid
flowchart TD
  subgraph Callers[External callers]
    CLIIn[CLI invocation]
    HTTPIn[HTTP POST request]
  end
  subgraph Presentation[Presentation layer]
    CLI[validation_cli.py and validation_cli_partial.py]
    HTTP[POST /validate and /validate_partial]
  end
  subgraph Orchestration[Evaluation orchestration layer]
    Request[Request and profile resolver]
    Runner[Evaluation run coordinator]
    Episode[Episode executor]
  end
  subgraph Evaluation[Evaluation and metric layer]
    Records[Episode record collector]
    Metrics[VM1–VM10 metric calculator]
    Criteria[AC1–AC7 criterion evaluator]
    Result[JSON result builder]
  end
  subgraph Adapters[Integration and persistence layer]
    Solution[Solution revision checkout and launcher]
    Benchmark[Benchmark adapter]
    Store[Episode and artifact store]
  end
  subgraph Outputs[File and HTTP results]
    File[JSON output file and exit status]
    HTTPResponse[HTTP response]
  end

  CLIIn -->|arguments| CLI
  HTTPIn -->|JSON body| HTTP
  CLI -->|parse arguments| Request
  HTTP -->|parse request| Request
  Request -->|resolved run specification| Runner
  Runner -->|checkout and launch solution| Solution
  Runner -->|select profile task set| Benchmark
  Runner -->|execute planned episodes| Episode
  Episode -->|interact with solution| Solution
  Episode -->|benchmark actions and correctness| Benchmark
  Episode -->|episode observations| Records
  Runner -->|persist plans and observations| Store
  Records -->|persist records and artifacts| Store
  Store -->|episode records and artifacts| Metrics
  Metrics --> Criteria
  Criteria --> Result
  Result --> File
  Result --> HTTPResponse

  classDef modified fill:#fff2cc,stroke:#d6b656,color:#000000
  class Request,Benchmark modified
```

A new directory separates the hard problems from the default problems. A test-only shortest-path search measures difficulty independently from the hand-written PDDL files and the execution adapter. Expected plan lengths remain test data; the production profile schema does not change.

```mermaid
flowchart TD
  Profile[blocksworld_hard.yaml]
  Tasks[20 hard PDDL tasks]
  Full[Full: 890 episodes]
  Partial[Partial: 42 episodes]
  Planner[Existing planned-episode builder]
  Search[Test-only breadth-first search]
  Executor[Existing Blocks World environment]
  Metrics[Existing metric and criterion evaluation]

  Tasks --> Profile
  Profile --> Full
  Profile --> Partial
  Full --> Planner
  Partial --> Planner
  Tasks --> Search
  Search --> Executor
  Planner --> Metrics
  Executor --> Metrics

  classDef created fill:#d9ead3,stroke:#38761d,color:#000000
  class Profile,Tasks,Full,Partial,Search created
```

No provisioning, protocol, metric, criterion, HTTP, storage, or solution code needs a structural change.
