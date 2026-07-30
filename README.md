# ROI-H

External AI agents use the installed CLI. See the
[external AI CLI operator guide](docs/external-ai-cli-operator-guide.md).

ROI-H helps an AI discover a business workflow in development, keeps side effects under
operator approval, and turns the successful run into a versioned automation for safe
production use.

The main path is:

```text
AI-assisted discovery → approved execution → evidence → immutable automation → audited production run
```

ROI-H is built on `activegraph==1.10.0`. ActiveGraph owns lifecycle, authority decisions,
approvals, budgets, events, persistence, replay, and observability. ROI-H adds a skills
catalog, isolated skill execution, checkpointed phases, immutable automation packages,
and a compact operator interface.

The first target workflow is a browser and file workflow: open a portal, download a
report, validate or transform it, save an artifact, ship the package, and run it again.
See [`docs/product-direction.md`](docs/product-direction.md) for the product focus.

## Workflow

Capabilities live under `skills/`. Each script exports `TOOL_ID`, `Input`, `Output`, and
`run`. A request becomes a durable `rpa.invocation`; ActiveGraph behaviors authorize and
execute it, emit canonical `tool.requested` / `tool.responded` events, and materialize the
terminal `rpa.step`.

The production path is deliberately linear:

```shell
export ROI_H_BROWSER=playwright

roi-h rpa project create acme --use
roi-h rpa start --goal "…" --auto-approve --phase explore --phase solve
# invoke global tools, add a project tool if needed, and complete the phases
roi-h rpa ship --name job --version 1.0.0 --from-run RUN_ID --skill vendor
roi-h rpa env set prod
roi-h rpa run job
```

The built-in skills are `browser`, `files`, `excel`, `http`, `pdf`, `shell`, and
`feedback`. Tools execute in isolated subprocesses with run-scoped paths and only
declared secrets. Interrupted writes become `outcome_unknown` and require reconciliation.

ROI-H stores user-owned state under `~/.roi-h` by default. Reusable custom skills live
under `~/.roi-h/skills`; project skills, automation packages, artifacts, databases,
browser profiles, and secrets live under `~/.roi-h/projects/<name>/`. Set `ROI_H_HOME`
or pass `--home` to use another data home. None of this state belongs in the generic core
repository or Python distribution.
The authoritative data-home and project-storage rules are in
[`docs/project-storage-activegraph-refactor.md`](docs/project-storage-activegraph-refactor.md);
application distribution and publication boundaries are in
[`docs/distribution-and-updates.md`](docs/distribution-and-updates.md); and the installed
external-AI interface is documented in the
[`operator guide`](docs/external-ai-cli-operator-guide.md). The live operation manifest
from `roi-h agent describe` is the machine-contract authority. The retained
[primary-source research](docs/research/external-ai-cli-primary-research.md) records the
security basis for this interface.
The release, installer, update, and rollback implementation plan is in
[`docs/release-implementation-plan.md`](docs/release-implementation-plan.md), with its
handoff in
[`docs/handoffs/release-implementation-handoff.md`](docs/handoffs/release-implementation-handoff.md).

Published versions are immutable, digest-verified snapshots. `ship` is the only CLI
publishing journey; `run` executes its straight-line recipe. Advanced ActiveGraph
operations remain available through `RunSession.runtime` instead of mirrored ROI-H
wrappers and commands.

## Python API

```python
from pathlib import Path

from roi_h import AutomationRegistry, RunSession, WorkspaceCatalog

projects = WorkspaceCatalog.at(Path.home() / ".roi-h")
workspace = projects.create("demo", env="dev")
session = RunSession.create(workspace, run_id="demo")
session.start_run("Open example.com")
session.invoke("browser", "navigate", {"url": "https://example.com/"})
automations = AutomationRegistry(workspace)
```

`WorkspaceCatalog`, `RunSession`, and `AutomationRegistry` are the stable public
interfaces. The underlying ActiveGraph `Runtime` is intentionally public.

## Development

```shell
git config core.hooksPath .githooks
uv sync --locked --group dev
uv run python scripts/qualify_release.py
```

The qualification command runs the publication guard, linting, formatting, type checks,
tests, and a clean package build against every supported Python version. Releases do not
depend on GitHub Actions; see
[`docs/distribution-and-updates.md`](docs/distribution-and-updates.md) for the local
publish procedure and external automation options.

Build one self-contained macOS ARM64 candidate from the locked Python 3.12 environment:

```shell
uv run python scripts/prepare_release_candidate.py \
  --repository "$PWD" \
  --output-dir /tmp/roi-h-candidate \
  --version 0.1.1 \
  --installer-version 0.1.1 \
  --python-version 3.12.13 \
  --browser-revision chromium-1228 \
  --activegraph-version 1.10.0 \
  --channel stable
```

The candidate contains the application wheel, the external installer wheel, every locked
runtime wheel, and a digest-verified release description. The macOS ARM64 one-line
command downloads the reviewed bootstrap from GitHub:

```shell
curl -LsSf https://raw.githubusercontent.com/omerlefaruk/roi-h/main/install.sh | sh
```

Windows 11 x86-64 uses PowerShell and does not require administrator access:

```powershell
irm https://raw.githubusercontent.com/omerlefaruk/roi-h/main/install.ps1 | iex
```

The release bundle is stored as an immutable GitHub Release asset. Re-running the command
updates a managed installation; the installed equivalent is `roi-h update`.
