# ROI-H

External AI agents use the installed CLI. See the
[external AI CLI operator guide](docs/external-ai-cli-operator-guide.md).

ROI-H is a durable RPA automation core on `activegraph==1.10.0`.
ActiveGraph owns lifecycle, authority decisions, approvals, budgets, events, persistence,
replay, and observability. ROI-H adds a skills catalog, isolated skill execution,
checkpointed phases, immutable automation packages, and a compact operator interface.

## Workflow

Capabilities live under `skills/`. Each script exports `TOOL_ID`, `Input`, `Output`, and
`run`. A request becomes a durable `rpa.invocation`; ActiveGraph behaviors authorize and
execute it, emit canonical `tool.requested` / `tool.responded` events, and materialize the
terminal `rpa.step`.

For open-ended development work, an opt-in ActiveGraph `LLMBehavior` can ask the local
Codex CLI to choose the next tool. Codex only returns a typed decision; ROI-H still
executes that decision through the same authority, approval, invocation, and step
lifecycle:

```shell
roi-h rpa adapt --run-id RUN_ID --auto-approve \
  --goal "Inspect the page and identify the download link" \
  --tool browser.navigate --tool browser.snapshot
```

Adaptive execution is dev-only, requires an explicit tool allowlist, rejects destructive
tools, and is bounded by `--max-turns`. It reuses saved Codex CLI authentication and runs
Codex ephemerally in a read-only sandbox. Set `ROI_H_CODEX_BIN` to override the executable
or `ROI_H_CODEX_MODEL` to select a model.

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
[`docs/distribution-and-updates.md`](docs/distribution-and-updates.md); and the stable
external-AI CLI plan is in
[`docs/external-ai-cli-plan.md`](docs/external-ai-cli-plan.md). Its paste-ready
implementation handoff is in
[`docs/handoffs/external-ai-cli-implementation-handoff.md`](docs/handoffs/external-ai-cli-implementation-handoff.md).
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
session.adapt(
    "Inspect the current page and summarize it",
    tools=["browser.snapshot"],
)
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
