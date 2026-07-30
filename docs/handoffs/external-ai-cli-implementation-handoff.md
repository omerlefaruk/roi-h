# External AI CLI Implementation Handoff

Use this prompt in a new Codex task with the repository open at
`/Users/rau/Desktop/Projects/roi-h`.

## Objective

Implement the complete external-AI CLI plan for ROI-H. Start with the versioned JSON
command interface. Keep the current human CLI working. The installed CLI is the only
external-AI transport.

Do not stop after scaffolding. Work through the phases in dependency order until the full
acceptance criteria pass, unless a real product decision or an external dependency blocks
progress.

## Read First

Read these files completely, in this order:

1. `AGENTS.md`
2. `docs/external-ai-cli-plan.md`
3. `docs/research/external-ai-cli-primary-research.md`
4. `docs/project-storage-activegraph-refactor.md`, with special attention to Sections
   15, 19, 20, 21, 22, 26, 27, and 28
5. `docs/distribution-and-updates.md`
6. `README.md`
7. `pyproject.toml`

Treat `docs/external-ai-cli-plan.md` as the authority for operation IDs, machine output,
safe retry, command additions, and task control. Treat the storage document as
the authority for data ownership, logical paths, ActiveGraph, portability, secrets,
diagnostics, and retention.

## Repository State

At handoff creation:

- repository HEAD is `e01cc4e0f529f0491a78fb0fdd6b84ed4754d88f`;
- the product capability baseline is
  `e8a39941f481242d43faf67202f380da2c223bb2`;
- Python support is 3.12 only;
- the project uses `argparse`;
- local qualification is authoritative;
- GitHub Actions must not be added; and
- user-owned state stays under `ROI_H_HOME` or `~/.roi-h`, outside the core repository
  and package.

The working tree can contain the approved plan documents. Inspect it before all edits.
Preserve unrelated user changes. If the plan documents are not committed, commit only
this approved document set before runtime implementation:

```text
README.md
docs/distribution-and-updates.md
docs/project-storage-activegraph-refactor.md
docs/external-ai-cli-plan.md
docs/research/external-ai-cli-primary-research.md
docs/handoffs/external-ai-cli-implementation-handoff.md
```

Use a separate documentation commit. Do not push or publish unless the user asks.

## Required Design

Build one deep command module with a small interface:

```python
describe(operation_id: str | None) -> OperationManifest
execute(request: CommandRequest) -> CommandResult
```

The module interface includes schemas, effect class, retry rules, error behavior,
approval rules, task behavior, and performance limits. Its implementation can use
internal files, but callers must only need the small interface.

Use two adapters at the same seam:

1. The current human CLI.
2. The strict agent JSON or JSON Lines CLI.

The operation catalog and dispatcher only validate, describe, and route. Keep project,
run, tool, approval, phase, artifact, skill, automation, secret, store, retention, and
diagnostic behavior local to their domain modules. Do not create a universal manager
that owns all business rules.

Do not replace `argparse` only for style. Replace it only if a proven contract requirement
cannot be implemented safely.

## Mandatory Machine Commands

Implement:

```shell
roi-h agent describe
roi-h agent describe OPERATION
roi-h agent context
roi-h agent call OPERATION --input -
roi-h agent call OPERATION --input FILE
```

The agent commands are always noninteractive. They never open a browser, prompt, pager,
spinner, editor, or color output unless an explicit operation requires it and its schema
states that behavior.

Standard output contains only the machine result. Logs, warnings, and progress text go to
standard error.

## Mandatory Human Commands

Add:

```shell
roi-h rpa runs list
roi-h rpa runs show RUN_ID
roi-h rpa runs wait RUN_ID --timeout 60
roi-h rpa runs cancel RUN_ID
roi-h rpa events list --run-id RUN_ID
roi-h rpa events follow --run-id RUN_ID --after EVENT_ID
roi-h rpa trace show --run-id RUN_ID

roi-h rpa skill list
roi-h rpa skill show NAME
roi-h rpa skill validate NAME
roi-h rpa skill promote NAME
roi-h rpa skill delete plan NAME
roi-h rpa skill delete apply PLAN_ID

roi-h rpa automation list
roi-h rpa automation show NAME --version VERSION
roi-h rpa automation verify NAME --version VERSION
roi-h rpa automation compare NAME VERSION_A VERSION_B

roi-h rpa secret status NAME
roi-h rpa secret set NAME --value-stdin
```

Keep current commands as compatibility adapters for one documented release. In
particular:

- `rpa status` and `rpa cancel` stay valid;
- `rpa automations` stays as a list alias;
- current project tool definition stays valid; and
- the `run input add` and `run files` rewriting stays until the human noun layout is
  normalized.

Fix the false promotion recommendation in `src/roi_h/harness/custom.py`. It must name a
real parser command.

## Register Existing Work

Do not reimplement completed storage functions. Put typed operation handlers in front of
these implementations:

- `WorkspaceCatalog` and project path, doctor, archive, import, rename, and delete code;
- `StoreLifecycle`;
- `RunStorage` and logical paths;
- artifact put, list, and export;
- `RetentionPlanner`;
- `SecretStore` implementations;
- `DiagnosticSink`;
- `ActiveGraphProjectionAdapter`;
- observer run projections;
- `RunSession` run, tool, phase, approval, automation, and reconciliation behavior; and
- `promote_to_global`.

Keep SQLite knowledge inside `ActiveGraphProjectionAdapter` and store lifecycle code.
Do not add raw SQL operations or generic graph operations to the public CLI.

## Contract Version 1.0

Define strict typed models for:

- command request;
- command result;
- structured error;
- operation manifest;
- page;
- operation task;
- task event;
- destructive plan; and
- next action.

Each operation descriptor must contain:

```text
operation_id
description
input_schema
output_schema
effect
idempotency
approval_rule
plan_rule
secret_input_paths
filesystem_requirements
network_requirements
pagination
execution_mode
timeout_seconds
handler
```

Use JSON Schema 2020-12. Reject unknown request fields. Accept compatible new output
fields within the same contract major version.

Use the success and failure envelopes in the plan. `ok` states whether the CLI operation
completed. It does not state whether the resource is healthy. For example, a successful
read of a failed run exits `0` and returns the run state as `failed`.

Use:

- exit `0` for a completed command;
- exit `1` for an operation or domain failure;
- exit `2` for invalid command or input; and
- exit `130` for interruption.

Do not use process status `126` or `127` for domain failures.

## Error Rules

Map existing stable storage error prefixes to typed errors. Every failure must state:

- stable code;
- category;
- retryable;
- optional retry delay;
- whether state changed;
- structured details;
- diagnostic ID when present; and
- safe remediation operations.

An agent must not parse the English message.

Do not return physical database paths, skill roots, project roots, or artifact roots in
the agent contract. Use project, environment, run, artifact, and logical path identities.
Human diagnostic commands can show a physical path when it is required for support.

## Pagination and Bounded Output

Every list that can grow must use:

```text
limit
cursor
filter
sort
```

Return:

```text
items
next_cursor
has_more
snapshot
```

Use opaque cursors and stable ordering. Do not fetch all pages in agent mode. Large files
and artifacts return metadata, digest, size, media type, and a logical reference. Do not
put binary file data in JSON.

## Run and Task Behavior

Use the existing observer and ActiveGraph projection implementations for run discovery.
Add bounded, stable operations for:

```text
run.list
run.show
run.status
run.events
run.events.follow
run.trace
run.wait
run.cancel
run.reconcile
```

Do not return raw SQL rows or raw ActiveGraph implementation objects.

Long commands use durable tasks with these states:

```text
queued
working
input_required
approval_required
succeeded
failed
cancelled
```

Implement:

```text
task.list
task.show
task.events
task.wait
task.cancel
```

Each task event has an event ID, sequence, timestamp, type, task ID, request ID, and typed
data. Resume with an opaque last event ID. A run is an RPA domain record. A task is the
execution record for one long command. Do not merge these meanings.

## Safe Writes

Accept a caller idempotency key for all supported write operations.

Bind the key to:

- operation ID;
- project and environment;
- normalized arguments; and
- first durable result.

A retry with the same key and arguments returns the first result. Reuse with changed
arguments returns a conflict before execution. An unknown external effect must route to
reconciliation. It must not run again.

Use ActiveGraph for project and run effect identity. Add a small home-level operation
record only for an effect that no project store can own. Do not create a second general
event store.

Use plan and apply for:

- project delete;
- state-replacing project import;
- store restore;
- skill delete; and
- retention.

A plan contains an opaque ID, exact effects, blockers, approvals, state digest, expiry,
and apply operation. Apply fails before change if state changed or the plan expired.

## Secret Safety

Remove the positional secret value from the supported CLI.

Human mode uses hidden terminal input when no value source is supplied. Agent mode uses
`--value-stdin` or an approved secret-provider reference.

Never put secret values in:

- process arguments;
- JSON output;
- diagnostics;
- plans;
- task events;
- ActiveGraph;
- project files; or
- automation packages.

The current macOS provider calls `/usr/bin/security` with the value in the child process
argument list. Replace that implementation with a native Security framework adapter or
another method that does not expose the value in argv. Add secure persistent adapters for
each supported operating system. Keep the environment provider only for explicit
headless or test use.

Schemas mark secret input paths. Shared redaction uses those paths.

## Implementation Phases

### Phase 0: Preserve and qualify the baseline

1. Inspect the working tree and recent commits.
2. Commit the approved documentation separately if required.
3. Run focused current tests for CLI, storage, secrets, projects, retention, and
   automations.
4. Record any baseline failure before code changes.

### Phase 1: Contract foundation

1. Add typed contract models and contract version 1.0.
2. Add stable error mapping.
3. Add schema fixtures.
4. Add the operation catalog and small dispatcher interface.
5. Register read-only existing operations first.
6. Add interface-level tests.

Exit condition: each registered operation has valid input and output schemas, and
duplicate operation IDs fail.

### Phase 2: Agent adapter

1. Add `agent describe`, `context`, and `call`.
2. Add file and standard-input JSON requests.
3. Make agent usage errors structured.
4. Separate standard output and standard error.
5. Add JSON Lines progress format.
6. Keep human commands working through the same handlers.

Exit condition: an agent can discover and call a read operation without parsing help
text or importing Python.

### Phase 3: Complete read access

1. Add run list, show, events, follow, trace, and wait.
2. Add artifact show.
3. Add approval show.
4. Add skill list, show, and validate.
5. Add automation show, verify, and compare.
6. Add shared bounded pagination.

Exit condition: an agent with no run ID can find and explain an old run without source
access or SQL.

### Phase 4: Safe mutation and long tasks

1. Add caller idempotency keys and replay.
2. Add durable tasks and reconnect.
3. Add approval rejection.
4. Generalize plan and apply.
5. Add state-change and retry fields to all failures.
6. Add interruption, unknown-outcome, and stale-plan tests.

Exit condition: a lost response cannot silently duplicate a write, and a stale plan
cannot change state.

### Phase 5: Complete workflows and secret safety

1. Add the full skill command group.
2. Add the full automation inspection group.
3. Add safe secret input and secure platform adapters.
4. Add support bundle and diagnostic filtering.
5. Add compatibility warnings on standard error.
6. Update operator documentation.

Exit condition: all required human commands and machine operations exist, and secret
values do not appear in arguments, files, stores, output, or diagnostics.

### Phase 6: Agent-only qualification

Build the wheel. Create an empty temporary `ROI_H_HOME`. Use only the installed `roi-h`
JSON CLI. Do not import ROI-H Python modules and do not read SQLite.

The test must:

1. Discover operations and schemas.
2. Create and select a project.
3. Inspect tools.
4. Start and find a run.
5. Add an input.
6. Invoke read and write tools.
7. Handle approval.
8. Disconnect and reconnect to task events.
9. Inspect the run trace.
10. Export and verify an artifact.
11. Define, validate, and promote a skill.
12. Ship, inspect, verify, compare, dry-run, and run an automation.
13. Back up and check the store.
14. Create and reject a stale destructive plan.
15. Prove that no secret appears in output, diagnostics, process arguments, project files,
    or ActiveGraph.

Exit condition: the installed CLI gives full supported product access from an empty home.

## Test Rules

Use test-driven changes for each new contract behavior:

1. Add a failing interface-level or CLI integration test.
2. Implement the smallest complete behavior.
3. Run the focused test.
4. Run all related tests.
5. Commit one coherent change.

Tests must exercise the public module interface or installed CLI. Do not test past the
interface unless an internal safety invariant cannot be observed through it.

Keep these existing tests green:

```shell
uv run python -m pytest tests/unit/test_storage_v4.py
uv run python -m pytest tests/integration/test_ops_features.py
uv run python -m pytest tests/integration/test_recipe_run.py
uv run python -m pytest tests/integration/test_reconciliation.py
```

At the end, run:

```shell
uv run python scripts/qualify_release.py
```

Inspect the built wheel and source archive. Confirm that no project automations, custom
skills, artifacts, databases, secrets, customer files, or local agent state are present.

## Commit Order

Use small commits in this order:

1. Documentation handoff.
2. Contract models and schemas.
3. Operation catalog and read-only registration.
4. Agent describe and call adapter.
5. Common result and error handling.
6. Run discovery, events, trace, and pagination.
7. Durable tasks and reconnect.
8. Caller idempotency.
9. General plan and apply.
10. Safe secret input and provider fixes.
11. Skill lifecycle.
12. Automation inspection.
13. Agent-only acceptance test.
14. Compatibility documentation and cleanup.

Do not combine all phases into one commit. Do not push, publish, create a release, or
rewrite Git history unless the user gives separate authority.

## Stop Conditions

Stop and ask the user only when:

- an existing user change directly conflicts with the required implementation;
- a product choice changes a published operation ID or contract meaning;
- a secure persistent provider cannot be implemented on a supported operating system;
- ActiveGraph cannot provide a required durable identity or ordered projection through a
  supported interface; or
- implementation needs a new external dependency or network service.

Do not stop because the work is large. Do not replace ActiveGraph, duplicate its event
history, expose raw SQL, or bypass approval and reconciliation rules.

## Final Report

Report:

- implemented phases;
- operation and command count;
- compatibility aliases;
- test and qualification results;
- built package-content result;
- commits created;
- remaining blocked items, if any; and
- confirmation that the product is CLI-only.
