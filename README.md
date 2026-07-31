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
# use --full for installer and complete application checks
uv run python scripts/qualify_release.py --full
```

User projects, artifacts, browser state, databases, secrets, and custom automations belong
in the ROI-H data home. They do not belong in this repository.

## Managed projects

```shell
roi-h project create acme --log-retention 7d
roi-h project init acme
roi-h project tree
```

Projects live under `<ROI_H_HOME>/projects/` (`~/.roi-h/projects/` by default). `create`
makes a project. `init` selects and verifies an existing project. Home skills are shared by
all projects; a project skill with the same name takes precedence. The developer tree shows
run input, output, screenshots, and logs without exposing internal absolute paths.
