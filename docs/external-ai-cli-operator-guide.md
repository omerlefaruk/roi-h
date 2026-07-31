# External AI CLI Operator Guide

ROI-H has one typed external interface. Use a native bridge when available. Otherwise use
the installed `roi-h agent` commands. Do not access ActiveGraph SQLite or project storage
directly.

## Discover and call operations

```shell
roi-h agent context
roi-h agent describe
roi-h agent describe automation.source.put
roi-h agent call automation.source.put --input request.json
```

Standard output is one JSON result. Success exits with code `0`. An operation failure exits
with code `1`. Invalid syntax or input exits with code `2`.

The live operation manifest is the authority for arguments, effects, idempotency, plans,
secrets, pagination, tasks, and time limits. Use a stable `idempotency_key` for each write.
After a lost response, retry the same operation, context, arguments, and key.

## Guidance skills

Use `skill.list` and `skill.show`. A skill contains only `SKILL.md` and optional Markdown
references. It supplies guidance to the AI. It does not define actions and it never runs a
script.

## Create modular source

Use `automation.source.put` in development. Supply:

- `name`;
- a manifest object that follows the live schema; and
- a `files` object that maps portable relative paths to text source.

Create small phase modules. Put shared code in `lib/`. Declare dependency edges with
`needs`. Set `parallel_safe` only when concurrent phase execution is safe. Include at least
one phase with role `verify`.

Each module exports:

```python
def run(context):
    path = context.output_path("result.txt")
    path.write_text("result", encoding="utf-8")
    return {
        "summary": {"verified": True},
        "artifacts": {"result": "result.txt"},
    }
```

The context supplies `input_dir`, `reference_dir`, `work_dir`, `output_dir`, dependency
artifacts, phase and attempt identities, and `secret(name)` for declared secrets.

## Development, shipping, and production

1. Run editable source with `automation.dev.run` in `dev`.
2. Inspect `run.status`, `run.events`, `run.trace`, and artifacts.
3. Change the source and use a new run ID when verification fails.
4. Ship the successful run with `automation.ship`.
5. Verify the immutable package with `automation.verify`.
6. Use `automation.run` in `prod` only when the user requests a production run.

ROI-H freezes source before a run. Shipping uses only that frozen tree and its ActiveGraph
verification evidence. Production verifies the package and source digests before execution.

## Inputs and outputs

`automation.dev.run` and `automation.run` can materialize input files through their typed
`inputs` argument. Phase code reads them from `context.input_dir`. Input names and hashes
are recorded in ActiveGraph.

Phase files become durable artifacts only when the phase returns them in `artifacts`.
Downstream phases read those artifact paths from `context.dependencies`.

## Secrets and destructive actions

Never put a secret value in a request JSON document, source file, manifest, log, plan, or
artifact. Use `secret.set` with its separate secure standard-input channel. Phase source
uses `context.secret(name)`.

For a destructive action, call its `.plan` operation, review the effects and blockers, and
call `.apply` only after the user approves the plan.

## Long tasks

Operations such as store backup return a task ID. Follow it with `task.show`, `task.events`,
or `task.wait`. Continue from returned cursors and keep reads bounded.
