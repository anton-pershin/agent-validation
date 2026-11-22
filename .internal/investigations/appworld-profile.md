## AppWorld validation profile

**Status**: active

### Objective

Define and implement the registered `appworld` validation profile required by issue #15 and the `reliable-agent-appworld` constitution. The profile must provide reproducible full validation and fine-grained partial validation without excessive resource use on the shared validation host.

### Current constraints

- Follow `.internal/validation-spec.md` for registered profiles, evaluation records, acceptance criteria, run evidence, and full/partial validation.
- Enable AC1, AC2, AC3, and AC7 only. The solution constitution omits NFR2, NFR3, and NFR4.
- Keep concurrency configurable. Use one worker as the frugal default, not a hard-coded service limit.
- Do not expose AppWorld ground truth or private evaluation results to the solution.

### Current state

The service originally had two Blocks World profiles and a concrete Blocks World execution path. The selected AppWorld design is recorded in `.internal/specs/15-appworld-profile-general-spec.md`. The adapter, registered profile, provisioning utility, fake-runtime integration tests, and documentation are implemented. Ruff, the clean complete regression suite (154 passed, 1 real-runtime skip), real-runtime smoke checks, and end-to-end provisioning pass. Independent review and PR checks remain.

### Contexts

| ID | Solution implementation | Validation setup | Runtime and environment | Baseline |
|----|-------------------------|------------------|-------------------------|----------|
| C1 | No solution implementation required | AppWorld 0.1.3.post1, data 0.1.0 | Python 3.12.13; 8 logical CPUs; 15 GiB RAM; shared Linux host | No AppWorld adapter |
| C2 | Local scripted solution and fake AppWorld worker | Validator unit/integration tests | Main validator environment; no model provider | Existing Blocks World suite passes on main |

### Current understanding

#### Supported by evidence

- The latest AppWorld release is 0.1.3.post1 at source commit `66ad8099e12188ece0d3fe45e661dbc01880813b`.
- Its selected `train`, `dev`, and `test_normal` splits contain 90, 57, and 168 tasks: 315 tasks and 630 two-repeat episodes.
- AppWorld 0.1.3.post1 has dependency conflicts with the validator and needs an isolated Python runtime.
- AppWorld permits one active world per process. Parallel worlds need separate processes and unique output locations.
- Real no-op probes reached 659–660 MiB peak RSS and used about 7.1 CPU-seconds per fresh worker. These are baseline observations, not upper bounds.

#### Tentative or unresolved

- Real agent code can require more memory and CPU than the no-op probes.
- The future solution subprocess has no implementation to measure yet.
- The recommended 2 GiB memory target and 3 GiB hard limit need operational confirmation during real validation.

### Experiments and decisions

| ID | Idea or question | Context | Result and evidence links | Decision and rationale |
|----|------------------|---------|---------------------------|------------------------|
| E1 | Which exact task population should full validation use? | C1 | The website says 750, while released tests define 732 total tasks. The owner selected train, dev, and test_normal: [issue discussion](https://github.com/anton-pershin/agent-validation/issues/15). | Pin 315 explicit task IDs from AppWorld 0.1.3.post1/data 0.1.0; exclude test_challenge. |
| E2 | Can AppWorld share the validator Python environment? | C1 | AppWorld requires Pydantic 1 and older FastAPI/Uvicorn; the validator requires Pydantic 2 and newer versions. | Use an isolated, locked, pre-provisioned AppWorld runtime. |
| E3 | What process model supports parallel AppWorld execution? | C1 | AppWorld's parallelization guide and issue #40 require one world per process and unique experiment/task output paths. | Start one fresh stdio worker per episode with a unique experiment name. |
| E4 | What RAM and CPU budget is appropriate? | C1 | One real task from each selected split peaked at 659–660 MiB and used about 7.1 CPU-seconds, including import, setup, no-op execution, evaluation, and shutdown. | Default to concurrency one; recommend 2 GiB memory target, 3 GiB hard limit, and one CPU quota. Keep every capacity setting configurable. |
| E5 | Does existing partial validation need a new mechanism? | C2 | The complete modified configuration contract already permits arbitrary task subsets, repeats, criteria, and resources. | Reuse configuration discovery and complete partial submission without a fixed AppWorld partial preset. |
| E6 | Does the implemented stdio worker operate with the real pinned runtime? | C1 | Initial probes exposed an adapter filename that shadowed `import appworld` and an interpreter-path `.resolve()` call that bypassed the venv. After correction, real train-task initialization, no-op execution, private evaluation, resource reporting, and cleanup passed. | Keep the adapter name `appworld_session.py` and preserve the venv interpreter symlink path. Retain `tests/test_appworld_real.py` as the repeatable smoke check. |
| E7 | Can operators provision the exact approved runtime reproducibly? | C1 | `scripts/provision_appworld.py` installed the locked dependencies, verified wheel/data SHA-256 values, matched all selected task IDs, and its provisioned runtime passed the real smoke test. | Use the checked provisioning utility and exact dependency manifest; do not use a floating install. |
| E8 | Does the integration preserve existing behavior? | C2 | The clean complete suite passed 154 tests with one expected real-runtime skip. Ruff and both main/AppWorld `pip check` runs passed; all 79 locked AppWorld distributions matched the manifest. | Retain the benchmark-session boundary and use the complete suite as the PR regression gate. |

### Next candidates

- [x] Run the implemented worker against the pinned real AppWorld runtime without a model provider.
- [x] Run the clean complete regression suite and address review findings.
- [ ] Complete independent review and required PR CI.
- [ ] Measure later real solution runs and adjust deployment limits without changing the registered population.

### Linked specs

- `.internal/specs/15-appworld-profile-general-spec.md` defines and implements the selected design.
