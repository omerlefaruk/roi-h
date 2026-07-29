---
name: rpa-harness
description: >
  Operator manual: multi-project homes, phase roles, explore → project skill →
  ship (auto-distill + push), run overrides, secrets, feedback after every run,
  and how to debug prod failures without hand-editing recipes.
version: 1.0.0
---

# RPA harness — operator playbook

You are the **operator**. Work in **dev**, discover with global tools, stabilize as
**project-local skills**, then **`ship --from-run`**. Never
hand-edit recipes. After each `rpa run`, **feedback.record** runs automatically.

## Default workflow

```text
0. project create|use + env set dev
1. tools
2. start --goal "…" \
     --phase explore:role=explore \
     --phase solve:role=work \
     --phase verify:role=verify
3. explore: browser.* / http.* / files.* → artifacts → phase end
4. Missing?  custom --skill S --tool T --script …
5. solve: invoke S T (--force or approve once) → evidence artifacts
6. status  (see next_approve if gated)
7. ship --name JOB --version X.Y.Z --from-run RUN [--skill S]
   → distill + package + push + prod dry-run
8. env set prod && run JOB
   headed watch: run JOB --set headless=false
```

For bounded, open-ended dev exploration, Codex CLI can choose steps while ROI-H keeps
execution authority:

```bash
roi-h rpa adapt --run-id RUN --auto-approve \
  --goal "Inspect this unfamiliar portal and find the invoice area" \
  --tool browser.navigate --tool browser.snapshot --tool browser.click
```

`adapt` is dev-only, requires an explicit tool allowlist, rejects destructive tools, and
stops at `--max-turns` (default 6). Codex runs ephemerally and read-only; its typed
decisions are recorded as `llm.requested` / `llm.responded`, while every selected tool
still becomes a normal durable `rpa.invocation` and `rpa.step`.

**Distill (always on publish/ship):** drops `role=explore` phases, empty phases,
and `browser.*` once project tools succeeded. Writes `distill.json` beside recipe.

## Projects

```bash
roi-h rpa project init
roi-h rpa project create acme --use
roi-h rpa project list|show|use NAME
roi-h rpa project rename OLD NEW
roi-h rpa project delete NAME --force   # destructive
```

## Environments

| Env | Purpose |
|---|---|
| `dev` | Build; project tools need approval unless `--auto-approve` / `--force` |
| `prod` | Unattended `rpa run` |

```bash
roi-h rpa env set dev|prod
```

Data home: `~/.roi-h` by default; override with `ROI_H_HOME` or `--home`.

Shared user skills: `$ROI_H_HOME/skills/`

Project layout:
`$ROI_H_HOME/projects/<name>/{dev,prod}/{rpa.sqlite,skills,artifacts,automations}`

Secrets: `$ROI_H_HOME/projects/<name>/secrets.json`

Feedback: `$ROI_H_HOME/projects/<name>/feedback/`

## Phase roles

```bash
--phase explore:role=explore
--phase solve:role=work:download and fill
--phase verify:role=verify
# shorthand: --phase explore  (name implies explore role)
```

| Role | Distill |
|---|---|
| `explore` | Always dropped from prod recipe |
| `work` | Kept (minus discovery tools when project skills exist) |
| `verify` | Kept only if it has steps; empty verify is soft (no fake artifacts) |

## Global skills

| Skill | Tools (high level) |
|---|---|
| `browser` | navigate, snapshot, click, fill, type, press, select, download, screenshot, session_status, session_stop |
| `files` | read, write, copy, glob, hash |
| `excel` | read_rows, write_rows |
| `http` | get, post, download |
| `pdf` | extract_text (needs `pypdf`) |
| `shell` | run (**always** requires approval) |
| `feedback` | record, list (auto after `rpa run`) |

Browser env: `ROI_H_BROWSER=playwright|stub`, `ROI_H_BROWSER_HEADED=1`, `ROI_H_BROWSER_SLOW_MO=ms`.

## Secrets

```bash
roi-h rpa secret set PORTAL_USER alice
roi-h rpa secret set PORTAL_PASS '…'
roi-h rpa secret list          # names only
# in tool args / recipes:
#   "user": "{{secret.PORTAL_USER}}"
```

Values inject as `ROI_H_SECRET_<NAME>` during tool execution; never printed by `list`.

## Approvals

```bash
roi-h rpa invoke …                 # may return pending_approval
roi-h rpa status                   # next_approve.command is ready to copy
roi-h rpa approve --run-id RUN ID --by human
roi-h rpa invoke … --force         # skip gate once
roi-h rpa start … --auto-approve   # AI-friendly default
```

## Ship / run

```bash
# one command after a green run
roi-h rpa ship --name weekly --version 1.0.0 --from-run RUN --skill finance
# options: --no-prod-dry-run | --prod-run | --full-transcript | --set headless=false

roi-h rpa env set prod
roi-h rpa run weekly
roi-h rpa run weekly --set headless=false   # override every invoke arg
roi-h rpa run weekly --dry-run
roi-h rpa run weekly --no-feedback          # skip feedback.record
```

Package: `automations/<name>/<ver>/{manifest.json,recipe.json,distill.json,skills/}`.

## Feedback (improve the codebase)

After every live `rpa run`, harness calls `feedback.record` with ok/fail summary.
Review:

```bash
roi-h rpa invoke --run-id RUN feedback list --args '{"limit":20}'
# or read: $ROI_H_HOME/projects/<project>/feedback/feedback.jsonl
```

When authoring manually, record lessons:

```bash
roi-h rpa invoke --run-id RUN feedback record --args '{
  "automation":"weekly",
  "ok":true,
  "severity":"suggestion",
  "suggestions":["add browser.wait_for_text global tool"],
  "notes":"portal spinner needs explicit wait"
}' --force
```

Use feedback to grow global skills and fix harness friction — not to hand-edit recipes.

## Debug prod failures

Prod is a **closed** `rpa run` of a frozen package. Inspect the failed run, reproduce
headed, fix the **skill** (or harness) in **dev**, re-**ship**. Never hand-edit
`recipe.json`.

```text
prod fail JSON
  → status / artifacts / events / trace
  → headed re-run (--set headless=false)
  → feedback.jsonl
  → fix skill in dev
  → ship new version
  → re-run prod
```

### 1. Capture the failure

`rpa run` returns structured JSON. On fail note:

| Field | Meaning |
|---|---|
| `ok: false` | Run stopped |
| `run_id` | Durable ActiveGraph run id (use for status) |
| `failed_phase` | Phase that blew up |
| `error` | Step/phase error string |
| `executed` | Steps that ran (status, tool, output/error) |
| `feedback` | Auto `feedback.record` result |

```bash
roi-h rpa env set prod
roi-h rpa run JOB   # copy run_id from JSON
# also: $ROI_H_HOME/projects/<project>/feedback/feedback.jsonl
```

### 2. Inspect that run

```bash
roi-h rpa status --run-id RUN
# → error_steps, phases, step errors/outputs, artifacts_root

roi-h rpa artifact list --run-id RUN
ls "$ROI_H_HOME/projects/<project>/prod/artifacts/RUN/"
ls "$ROI_H_HOME/projects/<project>/prod/artifacts/RUN/phases/"

roi-h rpa invoke --run-id RUN feedback list --args '{"limit":20}' --force
```

Advanced event and trace inspection uses `RunSession.runtime` directly; ROI-H does not
mirror ActiveGraph's observability API in its CLI.

Compare the frozen package (do not edit):

```text
$ROI_H_HOME/projects/<project>/prod/automations/JOB/<ver>/{recipe,distill,manifest}.json
$ROI_H_HOME/projects/<project>/prod/automations/JOB/<ver>/skills/
```

### 3. Reproduce with eyes on the UI

Same package, no republish:

```bash
roi-h rpa env set prod
roi-h rpa run JOB --set headless=false
# or:
ROI_H_BROWSER_HEADED=1 ROI_H_BROWSER_SLOW_MO=200 roi-h rpa run JOB

roi-h rpa run JOB --dry-run   # plan only
```

`--set key=value` overlays every invoke arg (repeatable).

### 4. Classify

| Symptom | Likely cause | Fix where |
|---|---|---|
| Selector / DOM / timing | Brittle project skill or missing wait | dev skill script → re-ship |
| Missing secret / `{{secret.X}}` | Secret not set for project | `roi-h rpa secret set …` |
| Approval mid-run | `--no-force` or `shell.*` | rarely in prod (default force) |
| Budget hit | max_events / max_tool_calls / max_seconds | raise budget on `run` |
| Distill dropped a needed step | Recipe too thin | re-run **dev** with evidence, ship again |
| Package skill ≠ dev skill | Push lag | re-`ship` / push latest version |

### 5. Fix loop (dev → ship → prod)

```bash
roi-h rpa env set dev
roi-h rpa start --goal "debug JOB failure: …" --auto-approve \
  --phase explore:role=explore --phase solve:role=work

# edit: $ROI_H_HOME/projects/<project>/dev/skills/<skill>/
# re-invoke project tool with evidence artifacts

roi-h rpa ship --name JOB --version X.Y.Z --from-run DEV_RUN --skill S

roi-h rpa env set prod
roi-h rpa run JOB --set headless=false
```

Record lessons for the next operator/AI:

```bash
roi-h rpa invoke --run-id RUN feedback record --args '{
  "automation":"JOB",
  "ok":false,
  "severity":"bug",
  "suggestions":["add wait_for_text before fill"],
  "notes":"prod fail: spinner left field disabled"
}' --force
```

### Notes

- Each `rpa run` is a **fresh** closed execution. Use durability for **inspection**
  (`status` / `trace` / artifacts), not interactive mid-recipe debugging in prod.
- `--from-handoff` can seed completed phases; it is not a general “resume failed
  prod step” debugger.
- Prefer growing **user-shared skills** (e.g. waits) when many jobs share the same
  friction. Packaged core skills are immutable.

## Naming

| Concept | Meaning |
|---|---|
| project | Isolation unit under `projects/<id>/` |
| phase role | explore \| work \| verify (drives distill) |
| project skill | Tool under env `skills/` |
| distill | Auto strip explore noise at publish/ship |
| ship | publish + push (+ optional prod dry-run) |
| recipe | Frozen control flow for `rpa run` |
| feedback | Post-run improvement signal |

## End-of-job

- status: phases, artifacts, next_approve, promote_advice  
- ship or publish --from-run (read distill report)  
- prod run (+ feedback entry written)  
- on fail: **Debug prod failures** (status → headed re-run → fix skill → re-ship)
