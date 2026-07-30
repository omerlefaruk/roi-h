# Pi iteration improvements

**Date:** 2026-07-29
**Repository:** `roi-h` at the current working tree
**Local Pi:** `@earendil-works/pi-coding-agent` 0.82.1

## Question

What should we add to Pi and this repository to make ROI-H development, research, review,
and release work faster without adding unsafe or redundant tooling?

## Short answer

Add these first:

1. **`pi-subagents`** for parallel scout, planner, reviewer, and research work.
2. **`pi-web-access`** for web search, page extraction, GitHub cloning, and source checks.
3. **Project prompt templates** for the repeatable ROI-H loops: implement, diagnose,
   review, research, and qualify.
4. **A small local safety extension** based on Pi's official examples: protected paths,
   dirty-repository checks, and completion notifications.
5. **A Pi-to-ROI-H bridge tool** later, so Pi can use the typed `roi-h agent` contract
   instead of composing shell commands.

Do not install every package. Pi extensions and packages have the full permissions of the
Pi process, and Pi has no built-in sandbox. Review and pin each package before enabling it.

## Current baseline

- The local Pi settings use `openai-codex`, `gpt-5.6-luna`, and `xhigh` thinking.
- No global Pi extension or package is configured in `~/.pi/agent/settings.json`.
- Pi already discovers the existing user skills under `~/.agents/skills/`. This includes
  skills for TDD, diagnosis, codebase design, research, review, and refactoring. Installing
  another broad skill collection would duplicate much of the current setup.
- The repository has one short `AGENTS.md` rule, but no project `.pi/` prompts, agents,
  extensions, or settings.
- ROI-H already has a strong development command: `uv run python
  scripts/qualify_release.py`. It also has a typed external-AI CLI plan and an agent
  operation catalog. These should be the basis of Pi integrations.

## Primary-source findings

### 1. Pi is designed to be extended, not made large

Pi's official documentation says that workflow-specific behavior belongs in extensions,
skills, prompt templates, and packages. It explicitly leaves sub-agents, plan mode, to-dos,
permission popups, and background bash outside the core. [Pi usage and design principles](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/usage.md)

Pi extensions can register tools and commands, intercept lifecycle events, change context,
provide UI, and persist session state. [Pi extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)

**Implication:** Add narrow workflow resources. Do not fork Pi or add a large wrapper around
its core.

### 2. Pi has useful safety boundaries, but they are not a sandbox

Pi's project trust controls whether project-local resources load. The official security
document says this is not a sandbox: tools and extensions still run with the permissions of
the Pi process. [Pi security](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md)

The package documentation also warns that extensions and skills can execute arbitrary code
or instruct the model to perform actions. [Pi packages](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md)

**Implication:** Keep third-party packages pinned. Use project trust carefully. Keep a local
protected-path and destructive-command policy even when the repository is trusted.

### 3. Parallel delegation is the highest-value speed improvement

The official subagent example runs child Pi processes with isolated context windows and
supports single, parallel, and chained work. [Official subagent example](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions/subagent)

The maintained `pi-subagents` package provides the same workflow as an installable package,
with built-in scout, researcher, planner, worker, reviewer, context-builder, oracle, and
delegate agents. It supports parallel reviewers, background work, chains, and review loops.
[pi-subagents source](https://github.com/nicobailon/pi-subagents)

**Recommendation:** Install `pi-subagents` first. Use a small model for scout and reviewer
roles when quality is acceptable. Use the main model for the worker and difficult decisions.

### 4. Web research should be a first-class Pi capability

The `pi-web-access` package provides web search, content extraction, GitHub repository
cloning, PDF extraction, YouTube understanding, and a `source_check` operation that returns
machine-readable claim status and passage citations. [pi-web-access source](https://github.com/nicobailon/pi-web-access)

Pi's own package gallery lists `pi-web-access` and `pi-subagents` as available packages.
[Pi package gallery](https://pi.dev/packages)

**Recommendation:** Install `pi-web-access` after a source review. It directly supports the
research and product-documentation work needed by ROI-H. Use domain filters and prefer
first-party sources for technical decisions.

### 5. Prompt templates are a low-risk way to remove repeated instructions

Pi loads Markdown prompt templates from project `.pi/prompts/` and exposes them as slash
commands. Templates support arguments and defaults. [Pi prompt templates](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/prompt-templates.md)

**Recommendation:** Add project templates for:

- `/implement`: inspect the relevant module, write tests first, implement, run focused
  checks, then report the diff;
- `/diagnose`: reproduce, collect evidence, identify the smallest failing boundary, and
  fix only after the cause is clear;
- `/review`: review the current diff for defects, missing tests, security, and unnecessary
  complexity;
- `/research`: use first-party sources, record citations in `docs/research/`, and separate
  facts from recommendations;
- `/qualify`: run the smallest relevant checks, then `scripts/qualify_release.py` when the
  change affects release behavior.

### 6. A durable work list fits this repository

The `pi-worklist` package separates branch-aware session tasks from repository-wide project
goals. Project goals are stored in `.pi/worklist.json`, and the package provides a model
 tool, dashboard, external CLI, locking, atomic replacement, and typed conflicts.
[pi-worklist source](https://github.com/max-miller1204/pi-worklist)

**Recommendation:** Add this as a second-wave package if work is often interrupted across
sessions. It is a better fit for ROI-H than an informal todo file because ROI-H already uses
durable runs, phases, feedback, and reconciliation.

### 7. Deterministic workflows are useful, but should not be the first addition

`pi-extensible-workflows` provides named multi-agent workflows with parallel work, approval
pauses, retries, resume, budgets, checkpoints, and worktrees. [pi-extensible-workflows source](https://github.com/vekexasia/pi-extensible-workflows)

**Recommendation:** Evaluate it only after `pi-subagents` exposes a repeated workflow that
needs resumability or approval. Do not install both at the start. The package overlaps with
ROI-H's own durable workflow and task model.

### 8. MCP should remain optional

`pi-mcp-adapter` reduces MCP context cost by exposing discovery through one proxy tool and
starting servers lazily. It can read shared and project MCP configurations. [pi-mcp-adapter source](https://github.com/nicobailon/pi-mcp-adapter)

**Recommendation:** Add it only when a required integration exists as an MCP server. ROI-H
already has browser, files, HTTP, PDF, spreadsheet, shell, and feedback skills. Adding MCP
now would add configuration and trust surface without solving a current gap.

## Recommended implementation order

### Phase 1: fast and safe

1. Review and pin `pi-subagents` and `pi-web-access`.
2. Install them at user scope if they are personal tools, or project scope with
   `pi install -l` if the team should share the setup.
3. Add project prompt templates under `.pi/prompts/`.
4. Expand `AGENTS.md` with the repository commands, test-first rule, publication boundary,
   and the rule to use `scripts/qualify_release.py` for release changes.
5. Add a small project-local extension for protected paths and a completion notification.

Suggested first test installs:

```shell
pi -e npm:pi-subagents@0.37.2
pi -e npm:pi-web-access@0.15.0
```

After review, install the accepted packages with exact versions:

```shell
pi install npm:pi-subagents@0.37.2
pi install npm:pi-web-access@0.15.0
```

Pinned npm versions are preferable for reproducible agent behavior. Pi's package manager
supports temporary loading, npm and Git sources, project-local installs, resource filters,
and updates. [Pi package management](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md)

### Phase 2: connect Pi to ROI-H

Build one project-local custom tool that:

1. calls `roi-h agent describe` to discover operations and schemas;
2. accepts an operation ID and validated JSON arguments;
3. sends structured input through `roi-h agent call --input -`;
4. returns bounded JSON to the model;
5. never exposes SQLite paths, secrets, or raw project storage; and
6. uses the ROI-H idempotency and approval rules.

This follows the existing ROI-H design: the installed CLI is the external-AI interface and
all callers must use the typed operation catalog. [ROI-H external-AI plan](../external-ai-cli-plan.md)

### Phase 3: measure before adding more

Record a small baseline for ten tasks:

- time to first useful change;
- number of model turns;
- focused-test failures before success;
- review findings after implementation;
- context compactions; and
- failed or repeated tool calls.

Compare the baseline with the Phase 1 setup. Add `pi-worklist`, deterministic workflows, or
context optimization only when the measurements show a real bottleneck.

## Not recommended now

- **A broad skill bundle:** existing `~/.agents/skills` already covers most coding,
  research, review, and design workflows.
- **`context-mode` immediately:** it claims large context savings, but it adds a separate
  MCP and indexing system and uses a non-standard ELv2 license. Benchmark it in a disposable
  project before adoption.
- **Automatic commit-on-exit:** it can hide review boundaries and create commits without an
  explicit user decision.
- **Automatic test execution after every turn:** it can waste time and make agent turns
  noisy. Use `/qualify` or a focused test prompt instead.
- **Unpinned packages or `@latest`:** Pi packages run with full local permissions.

## Decision

Start with `pi-subagents`, `pi-web-access`, project prompt templates, and a small safety
extension. Then add the ROI-H bridge. Defer MCP, context indexing, and deterministic workflow
orchestration until a measured need appears.

## Sources

- [Pi README and release notes](https://github.com/earendil-works/pi)
- [Pi usage](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/usage.md)
- [Pi extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)
- [Pi skills](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md)
- [Pi packages](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md)
- [Pi security](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md)
- [Pi prompt templates](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/prompt-templates.md)
- [Pi subagent example](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions/subagent)
- [Pi package gallery](https://pi.dev/packages)
- [pi-subagents](https://github.com/nicobailon/pi-subagents)
- [pi-web-access](https://github.com/nicobailon/pi-web-access)
- [pi-worklist](https://github.com/max-miller1204/pi-worklist)
- [pi-extensible-workflows](https://github.com/vekexasia/pi-extensible-workflows)
- [pi-mcp-adapter](https://github.com/nicobailon/pi-mcp-adapter)
- [Agent Skills specification](https://agentskills.io/specification)
