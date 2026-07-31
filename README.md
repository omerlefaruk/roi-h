# ROI-H

ROI-H is a durable automation core for external AI agents. Markdown skills guide the AI.
The AI creates modular Python phase source. ActiveGraph records run evidence. A successful
development run can ship its exact source as an immutable production package.

The external interface is in the
[operator guide](docs/external-ai-cli-operator-guide.md). The architecture is in the
[product direction](docs/product-direction.md).

## Development

```shell
uv sync --locked --group dev
uv run python scripts/qualify_release.py
```

Use `uv run python scripts/qualify_release.py --full` for the complete installer and
application checks.

User projects, source automations, packages, artifacts, browser state, databases, secrets,
and customer data belong in the ROI-H data home. They do not belong in this repository.

## External AI flow

```shell
roi-h agent context
roi-h agent describe
roi-h agent describe automation.source.put
```

The normal sequence is:

```text
skill guidance -> automation.source.put -> automation.dev.run -> automation.ship -> automation.run
```

`automation.run` is a production operation. Use it only when the user requests the live
production run.
