---
name: migrate-code-automation
description: Migrate an existing automation into modular ROI-H Python phases, verify it in development, and ship one immutable package.
---

# Migrate Code Automation

<!-- ROI-H managed agent skill -->

Read the old automation and its tests as a specification. Do not execute or change the
old automation.

Identify its inputs, business rules, secrets by name, effects, outputs, retries, and
success checks. Read ROI-H guidance with `skill.list` and `skill.show` for the capabilities
that the automation needs.

Create one source with `automation.source.put`:

- `automation.json` defines small phase modules and their `needs` dependencies.
- Independent phases can set `parallel_safe` to `true`.
- Shared code goes in a small `lib/` module.
- Phase inputs come from `context.input_dir` or `context.reference_dir`.
- Phase outputs use `context.output_path()` and the result `artifacts` map.
- Secrets use `context.secret(name)` and must be declared in the manifest.
- The final phase has role `verify` and checks the business result from prior artifacts.

Do not copy the old code structure or create one large compatibility script. Reimplement
only the required business behavior with standard Python or installed libraries.

Run the source with `automation.dev.run`. Fix the source and start a new development run
until the run has successful verification evidence. Ship that exact run with
`automation.ship`. Do not run `automation.run` in production unless the user requests the
live production run.

Report the project, development run ID, source digest, automation version, package digest,
production run ID when applicable, artifacts, warnings, and required user action.
