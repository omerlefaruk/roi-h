# ROI-H

ROI-H is a durable automation core for external AI agents. It provides a typed CLI,
isolated skills, durable runs, approvals, and immutable automation packages.

The installed external-AI interface is documented in the
[operator guide](docs/external-ai-cli-operator-guide.md). Product scope is documented in
the [product direction](docs/product-direction.md).

## Development

```shell
uv sync --locked --group dev
uv run python scripts/qualify_release.py
```

User projects, artifacts, browser state, databases, secrets, and custom automations belong
in the ROI-H data home. They do not belong in this repository.
