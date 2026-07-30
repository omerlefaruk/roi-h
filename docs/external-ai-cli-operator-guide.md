# External AI CLI Operator Guide

ROI-H has one external AI contract. The installed `roi-h` command is the universal
transport. A native bridge can expose the same live operation IDs and schemas, but it
must not add product behavior or policy.

Codex, Claude, Gemini, and generic shell agents use the same JSON contract. They do not
need source access. They must not read the ActiveGraph SQLite file.

## Discover the interface

```shell
roi-h agent describe
roi-h agent describe run.start
roi-h agent context --home "$ROI_H_HOME"
```

Standard output contains one JSON result. A successful command exits with code `0`. An
operation failure exits with code `1`. Invalid agent syntax or input exits with code `2`.
Standard error stays empty in agent mode.

## Call an operation

Create a request file:

```json
{
  "schema_version": "1.0",
  "request_id": "req_example_1",
  "idempotency_key": "example-project-create-1",
  "context": {},
  "arguments": {
    "home": "/path/to/data-home",
    "name": "example"
  }
}
```

Call the operation:

```shell
roi-h agent call project.create --input request.json
```

Use `--input -` only when standard input contains the request JSON:

```shell
printf '%s' "$REQUEST_JSON" | roi-h agent call project.list --input -
```

## Safe retries

Use a stable `idempotency_key` for each write. If a response is lost, send the same
operation, context, arguments, and key again. ROI-H returns the first result. If the
arguments change, ROI-H returns `request.idempotency_conflict`.

Do not retry an unknown write with a new key. First inspect the run, task, approval,
artifact, or target state.

## Long tasks

A long operation returns a task identity. Keep that identity.

```shell
roi-h agent call task.show --input task-show.json
roi-h agent call task.events --input task-events.json
roi-h agent call task.wait --input task-wait.json
roi-h agent call task.cancel --input task-cancel.json
```

Use the last event ID in the next `after` argument. Do not read all pages without a
limit.

## Background operations

`store.backup` starts a detached durable task. The first response contains a queued task
ID. Do not assume that the backup is ready. Wait for the terminal state:

```shell
roi-h agent call store.backup --input backup-request.json
roi-h agent call task.wait --input task-wait-request.json
```

Use `task.events` to reconnect to the task event stream. A task can be `queued`,
`working`, `succeeded`, `failed`, or `cancelled`. A working task is allowed to finish
when cancellation cannot safely stop its database operation.

## Destructive operations

Call the `.plan` operation first. Review its exact effects, blockers, state digest, and
expiry time. Then call the related `.apply` operation with the returned `plan_id`.

Examples include:

- `project.delete.plan` and `project.delete.apply`
- `store.restore.plan` and `store.restore.apply`
- `skill.delete.plan` and `skill.delete.apply`
- `retention.plan`, `retention.show`, and `retention.apply`

An expired plan returns `plan.expired`. A changed target returns `plan.state_changed`.
Create a new plan after either result.

## Secrets

Never put a secret value in a command argument or request JSON file.

Create a request file that contains the secret name, but not its value. Then use a
separate standard input channel:

```shell
printf '%s' "$TOKEN" |
  roi-h agent call secret.set --input secret-set-request.json --secret-stdin
```

For the human CLI, use:

```shell
printf '%s' "$TOKEN" | roi-h rpa secret set TOKEN --value-stdin
roi-h rpa secret set TOKEN
```

The second human command uses a hidden terminal prompt.

## Minimum agent sequence

1. Call `system.version`, `system.describe`, and `system.context`.
2. Select or create a project.
3. Discover tools with `tool.list` and `tool.show`.
4. Start or find a run.
5. Use stable idempotency keys for writes.
6. Read approvals, ordered events, and bounded traces.
7. Use plan-and-apply for destructive work.
8. Use task identities for long work.
9. Read redacted diagnostics or create a support bundle when an operation fails.

The operation manifest is the authority for effects, approvals, idempotency, secret
inputs, pagination, and timeouts.

## Skill contract

A `SKILL.md` file contains only guidance that the live manifest cannot express:

```markdown
---
name: skill-name
description: When and why to use this skill.
---

One short domain procedure, when required.
```

It must not copy operation schemas, command layouts, safety rules, model names, or
orchestration. Each executable tool module defines strict Pydantic `Input` and `Output`
models, `run(args: Input) -> Output`, and its effect, idempotency, approval, production,
timeout, secret, network, and filesystem metadata. Missing custom-tool security metadata
fails closed. ROI-H inspects custom modules in a bounded worker and never imports them in
the parent process.

The current development worker is process isolation, not an operating-system sandbox.
Custom skill Python is trusted local code; declared network and filesystem metadata guides
the broker but cannot confine arbitrary Python. Production recipe replacement stays
blocked until a verified, effect-restricted runner enforces those capabilities at the
process boundary.
