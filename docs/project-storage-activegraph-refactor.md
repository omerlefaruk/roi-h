# ROI-H Project Storage, ActiveGraph, Portability, and Operations Refactor

**Status:** Proposed implementation contract
**Audience:** Maintainers and implementation agents
**Last updated:** 2026-07-29
**Scope:** ROI-H data home, projects, environments, runs, paths, ActiveGraph persistence,
artifacts, diagnostics, secrets, export/import, migration, retention, recovery, and the
operator CLI

## 1. Purpose

This document defines the target architecture and dependency-ordered refactor plan for
ROI-H project storage.

The current system already has several correct foundations:

- application code and user-owned state are separated;
- `~/.roi-h` is the default user-owned data home;
- a project has isolated `dev` and `prod` environments;
- ActiveGraph is the durable source of truth for run lifecycle and authority;
- automation packages are immutable and digest-verified;
- skills execute in subprocesses with scoped environment variables;
- artifact and phase records can be reconciled with files; and
- the observer is read-only.

The missing piece is a single storage model that every caller follows. Physical paths are
currently constructed in multiple modules and some tools accept arbitrary paths.
Workspaces, artifacts, handoffs, browser state, automation packages, secrets, feedback,
and SQLite maintenance do not yet share one lifecycle contract. Whole-project export and
import are also absent.

This refactor makes projects:

- portable across machines;
- safe to back up while ROI-H is installed and running;
- explicit about what is authoritative and what is disposable;
- recoverable after interrupted file and database operations;
- isolated from the generic package and public GitHub repository;
- observable without duplicating ActiveGraph history into a second log;
- safe for concurrent runs within the supported SQLite envelope; and
- ready for a future non-SQLite ActiveGraph adapter without leaking database details
  through every caller.

This document is intentionally implementation-grade. It defines terminology, invariants,
interfaces, layouts, state transitions, CLI behavior, migration rules, failure behavior,
tests, acceptance criteria, and an ordered delivery plan.

## 2. Relationship to Other Documents

[`distribution-and-updates.md`](distribution-and-updates.md) remains authoritative for:

- installing and updating the ROI-H application;
- application release artifacts;
- PyPI publication;
- one-line bootstrap installers;
- rollback between installed application versions; and
- the boundary between generic product code and user-owned state.

This document is authoritative for the internal organization and lifecycle of the
user-owned state below `ROI_H_HOME`.

When accepted, the target layout in this document replaces the conceptual project layout
in the distribution document. The distribution document should link here instead of
maintaining a second detailed copy.

## 3. Executive Decisions

1. **ActiveGraph remains the sole authority for durable run history.**
   Run lifecycle, phases, invocations, authority decisions, approvals, outcomes,
   artifacts, feedback tied to a run, reconciliation results, and retention decisions
   are ActiveGraph events or typed objects derived from those events.

2. **ROI-H keeps only a narrow diagnostic fallback outside ActiveGraph.**
   Diagnostic records exist for failures that ActiveGraph cannot reliably record, such
   as database-open failures, migration failures, startup crashes, installer/update
   failures, and bounded subprocess stderr. ROI-H does not dual-write all events to a
   JSONL log.

3. **One ActiveGraph store remains the default per project environment.**
   All runs in `<project>/<environment>` share one store. This preserves run discovery,
   replay, lineage, and forks. Store access is hidden behind a lifecycle module for
   health, backup, restore, migration, and compaction; ROI-H does not reimplement
   ActiveGraph event semantics.

4. **Paths persisted into ActiveGraph and recipes are logical, not physical.**
   Machine-specific absolute paths must not appear in new recipes, artifact records,
   phase manifests, or project exports.

5. **A run owns its mutable workspace and durable artifacts.**
   Project-wide `input` and `output` folders are rejected because concurrent runs would
   collide. Stable, reusable source material belongs to project `reference/`.

6. **Project definitions are separate from environment execution state.**
   Editable project skills and stable references belong to the project. Environment
   directories contain stores, run data, runtime state, and environment policy.
   Production execution reads only immutable automation-package snapshots.

7. **Automation packages are stored once and promoted by immutable reference.**
   Shipping creates a digest-verified package. Environment channels point to that
   package. Promotion changes a small channel reference instead of creating divergent
   copies.

8. **Secret values do not live in project JSON files.**
   Secret values move to a `SecretStore` adapter, using the macOS Keychain initially.
   Project manifests contain required secret names and provider references only.
   Development and production secret namespaces are isolated.

9. **Whole-project export/import is a first-class CLI journey.**
   Export uses a consistent database snapshot, validates every included file, excludes
   sensitive and disposable state by default, and creates a manifest with hashes.
   Import stages, validates, migrates, and atomically activates a project.

10. **Destructive maintenance is plan-first.**
    Retention, garbage collection, project replacement, database compaction, and
    irreversible cleanup produce a persisted dry-run plan before applying changes.

11. **The observer consumes the storage and projection interfaces.**
    It must not independently hardcode project layouts or spread knowledge of
    ActiveGraph's SQLite schema across the codebase.

12. **The layout is explicitly versioned.**
    ROI-H detects an old or newer layout before opening a store. Migrations are backed
    up, resumable, idempotent, and never silently merge ambiguous user data.

## 4. Non-Goals

This refactor does not:

- replace ActiveGraph with a custom ROI-H event system;
- mirror the full ActiveGraph CLI through ROI-H;
- introduce a separate graph database for current-state projections;
- introduce Postgres before local concurrency measurements require it;
- introduce content-addressed artifact deduplication before storage measurements justify
  the complexity;
- implement cloud synchronization;
- make project exports a secret-backup mechanism;
- make untrusted skills safe through path conventions alone;
- guarantee secrecy against a user or process that already has access to the same OS
  account;
- preserve arbitrary physical paths embedded in legacy recipes as portable behavior; or
- auto-delete durable history or artifacts without an explicit operator policy.

## 5. Current-State Findings

The implementation plan is grounded in the current code, not a hypothetical greenfield
system.

### 5.1 Workspace

`src/roi_h/harness/workspace.py` currently:

- resolves home as explicit `--home`, then `ROI_H_HOME`, then `~/.roi-h`;
- resolves a selected project and environment;
- stores project configuration in `config.json`;
- creates `reference/`;
- creates `dev/` and `prod/`;
- places `rpa.sqlite`, `skills/`, `artifacts/`, and `automations/` inside each environment;
- creates shared skills in `<home>/skills`; and
- performs immediate recursive project deletion when forced.

The `Workspace` dataclass exposes physical `Path` values directly. That is useful inside
the storage implementation but allows every caller to construct additional paths.

### 5.2 Run filesystem

`src/roi_h/harness/invocation_runtime.py` currently places:

```text
<environment>/artifacts/<run-id>/_workspace/
<environment>/artifacts/<run-id>/runtime/browser-session.json
```

The worker receives these as `ROI_H_RUN_DIR` and `ROI_H_BROWSER_STATE`.

The `_workspace` directory has no enforced `input`, `work`, `output`, and `tmp`
substructure. Its placement below `artifacts/` also blurs mutable scratch files and
durable artifacts.

### 5.3 Tool paths

Core file, Excel, and browser tools currently accept strings and often construct
`Path(args.path)` directly. Relative paths happen to be run-scoped because the subprocess
working directory is the run workspace, but the interface still accepts absolute paths
and does not consistently validate declared filesystem roots.

`ToolInfo.filesystem_roots` exists as metadata, but the runtime does not yet enforce it.

### 5.4 Recipes

Recipe distillation carries invocation arguments forward and identifies path-like output
fields heuristically. A successful development run can therefore produce a recipe that
contains a machine-specific absolute path.

The production package can be internally immutable while still being operationally
non-portable.

### 5.5 Artifacts and handoffs

`src/roi_h/harness/phases.py` creates phase handoff packages.

`src/roi_h/harness/reconcile.py` already compares ActiveGraph artifact and phase records
with files and can perform bounded repairs. This is the correct recovery foundation.

Remaining weaknesses include:

- physical paths persisted in artifact records;
- artifact copying without a complete stage, sync, rename, event protocol;
- mutable workspace files stored below the artifact root;
- duplicated phase artifact bytes without one explicit retention policy; and
- no common path interface shared with the observer.

### 5.6 ActiveGraph store

ActiveGraph 1.10.0's installed SQLite adapter uses:

- `events`, ordered by an autoincrement `seq`;
- `runs`, including lineage and fork metadata;
- `meta`, including `schema_version`;
- `events_archive`; and
- `snapshots`.

One `SQLiteEventStore` instance is scoped to one run, while multiple run instances share
the same SQLite file.

The adapter enables WAL and `synchronous=NORMAL` on open. A schema mismatch fails closed.
Compaction can move events to the archive tier, and a fork cannot start below the
compaction horizon without restoring archived history.

These semantics must remain ActiveGraph-owned. ROI-H needs store lifecycle management
around them, not a second event-store abstraction that leaks or changes their meaning.

### 5.7 Observer

`src/roi_h/observer/projection.py` currently:

- discovers `<project>/<env>/rpa.sqlite` directly;
- opens SQLite in read-only/query-only mode;
- queries the `runs` and `events` tables directly;
- rebuilds an object projection from `object.created` and `patch.applied`; and
- discovers artifacts from the current physical layout.

Read-only behavior is correct. Hardcoded layout and schema knowledge should move behind
one adapter so layout and ActiveGraph changes remain local.

### 5.8 Secrets

`src/roi_h/harness/secrets.py` currently stores plaintext values in project
`secrets.json`. It resolves `{{secret.NAME}}`, attempts to redact values before
persistence, and injects only declared or referenced values into worker environments.

The declaration and injection behavior should remain. Plaintext-at-rest storage and the
shared dev/prod namespace should not.

### 5.9 Diagnostics

ROI-H has no unified diagnostic module. ActiveGraph already provides durable execution
observability, so a broad project logging subsystem is unnecessary.

The missing cases are failures around ActiveGraph itself, startup, migration, update,
and bounded subprocess diagnostics.

### 5.10 Export and import

`roi-h rpa ship` creates an immutable automation package. It does not export a project.
There is no whole-project export/import CLI, portable path contract, consistent live
database snapshot, import collision policy, or project archive manifest.

## 6. Ubiquitous Language

These terms are canonical. New code, CLI help, tests, and documentation must use them
consistently.

### Data home

The user-owned root selected by explicit `--home`, then `ROI_H_HOME`, then the platform
default. On current Unix-like installations the default is `~/.roi-h`.

The data home survives application update and uninstall unless the operator explicitly
requests data deletion.

### Project

A durable namespace containing one automation domain's references, editable skills,
immutable packages, configuration, secret declarations, and environment execution state.

A project is not a Git checkout and must not depend on the ROI-H source repository.

### Project definition

Portable project material that describes what the project can execute:

- project manifest;
- reference assets;
- editable project skills;
- immutable automation packages; and
- environment channel references and policy.

### Environment

An isolated execution domain such as `dev` or `prod`. It owns a separate ActiveGraph
store, run directories, runtime state, secret namespace, retention configuration, and
maintenance lock.

An environment is not an automation-package copy.

### Run

One durable ActiveGraph event stream with a stable `run_id`, plus its run-scoped
filesystem material.

### Event

An immutable ActiveGraph fact. Events are ordered by store sequence, not wall-clock
timestamp.

### Projection

A rebuildable current view derived from events. A projection is never an independent
authority.

### Run workspace

Mutable, run-scoped files used while executing a run. It is safe to delete only according
to retention rules after the run is terminal.

### Reference asset

A stable, project-owned input such as a template, schema, fixture, mapping, or operator
provided document intended for reuse across runs.

### Artifact

A durable, immutable output attached to a run and represented in ActiveGraph by identity,
digest, size, media type, logical location, and provenance.

A file merely present in a workspace is not yet an artifact.

### Phase handoff

An immutable, digest-verifiable package representing the durable output contract of a
terminal phase.

### Runtime state

Sensitive or process-specific state required to continue an execution, such as browser
session data, process metadata, and locks. Runtime state is not an artifact and is
excluded from project export by default.

### Automation package

An immutable, digest-verified snapshot containing a recipe and every required non-core
skill. Production execution uses this package rather than ambient editable skills.

### Channel reference

A small immutable-package selection record for an environment, such as the production
selection of `daily-report@1.4.0`.

### Secret declaration

Portable metadata identifying a secret name, purpose, required environments, and
provider. It never contains the secret value.

### Secret value

Sensitive material stored through a `SecretStore` adapter and addressed by project,
environment, and secret name.

### Diagnostic record

A bounded operational observation used to understand why ROI-H itself could not perform
or record an operation. It is not domain history.

### Store backup

A consistent snapshot produced through the database implementation's supported backup
mechanism. A raw copy of an active SQLite main file is not a store backup.

### Project archive

A manifest-driven `.roih` file created by `project export` and consumed by
`project import`.

### Retention plan

A persisted, reviewable description of files and events that a later apply operation may
archive or delete.

### Module, interface, seam, and adapter

A **module** hides substantial behavior behind a small **interface**. A **seam** is the
location where behavior can vary without editing the caller. An **adapter** satisfies an
interface at a seam.

This refactor creates seams only where behavior already varies or is known to vary:

- SQLite versus future ActiveGraph store implementations;
- macOS Keychain versus environment/external secret providers;
- console versus JSONL diagnostic output; and
- filesystem project archives versus a possible future remote transport.

## 7. Authority and Ownership Matrix

| Information | Authority | Physical representation | Rebuildable | Default export |
|---|---|---|---:|---:|
| Run lifecycle | ActiveGraph | Environment store | No | Full archive |
| Phase lifecycle | ActiveGraph | Environment store | No | Full archive |
| Invocation lifecycle | ActiveGraph | Environment store | No | Full archive |
| Approvals and authority | ActiveGraph | Environment store | No | Full archive |
| Run feedback | ActiveGraph | Environment store | No | Full archive |
| Current run status | Projection | Memory/read model | Yes | No |
| Artifact identity and provenance | ActiveGraph | Environment store | No | Full archive |
| Artifact bytes | Run artifact directory | Files | No | Full archive |
| Phase handoff bytes | Run handoff directory | Files | No | Full archive |
| Mutable run work | Run workspace | Files | No | No |
| Browser/process state | Environment/run runtime | Files | No | No |
| Project reference assets | Project definition | Files | No | Yes |
| Editable project skills | Project definition | Files | No | Yes |
| Automation packages | Package store | Files | No | Yes |
| Environment channel references | Project definition | JSON | No | Yes |
| Secret declarations | Project definition | JSON | No | Yes |
| Secret values | SecretStore | Keychain/provider | No | Never |
| General diagnostics | DiagnosticSink | Rotated JSONL/stderr | No | No |
| Failure stderr | DiagnosticSink | Bounded run file | No | No |
| Observer view | Projection | Memory | Yes | No |
| Cache | Cache implementation | Files | Yes | No |

There must be only one authority for each fact. In particular:

- do not duplicate every ActiveGraph event into `run.jsonl`;
- do not treat phase manifests as a replacement for phase events;
- do not treat an artifact's physical path as its identity;
- do not derive production package selection from directory modification time; and
- do not infer a secret value from project files.

## 8. Target Data-Home Layout

```text
~/.roi-h/
├── config.json
├── diagnostics/
│   ├── roi-h.jsonl
│   └── update.jsonl
├── cache/
├── skills/
└── projects/
    └── <project-slug>/
        ├── project.json
        ├── reference/
        ├── skills/
        ├── packages/
        │   └── automations/
        │       └── <automation-name>/
        │           └── <version>/
        │               ├── manifest.json
        │               ├── recipe.json
        │               └── skills/
        ├── channels/
        │   ├── dev/
        │   │   └── <automation-name>.json
        │   └── prod/
        │       └── <automation-name>.json
        ├── secrets.meta.json
        └── environments/
            ├── dev/
            │   ├── environment.json
            │   ├── store/
            │   │   └── activegraph.sqlite
            │   ├── runs/
            │   │   └── <run-id>/
            │   │       ├── workspace/
            │   │       │   ├── input/
            │   │       │   ├── work/
            │   │       │   ├── output/
            │   │       │   └── tmp/
            │   │       ├── artifacts/
            │   │       ├── phases/
            │   │       ├── runtime/
            │   │       └── diagnostics/
            │   └── runtime/
            │       ├── locks/
            │       └── browser-profiles/
            └── prod/
                └── ...
```

### 8.1 Folders deliberately omitted

The target layout has no:

- project-wide `input/` or `output/`;
- generic `data/`, `files/`, or `misc/`;
- permanent project-wide `logs/`;
- plaintext `secrets.json`;
- environment-specific editable `skills/`;
- duplicated environment-specific automation package trees;
- project `exports/` folder;
- project-local package cache; or
- second projection database.

Exports are written to an operator-selected destination. Backups should normally be
written outside the data home so a single disk failure does not destroy both live state
and backups.

### 8.2 Permissions

On Unix-like systems:

- the data home and projects are created with user-only directory permissions;
- secret metadata may be readable only by the user;
- database, runtime, diagnostic, and browser state files are user-only;
- exports are user-only by default;
- imported files never preserve setuid, setgid, or executable bits except validated
  skill entrypoints that explicitly require execution; and
- symlinks are not followed during export or import.

Windows uses the current user's access-control mechanisms rather than pretending POSIX
modes provide protection.

## 9. Configuration Schemas

All configuration uses strict, versioned models. Unknown fields fail closed unless a
specific forward-compatible extension map is defined.

### 9.1 Home configuration

`<home>/config.json` contains user-local preferences:

```json
{
  "schema_version": 4,
  "active_project": "acme",
  "active_environments": {
    "acme": "dev"
  },
  "release_channel": "stable",
  "diagnostics": {
    "level": "warning",
    "max_bytes": 52428800,
    "max_files": 5
  }
}
```

Active project and active environment are local preferences. They are not exported with
a project.

### 9.2 Project manifest

`project.json` contains portable project identity and policy:

```json
{
  "schema_version": 1,
  "project_id": "prj_01J...",
  "slug": "acme",
  "display_name": "Acme",
  "created_at": "2026-07-29T00:00:00Z",
  "required_secrets": [
    {
      "name": "PORTAL_PASSWORD",
      "environments": ["dev", "prod"],
      "description": "Portal login password"
    }
  ],
  "retention": {
    "workspace_days_after_success": 7,
    "workspace_days_after_failure": 30,
    "runtime_days": 7,
    "diagnostic_days": 14,
    "artifact_policy": "keep",
    "event_policy": "keep"
  }
}
```

`project_id` is stable across rename and export/import. The directory slug may change.

### 9.3 Environment manifest

`environment.json` contains execution policy:

```json
{
  "schema_version": 1,
  "name": "prod",
  "store": {
    "adapter": "activegraph-sqlite",
    "durability": "full",
    "busy_timeout_ms": 10000
  },
  "execution": {
    "allow_adaptive": false,
    "allow_ambient_project_skills": false
  }
}
```

Development may use a performance-oriented durability profile. Production should use the
strongest SQLite durability profile supported by ActiveGraph. The current ActiveGraph
adapter forces `synchronous=NORMAL`; changing this requires a supported ActiveGraph
configuration seam or an upstream change. ROI-H must not monkeypatch an installed
dependency.

### 9.4 Secret metadata

`secrets.meta.json` contains references, never values:

```json
{
  "schema_version": 1,
  "provider": "macos-keychain",
  "entries": [
    {
      "name": "PORTAL_PASSWORD",
      "environments": ["dev", "prod"]
    }
  ]
}
```

## 10. Project and Environment Selection

Selection precedence remains deterministic.

### Home

1. explicit `--home`;
2. `ROI_H_HOME`; and
3. platform default.

### Project

1. explicit `--project`;
2. `ROI_H_PROJECT`;
3. active project in home configuration; and
4. the sole project when exactly one project exists.

No implicit project is selected when multiple projects exist and none is active.

### Environment

1. explicit `--env`;
2. `ROI_H_ENV`;
3. active environment preference for the selected project; and
4. `dev`.

Every JSON response includes the resolved home, project, environment, and store identity
so automation never has to guess what was selected.

## 11. Logical Path Contract

### 11.1 Schemes

New durable records and recipes use these logical path forms:

```text
project://reference/<relative-path>
run://input/<relative-path>
run://work/<relative-path>
run://output/<relative-path>
run://tmp/<relative-path>
artifact://<artifact-id>
automation://<name>/<version>/<relative-path>
```

Meaning:

- `project://reference/...` is read-only during normal execution;
- `run://input/...` is materialized input owned by the run;
- `run://work/...` is mutable intermediate work;
- `run://output/...` is a candidate output, not yet a durable artifact;
- `run://tmp/...` may be removed during or immediately after execution;
- `artifact://...` addresses a durable artifact by identity; and
- `automation://...` reads immutable package content.

### 11.2 LogicalPath model

Introduce a strict `LogicalPath` value model:

```python
class LogicalPath:
    scheme: Literal["project", "run", "artifact", "automation"]
    root: str
    segments: tuple[str, ...]
```

Validation rejects:

- empty path segments;
- `.` and `..`;
- NUL bytes;
- absolute-path syntax inside segments;
- backslash ambiguity;
- Windows drive prefixes;
- reserved device names where applicable;
- path components that normalize to a different Unicode identity;
- paths longer than supported platform limits;
- case-insensitive collisions during import; and
- symlinks that escape the resolved root.

### 11.3 PathResolver module

Create one deep `PathResolver` module. Its external interface should remain small:

```python
resolve(logical_path, scope, intent) -> ResolvedPath
normalize(physical_path, scope) -> LogicalPath
```

`scope` contains the already-resolved project, environment, run, and optional automation
package. `intent` is `read`, `create`, `replace`, or `delete`.

The implementation owns:

- layout version;
- containment checks;
- directory creation;
- symlink policy;
- cross-platform normalization;
- read-only roots;
- artifact identity lookup;
- package immutability;
- path length checks; and
- conversion of legacy paths during migration.

Callers must not join `workspace.artifacts / run_id` themselves after this module exists.

### 11.4 Tool interface

Tool schemas may continue exposing a string for compatibility, but the string represents
a logical path.

Immediately before subprocess execution:

1. validate the logical path;
2. validate it against the tool's declared filesystem roots;
3. resolve it inside the selected run scope;
4. pass the physical path to the subprocess; and
5. normalize path-bearing output fields back to logical paths before persistence.

The worker may receive physical paths because it must interact with the OS. ActiveGraph,
recipes, manifests, CLI JSON, and automation packages receive logical paths.

### 11.5 Filesystem-root enforcement

Replace free-form root descriptions with canonical capabilities:

```text
project:reference:read
run:input:read
run:work:read-write
run:output:read-write
run:tmp:read-write
artifact:read
automation:read
```

Every built-in and custom tool declares the smallest required set.

The runtime rejects a request before approval or execution when its logical paths exceed
the declared capabilities. Tool effect (`read`, `write`, `destructive`) and filesystem
capability are separate checks.

### 11.6 External paths

Arbitrary external physical paths are supported only at explicit operator ingress and
egress commands:

```shell
roi-h rpa run input add ./customers.xlsx --as customers.xlsx
roi-h rpa artifact export ARTIFACT_ID --output ./report.xlsx
roi-h rpa project import ./acme.roih
roi-h rpa project export acme --output ./acme.roih
```

These commands copy data across the seam. Recipes and skills do not receive ambient
filesystem authority.

Development-only escape hatches, if retained, must require an explicit flag, produce an
ActiveGraph policy event, and make the resulting run ineligible for production shipping
until all external paths are replaced.

## 12. Run Storage Lifecycle

### 12.1 Run creation

Creating a run:

1. validates `run_id`;
2. acquires a short environment maintenance/read guard;
3. creates the run directory in staging;
4. creates required workspace roots and runtime directories;
5. writes a run filesystem manifest;
6. atomically renames staging into place;
7. creates the ActiveGraph run; and
8. emits the typed `rpa.run` object.

If filesystem creation succeeds but ActiveGraph creation fails, reconciliation classifies
the directory as an unregistered run workspace. It may be safely removed only after
confirming it contains no registered artifacts.

### 12.2 Workspace semantics

- `input/` is immutable after execution begins unless an explicit input-replacement event
  is recorded before the first consuming invocation.
- `work/` is mutable and may contain intermediate state.
- `output/` is mutable until a file is attached as an artifact.
- `tmp/` is disposable and may be cleaned at process exit.
- a successful run does not automatically make every file in `output/` an artifact;
  attachment remains explicit or schema-driven.

### 12.3 Runtime semantics

`runtime/` contains:

- browser session pointers;
- process metadata;
- resumability state not already represented in ActiveGraph;
- bounded failed-tool stdout/stderr; and
- crash markers.

Sensitive runtime files are never served by the observer as artifacts.

### 12.4 Terminal run

At terminal success or failure:

1. complete pending ActiveGraph lifecycle records;
2. flush tool processes;
3. reconcile artifact and phase files;
4. close runtime resources;
5. remove `tmp/`;
6. mark runtime state retention eligibility; and
7. release the run lease.

Workspace retention occurs later through a retention plan. A terminal transition does not
perform broad recursive deletion.

## 13. Artifact Protocol

### 13.1 Identity

Use a stable `artifact_id`, distinct from filename and physical path:

```text
art_01J...
```

The ActiveGraph artifact record should contain:

```json
{
  "artifact_id": "art_01J...",
  "run_id": "run_01J...",
  "name": "summary.xlsx",
  "uri": "artifact://art_01J...",
  "sha256": "sha256:...",
  "bytes": 18214,
  "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "phase_id": "phase_...",
  "invocation_id": "inv_...",
  "source": "run://output/summary.xlsx",
  "created_at": "..."
}
```

Do not persist a machine-specific `path`.

### 13.2 Attach protocol

Attaching an artifact:

1. resolve and validate the source logical path;
2. reject directories, devices, sockets, and escaping symlinks;
3. copy to a staging file in the target artifact directory;
4. stream SHA-256 and byte count while copying;
5. flush and `fsync` the staging file;
6. apply safe permissions;
7. atomically rename staging to the final artifact filename;
8. sync the containing directory where supported;
9. append the ActiveGraph artifact record; and
10. return the artifact identity and logical path.

The artifact filename is unique within a run. Reattaching an identical digest under the
same name is idempotent. A different digest under the same name fails unless an explicit
new artifact version/name is requested.

### 13.3 Crash windows

| Crash point | Result | Recovery |
|---|---|---|
| Before staging | No change | Retry |
| During staging | Hidden partial file | Reconcile removes stale staging |
| After final rename, before event | Orphan artifact file | Reconcile offers record repair |
| After event commit | Consistent artifact | No action |
| Event exists, file later missing | Missing artifact | Error; restore from backup |

ROI-H should prefer file-before-event because an orphan file can be repaired
unambiguously from its digest. An event claiming nonexistent durable bytes cannot be
repaired without another source.

### 13.4 Handoffs

Phase handoff manifests contain:

- schema version;
- project ID;
- environment;
- run ID;
- phase ID and index;
- terminal phase status;
- artifact IDs, names, sizes, and digests;
- summary;
- required artifacts;
- source lineage; and
- package digest.

Manifest paths are relative or logical. A handoff is written through staging and atomic
rename. Once referenced by a terminal phase event it is immutable.

### 13.5 Future object storage

Artifact layout remains behind `RunStorage`. If measurements later justify
content-addressed deduplication or remote object storage, an adapter can implement the
same attach/open/export behavior.

Do not create an `objects/` content store during this refactor without a second real
adapter or a demonstrated storage requirement.

## 14. ActiveGraph Store Architecture

### 14.1 Source of truth

The environment ActiveGraph store owns:

- run registry and lineage;
- canonical event order;
- object creation and patches;
- phases;
- steps;
- invocations and attempts;
- approvals;
- artifacts and handoffs;
- run feedback;
- policy decisions;
- reconciliation reports;
- retention decisions; and
- terminal outcomes.

Current state is always replayable from the live event tier plus required snapshots and
archived history.

### 14.2 Store placement

Target:

```text
projects/<project>/environments/<env>/store/activegraph.sqlite
```

The filename describes the implementation but callers receive a store identity from the
workspace module. Tests may override the adapter through the supported seam.

### 14.3 StoreLifecycle module

Create a `StoreLifecycle` module around operational store concerns. It must not wrap
ordinary ActiveGraph graph/event calls.

External interface:

```python
inspect(workspace) -> StoreStatus
check(workspace, level) -> StoreCheck
backup(workspace, destination) -> StoreBackup
restore(workspace, backup, mode) -> RestoreResult
```

The implementation hides:

- SQLite connection details;
- WAL checkpointing;
- online backup;
- schema inspection;
- disk-space preflight;
- maintenance locking;
- temporary-file placement;
- backup manifests and hashes;
- restore staging;
- post-restore replay verification.

### 14.4 SQLite durability

Required behavior:

- WAL is supported and expected;
- every connection has a defined busy timeout;
- maintenance reports journal mode and synchronous mode;
- production's requested durability is verified rather than assumed;
- disk-full and read-only failures are typed;
- a store is never considered healthy only because the file exists; and
- `-wal` and `-shm` files are treated as live database state, not diagnostic logs.

ActiveGraph currently selects WAL and `synchronous=NORMAL`. Before claiming the
production `full` durability profile, ROI-H must obtain a supported ActiveGraph
configuration for `synchronous=FULL` and busy timeout, or document and accept the
weaker power-loss window. Tests must assert the effective PRAGMA values.

### 14.5 Concurrency

Current per-run leases prevent two runners from mutating the same run. Preserve them.

Add an environment maintenance lock for:

- restore;
- schema/layout migration;
- database replacement;
- compaction that changes the live event tier;
- project rename/delete;
- full export when the chosen backup method requires quiescence; and
- garbage collection that touches shared environment state.

Normal runs in different `run_id` values may execute concurrently only after a stress
test proves the configured SQLite busy timeout and retry behavior. A SQLite database has
one writer at a time even when WAL allows concurrent readers.

If sustained concurrent write demand exceeds the SQLite envelope, use a supported
ActiveGraph Postgres adapter behind the existing store seam. Do not add application-level
event queues that create a second authority.

### 14.6 Health checks

`store check` has levels:

**Quick**

- file exists and is readable;
- expected ActiveGraph schema version;
- expected ROI-H layout version;
- `PRAGMA quick_check`;
- effective journal, synchronous, and busy timeout configuration;
- no stale migration marker; and
- available disk-space warning.

**Full**

- everything in quick;
- replay every selected run;
- verify snapshot references;
- inspect archive/live event consistency;
- verify run lineage;
- reconcile all registered artifacts and handoffs;
- verify immutable package digests; and
- report orphan runtime/workspace directories.

Checks are read-only unless `--repair` is explicit.

### 14.7 Backup

SQLite backup uses the SQLite online backup interface or another ActiveGraph-supported
consistent snapshot mechanism.

Protocol:

1. resolve a destination outside the live store;
2. ensure sufficient free space;
3. acquire required maintenance coordination;
4. create a temporary backup database;
5. use online backup;
6. run integrity checks on the backup;
7. record ActiveGraph and ROI-H schema versions;
8. calculate size and SHA-256;
9. write a backup manifest;
10. atomically publish the backup; and
11. release coordination.

Never copy only `activegraph.sqlite` while a writer may have uncheckpointed WAL changes.

### 14.8 Restore

Restore:

1. validates manifest and digest;
2. validates compatible schema or plans a migration;
3. acquires the environment maintenance lock;
4. confirms no active run processes;
5. backs up the current store;
6. restores into staging;
7. runs full validation;
8. atomically swaps the store;
9. reopens and replays representative runs;
10. records a restore diagnostic and, after open, a domain event; and
11. retains the previous store until explicit cleanup.

An interrupted restore must leave either the old validated store or a complete new
validated store selected, never a partially copied active path.

### 14.9 Migration

ActiveGraph schema migration and ROI-H layout migration are separate concepts:

- ActiveGraph migration changes the event-store format;
- ROI-H layout migration changes project files and paths; and
- ROI-H domain migration changes typed event/object payload interpretation.

The migration planner reports all three independently.

### 14.10 Compaction

Compaction is initially disabled by default.

Before enabling it:

- prove a snapshot can rebuild every required projection;
- verify the observer can read compacted runs;
- define the fork horizon shown to operators;
- export or back up archive-tier history;
- make the dry-run show affected runs, events, bytes, snapshots, and lost fork points;
- ensure interruption is recoverable; and
- test ActiveGraph version compatibility.

Compaction must not be presented as ordinary log rotation. It changes available live
history and fork behavior.

## 15. ActiveGraph Events Versus Diagnostics

### 15.1 Governing rule

> If information changes the meaning or state of a run, it belongs in ActiveGraph. If it
> only explains why ROI-H itself could not operate or persist that state, it is a
> diagnostic record.

### 15.2 ActiveGraph examples

Record in ActiveGraph:

- run started, paused, resumed, completed, failed, or cancelled;
- phase opened or ended;
- invocation scheduled, running, succeeded, failed, or outcome unknown;
- approval requested, granted, or denied;
- retry and reconciliation decisions;
- artifact attached;
- handoff created;
- secret name requested, without its value;
- production policy allowed or denied an operation;
- operator feedback associated with a run;
- retention plan approved and applied; and
- project import origin recorded after the target store is open.

### 15.3 Diagnostic examples

Record diagnostically:

- CLI failed before home/project resolution;
- home configuration could not be parsed;
- database could not open, was locked, or failed integrity checks;
- migration crashed before ActiveGraph became writable;
- application update failed;
- subprocess failed before a canonical invocation outcome could be persisted;
- bounded stack trace for an internal defect;
- observer HTTP server failed to bind; and
- backup destination became unavailable.

### 15.4 DiagnosticSink module

External interface:

```python
emit(record) -> None
capture_exception(context, exception) -> DiagnosticId
```

Adapters:

- stderr/console;
- rotated home JSONL; and
- bounded run diagnostic files for subprocess stdout/stderr.

OpenTelemetry is a future adapter only when deployment requirements justify it.

### 15.5 Diagnostic schema

```json
{
  "schema_version": 1,
  "diagnostic_id": "diag_01J...",
  "timestamp": "2026-07-29T00:00:00Z",
  "level": "error",
  "code": "store.open_failed",
  "message": "Could not open the selected ActiveGraph store.",
  "component": "store-lifecycle",
  "project_id": "prj_...",
  "project": "acme",
  "environment": "prod",
  "run_id": "run_...",
  "invocation_id": null,
  "exception_type": "OperationalError",
  "details": {
    "sqlite_code": 5
  }
}
```

Physical paths are omitted or home-relative unless the operator selects verbose local
diagnostics.

### 15.6 Redaction

Redaction occurs before serialization and before sending to an adapter.

At minimum:

- replace known secret values with `{{secret.NAME}}`;
- omit full environment maps;
- omit browser cookies, storage state, and authorization headers;
- cap arbitrary strings and collections;
- omit tool input/output by default;
- normalize home paths to `<ROI_H_HOME>`;
- strip query parameters from sensitive URLs where configured; and
- prevent diagnostic serialization failures from exposing the unredacted object in a
  fallback message.

### 15.7 Retention

Default diagnostic behavior:

- warning and error level persisted;
- info level console-only unless enabled;
- debug level opt-in and time-limited;
- home JSONL rotated by size with a small fixed file count;
- successful tool stdout/stderr discarded;
- failed tool stdout/stderr size-capped and retained for the configured diagnostic
  period; and
- diagnostics excluded from project export.

There is no permanent project-wide log history.

### 15.8 CLI naming

Use `diagnostics`, not `logs`, for the fallback records:

```shell
roi-h diagnostics show
roi-h diagnostics tail
roi-h diagnostics show --run RUN_ID
roi-h diagnostics clear --before 2026-07-01 --dry-run
roi-h support-bundle --run RUN_ID --redacted
```

Human-readable run history remains:

```shell
roi-h rpa status --run-id RUN_ID
roi-h rpa events --run-id RUN_ID
roi-h rpa trace --run-id RUN_ID
```

Those commands are views over ActiveGraph.

## 16. Observer Refactor

### 16.1 Required design

The observer remains read-only. It receives:

- a project catalog interface;
- a read-only ActiveGraph projection adapter; and
- a run-storage interface for safe artifact access.

It must not:

- create missing directories;
- call schema creation through a nominally read-only path;
- infer layout independently;
- query arbitrary operator-supplied database files by default;
- mutate ActiveGraph;
- repair reconciliation issues;
- expose workspace, runtime, secret, or diagnostic files as artifacts; or
- trust an artifact path stored in an event without containment validation.

### 16.2 Projection adapter

Preferred order:

1. use a supported ActiveGraph read/replay interface;
2. if ActiveGraph lacks the required read-only query, isolate SQLite queries inside one
   `ActiveGraphProjectionAdapter`; and
3. protect that adapter with installed-version schema contract tests.

No observer route or UI function should contain SQL.

### 16.3 Artifact access

Artifact preview resolves `artifact_id` through the projection and `RunStorage`.
The physical file must be contained inside the selected run's artifact root and its
digest may be verified before download or preview.

Preview limits remain explicit for text, CSV, spreadsheets, images, and PDFs.

## 17. Skills and Automation Packages

### 17.1 Skill scopes

Canonical resolution order in development:

1. packaged core skill;
2. home-shared skill; and
3. project skill.

Name collisions are visible in `tools list` output with scope and source digest.
Overrides are never silent in production.

### 17.2 Editable project skills

Target:

```text
projects/<project>/skills/
```

Editable skill source is project definition, not environment execution state.

Development may load it. Production may not load ambient project or shared skills.

### 17.3 Immutable automation packages

Target:

```text
projects/<project>/packages/automations/<name>/<version>/
```

A package contains:

- package manifest;
- recipe;
- referenced project and shared skill snapshots;
- tool schemas and policy metadata;
- source run identity;
- compatibility requirements;
- file manifest;
- package digest; and
- optional signature metadata.

Package content is written in staging, verified, then atomically installed. An existing
`name/version` with the same digest is idempotent. A different digest under the same
identity fails.

### 17.4 Channels and promotion

Environment selection lives in channel references:

```text
channels/dev/daily-report.json
channels/prod/daily-report.json
```

Example:

```json
{
  "schema_version": 1,
  "name": "daily-report",
  "version": "1.4.0",
  "package_digest": "sha256:...",
  "promoted_at": "...",
  "source_run_id": "run_..."
}
```

Promotion:

1. verifies package digest;
2. validates environment policy;
3. validates required secret declarations;
4. validates logical-path portability;
5. records approval/authority through ActiveGraph where applicable;
6. atomically changes the channel reference; and
7. leaves the package bytes unchanged.

### 17.5 Shipping gate

`ship` rejects:

- physical absolute paths;
- development escape-hatch path usage;
- missing shared/project skill snapshots;
- undeclared secrets;
- tools disallowed in production;
- unbounded filesystem capabilities;
- mutable package files;
- unresolved outcome-unknown invocations;
- failed artifact reconciliation;
- missing required phase artifacts; and
- incompatible ROI-H or ActiveGraph requirements.

## 18. Secrets Refactor

### 18.1 SecretStore seam

Create a `SecretStore` interface:

```python
list_names(project_id, environment) -> list[SecretMetadata]
get(project_id, environment, name) -> SecretValue | None
set(project_id, environment, name, value) -> SecretMetadata
delete(project_id, environment, name) -> DeleteResult
```

Adapters:

- macOS Keychain;
- environment variables for ephemeral/headless execution; and
- a future external provider when actually required.

The interface never returns all values in bulk.

### 18.2 Namespacing

Secret identity is:

```text
<project-id>/<environment>/<secret-name>
```

Development and production never share a value implicitly. A CLI may intentionally copy
or set the same value in both, but the operation is explicit and produces names-only
output.

### 18.3 Injection

Only secrets declared by the selected tool or referenced by a validated template are
loaded. They are injected into the child process environment immediately before
execution.

The parent process should avoid permanently mutating its own environment with secret
values.

### 18.4 Migration from `secrets.json`

Migration:

1. verifies file ownership and restrictive permissions;
2. parses names and values without printing them;
3. asks for or applies an explicit environment mapping;
4. writes each value to the selected provider;
5. reads each value back through the provider for equality verification;
6. writes `secrets.meta.json`;
7. scans recipes, packages, and event stores for known-value exposure without printing
   matches;
8. creates new corrected package versions when a frozen package contains a value;
9. blocks full export when immutable history contains an unresolved secret exposure;
10. marks the plaintext file migrated; and
11. moves the plaintext file to a recoverable, access-restricted migration backup.

Historical ActiveGraph events are not silently rewritten. If an immutable event contains
a secret value, remediation requires a separately designed, audited store-redaction
migration or retirement of that store after preserving whatever compliant audit evidence
is required. This exceptional security operation is outside ordinary layout migration.

Secure deletion cannot be promised on modern copy-on-write or SSD filesystems. The CLI
must state that limitation. The backup is removed only by a separate explicit operator
action.

### 18.5 Exports

Project archives include:

- secret names;
- descriptions;
- environment requirements; and
- provider type hints.

They never include:

- secret values;
- Keychain exports;
- `.env` files;
- browser cookies; or
- authorization headers.

Import ends with a missing-secret report and exact `secret set` commands containing
names, never placeholder values that might be mistaken for real credentials.

## 19. Project Export

### 19.1 CLI

```shell
roi-h rpa project export NAME --output NAME.roih
roi-h rpa project export NAME --mode definition --output NAME.roih
roi-h rpa project export NAME --mode full --output NAME.roih
roi-h rpa project export NAME --verify
```

Modes:

**definition**

- project manifest;
- references;
- project skills;
- automation packages;
- channel references;
- environment configuration;
- secret declarations; and
- required vendored shared-skill sources not already frozen into packages.

No run stores, artifacts, handoffs, workspaces, runtime state, or diagnostics.

**full** — default for “export this project”

- everything in definition;
- consistent ActiveGraph backups for selected environments;
- durable registered artifacts;
- phase handoffs; and
- run filesystem manifests.

No mutable workspaces, runtime state, secret values, browser profiles, locks, caches, or
diagnostics.

### 19.2 Archive format

Use a versioned ZIP-based `.roih` container initially because Python can read and write
it without a new runtime dependency.

Archive root:

```text
manifest.json
project/
stores/
files/
dependencies/
```

The archive manifest contains:

```json
{
  "format": "roi-h-project",
  "format_version": 1,
  "created_at": "...",
  "created_by": {
    "roi_h": "0.2.0",
    "activegraph": "1.10.0",
    "platform": "macos-arm64"
  },
  "project": {
    "project_id": "prj_...",
    "slug": "acme",
    "display_name": "Acme"
  },
  "mode": "full",
  "environments": ["dev", "prod"],
  "files": [
    {
      "path": "project/project.json",
      "bytes": 1234,
      "sha256": "sha256:...",
      "kind": "project-manifest"
    }
  ],
  "excluded": [
    "secret-values",
    "workspace",
    "runtime",
    "diagnostics",
    "cache",
    "locks"
  ],
  "required_secrets": [],
  "compatibility": {
    "roi_h": ">=0.2,<0.3",
    "activegraph_schema": "1",
    "layout_schema": 4
  }
}
```

### 19.3 Export protocol

1. resolve project by explicit identity;
2. acquire a project export guard;
3. inspect layout and store health;
4. reject or report active ambiguous migrations;
5. create consistent store backups into a private staging directory;
6. enumerate files from allowlisted roots;
7. reject symlinks, devices, sockets, path escapes, and case collisions;
8. include only registered durable run files in full mode;
9. hash files while streaming them into the archive;
10. write the manifest last;
11. close the archive;
12. reopen and verify every entry and digest;
13. atomically rename to the requested destination; and
14. print included/excluded counts, bytes, and missing secret names.

An export never reads files by following arbitrary paths from an event. It resolves
logical artifact identities through `RunStorage`.

### 19.4 Consistency

The full archive represents a point-in-time snapshot:

- each environment store backup is consistent;
- included artifacts correspond to the backup's registered artifacts;
- a run completing during export is either wholly outside the snapshot or captured by a
  defined second reconciliation pass;
- export records its event-sequence horizon per environment; and
- artifacts added after that horizon are not silently included.

The simplest first implementation acquires a short artifact-attachment barrier around
store snapshot plus artifact enumeration. Long-running tool execution may continue in
its workspace.

Full export may preserve historical ActiveGraph payloads created by older ROI-H versions.
Layout migration never rewrites immutable historical events merely to replace a physical
path. The archive manifest reports whether legacy physical paths remain in history. Such
paths must be semantically inactive after migration, but they may still disclose a former
local directory name. Export requires explicit acknowledgement when the portability scan
finds them.

### 19.5 Large projects

Export streams data and does not load files or the entire archive into memory.

Preflight reports:

- estimated bytes;
- available destination space;
- largest artifacts;
- number of runs; and
- excluded disposable bytes.

Cancellation removes staging and leaves any existing destination unchanged.

## 20. Project Import

### 20.1 CLI

```shell
roi-h rpa project import ./acme.roih
roi-h rpa project import ./acme.roih --name acme-copy
roi-h rpa project import ./acme.roih --verify-only
roi-h rpa project import ./acme.roih --replace --plan PLAN_ID
```

### 20.2 Import protocol

1. open archive without extracting;
2. validate format and compatibility;
3. validate entry names, normalized identities, sizes, and compression ratios;
4. reject absolute paths, `..`, symlinks, devices, duplicates, and case collisions;
5. enforce total uncompressed size and entry-count limits;
6. verify manifest completeness and every digest;
7. choose a non-conflicting target slug;
8. stage under the target project's parent directory;
9. extract only manifest-listed entries;
10. migrate layout/store/domain schemas in staging when supported;
11. run full project doctor against staging;
12. verify packages, stores, artifacts, and handoffs;
13. generate a missing-secret report;
14. atomically rename staging to the final project path;
15. update home selection only when `--use` is explicit; and
16. retain a failed staging directory only when `--keep-failed-staging` is explicit.

### 20.3 Collision policy

Default behavior never merges into an existing project.

- same slug, different project ID: fail and suggest `--name`;
- same project ID at another slug: report possible duplicate and require a choice;
- same package identity and digest: idempotent;
- same package identity, different digest: fail;
- same artifact identity, different digest: fail; and
- `--replace` requires a generated plan, a backup, no active runs, and explicit apply.

### 20.4 Trust

Importing a project does not automatically authorize:

- custom skill execution;
- production promotion;
- secret access;
- network access;
- destructive tools; or
- adaptive execution.

Imported custom skills are marked unreviewed unless the archive carries a locally trusted
signature. Package digest proves integrity, not trust.

### 20.5 Post-import output

The command reports:

- project path and ID;
- imported environments, packages, runs, and artifacts;
- migrations performed;
- packages requiring trust review;
- missing secrets by environment;
- channel selections;
- doctor result; and
- exact next safe command.

## 21. Project Doctor

### 21.1 CLI

```shell
roi-h rpa project doctor
roi-h rpa project doctor --project acme
roi-h rpa project doctor --full
roi-h rpa project doctor --repair --plan PLAN_ID
roi-h rpa project paths
```

### 21.2 Checks

Project doctor validates:

- home resolution and permissions;
- home and project schema versions;
- stable project ID and valid slug;
- environment manifests;
- no path escape or forbidden symlink;
- store presence and health;
- effective SQLite settings;
- active/stale leases;
- package digest and channel-reference consistency;
- project and shared skill name collisions;
- production channels reference self-contained packages;
- required secret names are present in the selected provider;
- plaintext secret files are absent after migration;
- artifact and handoff reconciliation;
- workspace/runtime retention eligibility;
- observer can build a read-only projection;
- free disk space;
- export portability; and
- publication-boundary violations if run from a source checkout.

### 21.3 Repair rules

Automatic repair is limited to operations with one unambiguous correct result:

- create missing empty required directories;
- rewrite stale derived metadata from an authoritative source;
- attach an orphan artifact only when identity and provenance are uniquely recoverable;
- remove stale staging files after age and lease checks;
- repair a channel reference only when one exact digest match exists; and
- clear a stale lease only after verifying its process is absent.

Doctor does not:

- invent missing artifact bytes;
- select between conflicting skill/package versions;
- merge projects;
- recreate missing secret values;
- delete unregistered user files;
- truncate ActiveGraph history; or
- resolve an `outcome_unknown` external write without the tool's reconciliation
  procedure.

## 22. Retention and Garbage Collection

### 22.1 Classes

**Disposable**

- `tmp/`;
- stale staging;
- rebuildable caches;
- expired successful-tool diagnostics; and
- ended process metadata.

**Retain by default**

- run workspace input/work/output;
- failure diagnostics within configured period;
- runtime/browser state within configured period; and
- unregistered files pending reconciliation.

**Durable**

- ActiveGraph live and archive tiers;
- store backups;
- registered artifacts;
- phase handoffs;
- references;
- project skills;
- automation packages;
- channel references; and
- secret declarations.

Durable material is never deleted by default.

### 22.2 CLI

```shell
roi-h rpa gc plan --project acme
roi-h rpa gc show PLAN_ID
roi-h rpa gc apply PLAN_ID
roi-h rpa gc cancel PLAN_ID
```

The plan contains exact paths/identities, classifications, bytes, reasons, retention
rules, ActiveGraph horizons, and blockers.

### 22.3 Default policy

Conservative initial defaults:

- remove `tmp/` after terminal run cleanup;
- keep successful workspaces for 7 days;
- keep failed/outcome-unknown workspaces for 30 days;
- keep sensitive runtime state for at most 7 days unless a named reusable profile is
  explicitly configured;
- keep diagnostics for 14 days within size limits;
- keep artifacts indefinitely;
- keep ActiveGraph events indefinitely; and
- keep immutable packages while referenced by a channel or run.

An operator may configure stricter or longer policies.

### 22.4 Reachability

A package cannot be collected while referenced by:

- a channel;
- a run record;
- another package manifest; or
- a retained export/backup manifest known to the catalog, when such indexing is later
  implemented.

An artifact cannot be collected independently of its ActiveGraph record. Artifact
retention is a domain operation recorded before physical deletion.

### 22.5 Legal hold and pinned runs

Support a run-level `retention=pinned` event before any automatic durable cleanup is
introduced. Pinned runs and their artifacts, handoffs, relevant packages, and store
history are excluded from destructive plans.

## 23. Project Rename and Delete

### 23.1 Rename

Project identity is `project_id`; slug is a mutable local locator.

Rename:

1. validates target slug;
2. acquires project maintenance lock;
3. confirms no conflicting target;
4. updates staged project/home metadata;
5. updates secret-provider display labels without changing secret identity;
6. atomically renames the directory on the same filesystem;
7. verifies open and doctor; and
8. rolls back metadata on failure.

Events and packages use `project_id`, so history does not need rewriting.

### 23.2 Delete

Default `project delete` moves the exact project to a recoverable trash/quarantine area
after:

- resolving and printing project ID, slug, path, environments, runs, packages, and bytes;
- confirming no active runs;
- creating a deletion plan;
- optionally creating a full export;
- checking secret-provider entries; and
- requiring explicit confirmation/apply.

Secret deletion is a separate flag. Permanent purge is a separate command and reports
that recovery is unavailable.

Immediate `shutil.rmtree` is not the normal operator journey.

## 24. Feedback

Feedback tied to a run, invocation, tool, artifact, or package is durable domain history
and belongs in ActiveGraph as typed `rpa.feedback`.

The existing project-level `feedback.jsonl` should be migrated only when entries can be
mapped to a run or explicit project-level feedback record without inventing identity.
Unmappable legacy feedback remains in a restricted migration archive and is reported by
doctor.

Aggregated feedback views are projections. Generated recommendations or candidate skill
changes are artifacts until an operator promotes them into editable project skill source.

## 25. Publication and Repository Boundary

The generic core repository and Python distribution may contain:

- `src/roi_h`;
- packaged generic core skills;
- observer static assets;
- generic documentation;
- tests;
- packaging metadata;
- release and publication-boundary checks; and
- generic migration code.

They must never contain:

- an actual data home;
- project manifests or references;
- custom/shared user skills;
- automation packages;
- channel selections;
- run workspaces;
- artifacts or phase handoffs;
- ActiveGraph databases, WAL, or SHM files;
- browser profiles, cookies, or sessions;
- secret metadata or values;
- customer-specific fixtures;
- project exports;
- diagnostics or support bundles; or
- migration backups from a user project.

Required defenses:

1. Git ignore patterns for known local-state roots and extensions.
2. A tracked-file allowlist check in CI.
3. Wheel and source-distribution content tests.
4. Secret scanning.
5. Archive-content tests.
6. Documentation examples that use synthetic data only.
7. A release gate that fails when unexpected files appear.

`ROI_H_HOME` must never default inside the current repository. Tests always use an
explicit temporary home.

## 26. Deep Modules and Seams

The refactor should produce a small number of deep modules.

### 26.1 WorkspaceCatalog

Interface:

```python
resolve(selection) -> Workspace
list_projects() -> list[ProjectSummary]
create(spec) -> ProjectResult
rename(project, target, plan) -> ProjectResult
delete(project, plan) -> ProjectResult
```

Implementation hides layout, selection precedence, schema loading, identity, locks,
atomic creation, and local preference updates.

### 26.2 PathResolver

Interface:

```python
resolve(logical_path, scope, intent) -> ResolvedPath
normalize(physical_path, scope) -> LogicalPath
```

Implementation hides containment and portability complexity.

### 26.3 RunStorage

Interface:

```python
prepare(run_scope) -> RunPaths
attach(run_scope, source, metadata) -> ArtifactAttachment
open_artifact(run_scope, artifact_id) -> BinaryIO
finalize(run_scope) -> FinalizeResult
reconcile(run_scope, repair=False) -> ReconciliationReport
```

Implementation hides staging, hashing, atomic rename, handoffs, filesystem manifests,
crash cleanup, and physical layout.

### 26.4 StoreLifecycle

Interface defined in Section 14.3. It hides operational database concerns while normal
graph operations remain ActiveGraph-native.

### 26.5 ProjectArchive

Interface:

```python
inspect(source) -> ArchiveInspection
export(project, destination, options) -> ExportResult
import_archive(source, target, options) -> ImportResult
```

Implementation hides database snapshots, archive security, streaming, hashes, staging,
compatibility, and atomic activation.

### 26.6 SecretStore

Interface defined in Section 18.1. Multiple real adapters justify the seam.

### 26.7 DiagnosticSink

Interface defined in Section 15.4. Console and rotated-file adapters justify the seam.

### 26.8 RetentionPlanner

Interface:

```python
plan(scope, policy) -> RetentionPlan
inspect(plan_id) -> RetentionPlan
apply(plan_id) -> RetentionResult
```

The planner never accepts an unreviewed list of paths from a caller. It discovers and
classifies targets itself.

### 26.9 Interfaces deliberately not introduced

Do not add:

- an ROI-H event-store interface duplicating ActiveGraph append/replay;
- a generic repository interface for every JSON file;
- a filesystem adapter with only one implementation and no test leverage;
- a logger wrapper at every call site;
- an artifact adapter before a second storage implementation is real; or
- a universal manager module that combines project, store, run, archive, secret, and
  retention behavior.

## 27. Error Model

Every operator-facing failure has:

- stable error code;
- concise message;
- what failed;
- why ROI-H stopped;
- whether any state changed;
- exact recovery action;
- selected home/project/environment/run;
- diagnostic ID when available; and
- machine-readable context without secret values.

Core codes include:

```text
home.schema_unsupported
project.not_found
project.layout_migration_required
project.active_runs
path.invalid_logical_path
path.escape_denied
path.capability_denied
artifact.identity_conflict
artifact.file_missing
store.open_failed
store.locked
store.integrity_failed
store.schema_mismatch
store.backup_failed
store.restore_failed
store.migration_failed
archive.invalid
archive.digest_mismatch
archive.path_unsafe
archive.incompatible
secret.missing
secret.provider_failed
package.digest_mismatch
package.not_portable
retention.plan_stale
```

Commands must distinguish:

- no state changed;
- staging created and cleaned;
- staging retained;
- old state remains active;
- new state atomically activated; and
- repair or deletion applied.

## 28. CLI Target

This section defines the storage command groups. The versioned machine contract,
operation catalog, and safe retry rules are defined in
[`external-ai-cli-plan.md`](external-ai-cli-plan.md). New CLI work must follow both
documents.

### Project

```shell
roi-h rpa project list
roi-h rpa project show
roi-h rpa project create NAME --use
roi-h rpa project use NAME
roi-h rpa project paths
roi-h rpa project doctor [--full]
roi-h rpa project export NAME --output FILE [--mode definition|full]
roi-h rpa project import FILE [--name NAME] [--verify-only]
roi-h rpa project rename OLD NEW
roi-h rpa project delete NAME
```

### Environment

```shell
roi-h rpa env show
roi-h rpa env set dev|prod
roi-h rpa env doctor
```

### Store

```shell
roi-h rpa store status
roi-h rpa store check [--full]
roi-h rpa store backup --output FILE
roi-h rpa store restore FILE
```

Use `store`, not `database`, in the stable CLI because SQLite is an adapter detail.

### Run files

```shell
roi-h rpa run input add SOURCE --as NAME
roi-h rpa run files --run-id RUN_ID
roi-h rpa artifact put --run-id RUN_ID --source run://output/report.xlsx
roi-h rpa artifact list --run-id RUN_ID
roi-h rpa artifact export ARTIFACT_ID --output FILE
```

### Diagnostics and support

```shell
roi-h diagnostics show
roi-h diagnostics tail
roi-h support-bundle --run RUN_ID --redacted
```

### Retention

```shell
roi-h rpa gc plan
roi-h rpa gc show PLAN_ID
roi-h rpa gc apply PLAN_ID
```

### Output contract

Every command supports structured JSON without mixing human prose into stdout. Progress
and diagnostics go to stderr. JSON results use stable schemas and include:

```json
{
  "ok": true,
  "home": "...",
  "project": "acme",
  "project_id": "prj_...",
  "environment": "dev",
  "changed": false,
  "result": {}
}
```

## 29. Migration from the Current Layout

### 29.1 Layout versions

Current home configuration version is 3. The target layout becomes home layout version 4.

The migrator detects:

- version 3 current layout;
- partially migrated version 4 staging;
- completed version 4;
- unsupported future versions; and
- unversioned legacy homes.

### 29.2 Preflight

Before changes:

1. resolve the exact data home;
2. list projects and sizes;
3. verify ownership and permissions;
4. detect active run and migration leases;
5. run store quick checks;
6. verify package digests;
7. inventory plaintext secrets without printing values;
8. inventory path-bearing recipes and events;
9. detect dev/prod skill conflicts;
10. detect package identity conflicts;
11. estimate required free space; and
12. write a migration plan.

### 29.3 Backup

Migration creates:

- consistent store backups;
- a manifest of existing files and hashes;
- copies or recoverable moves for configuration and plaintext-secret migration;
- exact source layout version; and
- rollback instructions.

The backup destination should be operator-selectable and preferably outside the live data
home.

### 29.4 Structural mapping

```text
OLD                                           NEW
config.json                                   config.json
skills/                                       skills/
projects/P/config.json                        projects/P/project.json
projects/P/reference/                         projects/P/reference/
projects/P/dev/skills/                        projects/P/skills/
projects/P/prod/skills/                       conflict review; never ambient prod source
projects/P/E/rpa.sqlite                       projects/P/environments/E/store/activegraph.sqlite
projects/P/E/automations/                     projects/P/packages/automations/ + channels/E/
projects/P/E/artifacts/R/_workspace/          projects/P/environments/E/runs/R/workspace/work/
projects/P/E/artifacts/R/runtime/             projects/P/environments/E/runs/R/runtime/
projects/P/E/artifacts/R/phases/              projects/P/environments/E/runs/R/phases/
projects/P/E/artifacts/R/<artifact files>     projects/P/environments/E/runs/R/artifacts/
projects/P/.locks/E/                          projects/P/environments/E/runtime/locks/
projects/P/secrets.json                       SecretStore + secrets.meta.json
projects/P/feedback/feedback.jsonl            ActiveGraph when mappable; legacy archive otherwise
```

### 29.5 Skill conflicts

When dev and prod editable skill directories contain:

- identical content: keep one project source;
- same name with different content: stop and produce a conflict report;
- prod-only content referenced by an immutable package: retain inside that package, do
  not make it ambient source; and
- prod-only content not referenced by a package: quarantine for explicit operator
  review.

No last-write-wins merge.

### 29.6 Automation migration

For every existing environment package:

1. verify or seal legacy manifest according to existing package policy;
2. calculate canonical package digest;
3. install once into the project package store;
4. create an environment channel reference when the package was selected there;
5. detect name/version digest conflicts; and
6. retain source paths until verification completes.

### 29.7 Path migration

Classify stored paths:

- already logical: validate;
- under current run workspace: normalize to `run://work/...`;
- under current run artifacts: resolve to artifact identity;
- under project references: normalize to `project://reference/...`;
- under automation package: normalize to `automation://...`;
- external but file copied into an artifact: replace with artifact identity where
  semantics permit;
- external and still required: mark recipe non-portable and block production shipping;
  and
- missing/ambiguous: require operator repair.

Never replace a path based only on matching basename.

Never rewrite immutable historical ActiveGraph event payloads as an ordinary layout
migration. For current object state, append a typed migration patch/event that maps the
legacy physical value to its logical identity. Recipes and manifests may be rewritten
only by creating a new version or migrated copy. Full exports report any inactive legacy
physical paths that remain in historical event payloads.

### 29.8 Store migration

For each environment:

1. create a consistent source backup;
2. create target store through the supported ActiveGraph migration path when schema
   conversion is required;
3. otherwise restore a consistent backup into target staging;
4. replay representative and then all runs;
5. rebuild observer projections;
6. reconcile artifacts against the new logical identities;
7. validate lineage and archive/snapshot state; and
8. atomically select the version-4 project only after all checks pass.

### 29.9 Resumability

Migration writes a journal outside both source and target trees:

```json
{
  "migration_id": "mig_...",
  "source_version": 3,
  "target_version": 4,
  "project_id": "prj_...",
  "steps": [
    {"name": "backup-dev-store", "status": "completed", "digest": "..."}
  ]
}
```

Each step is idempotent. Restart resumes after validating completed-step outputs.

### 29.10 Activation and rollback

Activation uses atomic directory rename on the same filesystem.

The old project becomes a read-only migration backup. Rollback:

1. confirms no version-4 runs were added, or explicitly exports them;
2. acquires maintenance lock;
3. swaps directories;
4. restores home selection;
5. verifies old layout; and
6. reports any new secret-provider entries left in place.

## 30. Security Threat Model

### 30.1 Archive attacks

Defend against:

- zip slip/path traversal;
- absolute archive paths;
- symlink and hard-link escapes;
- duplicate normalized names;
- case-fold collisions;
- decompression bombs;
- excessive entry counts;
- oversized manifest fields;
- executable-bit smuggling;
- device files; and
- archive entries omitted from the signed/hashed manifest.

### 30.2 Skill filesystem attacks

Logical-path validation is necessary but not sufficient. A malicious skill process may
still use OS interfaces directly.

For untrusted skills, use OS-level sandboxing or containerization in addition to:

- minimal subprocess environment;
- working-directory isolation;
- declared filesystem capabilities;
- network-host policy;
- approval policy;
- secret minimization; and
- timeout/process-tree termination.

### 30.3 Secret leaks

Test:

- exceptions containing secret values;
- subprocess command lines;
- environment dumps;
- ActiveGraph args/output;
- recipe distillation;
- support bundles;
- observer responses;
- archive manifests;
- browser screenshots;
- HTTP URLs/query strings; and
- custom tool stdout/stderr.

### 30.4 Artifact exposure

Observer and export paths must:

- select by project, environment, run, and artifact identity;
- validate containment;
- reject symlinks;
- optionally verify digest;
- use bounded preview; and
- set safe content types and download headers.

### 30.5 Cross-project confusion

Project slug is not authority. Every internal scope carries stable `project_id`.
Run IDs should be globally unique, but all lookups still include project and environment.

## 31. Failure Scenarios

Implementation tests and operator messages must cover at least:

1. power loss while writing an artifact staging file;
2. process crash after artifact rename but before event append;
3. event record exists but artifact bytes are missing;
4. SQLite database locked by a second writer;
5. SQLite database corrupt;
6. store schema newer than installed ActiveGraph;
7. disk full during event append;
8. disk full during export;
9. export destination disconnects;
10. import archive contains `../`;
11. import archive contains a symlink;
12. import archive has a decompression bomb;
13. import collides with an existing project slug;
14. import collides by stable project ID;
15. package version collides with a different digest;
16. project rename crosses filesystems;
17. project deletion attempted while a run is active;
18. migration interrupted after store backup;
19. migration interrupted after target staging;
20. plaintext secret migration fails halfway;
21. Keychain unavailable in a headless session;
22. production package references a missing secret;
23. recipe contains a legacy absolute path;
24. custom tool returns a path outside allowed roots;
25. observer reads while a writer appends;
26. observer sees a compacted run;
27. fork requested below compaction horizon;
28. stale run lease after process death;
29. clock moves backwards;
30. run IDs differ only by case on a case-insensitive filesystem;
31. artifact filename uses Unicode normalization collision;
32. support bundle redaction encounters an unserializable value;
33. diagnostics directory is unwritable;
34. restore validation fails after staging; and
35. application update occurs while data layout migration is required.

## 32. Testing Strategy

### 32.1 Unit tests

Test:

- selection precedence;
- project and run ID validation;
- logical-path parsing and normalization;
- path containment;
- Windows and POSIX path edge cases;
- Unicode and case collisions;
- filesystem-capability enforcement;
- artifact attach state transitions;
- archive manifest validation;
- import entry validation;
- redaction;
- secret namespacing;
- retention classification;
- migration step idempotency; and
- error-code stability.

### 32.2 Interface tests

Every deep module is tested through its external interface:

- `WorkspaceCatalog`;
- `PathResolver`;
- `RunStorage`;
- `StoreLifecycle`;
- `ProjectArchive`;
- `SecretStore`;
- `DiagnosticSink`; and
- `RetentionPlanner`.

Tests should not reach past an interface merely to verify private directory-joining
helpers.

### 32.3 Integration tests

Cover:

- create project, start run, invoke tools, attach artifacts, reopen, observe;
- concurrent independent runs in one environment store;
- ship, promote, and run package without ambient skills;
- full export and import round trip;
- definition export and import round trip;
- export while a run is active;
- store backup and restore;
- version-3 to version-4 migration;
- plaintext secret migration to a fake SecretStore;
- observer artifact preview through identity;
- doctor quick/full;
- reconciliation repair and non-repair cases; and
- retention plan/apply.

### 32.4 Fault-injection tests

Inject failures:

- after every artifact attach stage;
- after every archive/import/migration stage;
- during SQLite backup;
- before and after atomic rename;
- on `fsync`;
- on diagnostic emission;
- on Keychain access;
- on free-space preflight race; and
- on maintenance-lock acquisition.

### 32.5 Portability tests

Build a project on one temporary root and import into another with a different absolute
path. No recipe, event, package, or manifest may require the source root.

Run path tests against:

- macOS case-insensitive semantics;
- Linux case-sensitive semantics;
- Windows drive and reserved-name rules; and
- non-ASCII names.

### 32.6 Security tests

Include:

- malicious archives;
- path escapes;
- symlink races where feasible;
- secrets in nested structures;
- secrets in exception strings;
- support bundle inspection;
- observer traversal attempts;
- custom tool capability violations;
- untrusted package import state; and
- publication boundary inspection.

### 32.7 Packaging tests

Wheel and source distributions must not include:

- `.roi-h`;
- `.roih`;
- SQLite/WAL/SHM;
- artifacts;
- project/custom/shared skills;
- browser profiles;
- diagnostics;
- migration backups; or
- secrets.

## 33. Performance and Scale Gates

Measure before changing adapters.

Track:

- events appended per second;
- SQLite busy/locked rate;
- p50/p95/p99 event append latency;
- run reopen/replay latency;
- observer projection latency;
- database bytes per run/event;
- artifact hashing throughput;
- export/import throughput;
- full doctor duration;
- backup duration; and
- retained workspace/artifact bytes.

Escalate beyond SQLite when one or more are sustained:

- lock errors after configured busy timeout and bounded retries;
- required multi-host writers;
- store size makes backup/restore operationally unacceptable;
- observer workload materially blocks writes despite read-only WAL behavior; or
- recovery objectives cannot be met locally.

The Postgres change should replace the store adapter at the seam. It must not alter
logical paths, project archives, artifact identity, or ActiveGraph's domain authority.

## 34. Compatibility Policy

### Application versions

An application update does not automatically mutate project data.

On open:

- compatible layout: proceed;
- older supported layout: report migration requirement and exact command;
- newer layout: fail closed and report required application version; and
- known read-only compatibility: allow doctor/export only.

### Project archive versions

Import supports:

- current format;
- explicitly supported older formats with staged migration; and
- verify-only inspection of newer formats when the manifest can be safely parsed.

Unknown archive fields do not authorize unknown files.

### Automation packages

Package manifests declare:

- package schema;
- compatible ROI-H versions;
- compatible ActiveGraph requirements;
- required core skill versions or digests;
- required secrets; and
- logical-path schema.

## 35. Implementation Sequence

Each phase should land independently with tests, migration notes, and no unrelated
changes.

### Phase 0 — Freeze terminology and decisions

Deliver:

- canonical glossary;
- accepted authority matrix;
- target layout version;
- logical-path schemes;
- export modes;
- secret namespace decision;
- diagnostics rule; and
- SQLite durability decision or explicitly tracked ActiveGraph prerequisite.

Acceptance:

- no unresolved overloaded use of “log,” “artifact,” “workspace,” or “project”;
- README and distribution documentation can link to one storage authority; and
- hard-to-reverse decisions are recorded as ADRs where warranted.

### Phase 1 — Introduce typed layout without moving data

Deliver:

- versioned home/project/environment models;
- `WorkspaceCatalog`;
- typed project/environment/run paths;
- read-only layout inspection;
- `project paths`; and
- compatibility detection for version 3.

Acceptance:

- no behavior change for existing runs;
- every current physical path can be represented by the typed layout;
- observer can receive resolved paths rather than constructing them; and
- tests use explicit temporary homes.

### Phase 2 — Logical paths and enforcement

Deliver:

- `LogicalPath`;
- `PathResolver`;
- worker input materialization;
- output normalization;
- filesystem-root capability enforcement;
- built-in skill declarations; and
- legacy physical-path detection.

Acceptance:

- new ActiveGraph step/artifact records do not persist physical paths;
- new distilled recipes contain no machine-specific paths;
- path escapes fail before subprocess execution;
- project references are read-only by policy; and
- production shipping rejects non-portable runs.

### Phase 3 — New run storage

Deliver:

- target run directory;
- `RunStorage`;
- workspace roots;
- runtime separation;
- atomic artifact attach;
- artifact identity;
- versioned phase handoffs; and
- updated reconciliation.

Acceptance:

- mutable workspace files are not listed as artifacts;
- artifact crash windows are fault-injection tested;
- observer accesses artifacts by identity;
- old and new layout runs are distinguishable; and
- no caller outside storage tests manually joins run artifact paths.

### Phase 4 — ActiveGraph store lifecycle

Deliver:

- `StoreLifecycle`;
- quick/full store check;
- consistent backup;
- staged restore;
- environment maintenance locks;
- effective PRAGMA reporting; and
- typed failures.

Acceptance:

- live-store raw copy is absent from production code;
- restore cannot replace a healthy store with an invalid one;
- backup passes integrity and replay checks;
- observer read concurrency is tested; and
- SQLite durability limitations are accurately reported.

### Phase 5 — Observer projection seam

Deliver:

- project catalog consumption;
- isolated ActiveGraph projection adapter;
- no route-level SQL;
- artifact identity preview;
- compacted-run test fixtures; and
- layout-version-aware discovery.

Acceptance:

- observer remains read-only;
- observer does not create schema/directories;
- all physical path resolution goes through storage modules; and
- ActiveGraph SQLite schema knowledge exists in one adapter only.

### Phase 6 — Skill/package layout

Deliver:

- project-level editable skills;
- single immutable package store;
- environment channel references;
- package migration/integrity logic;
- production ambient-skill denial; and
- enhanced shipping gate.

Acceptance:

- package bytes are not duplicated for dev/prod;
- production execution remains self-contained;
- promotion is an atomic reference change;
- package identity conflicts fail closed; and
- dev/prod legacy skill differences generate an explicit conflict.

### Phase 7 — SecretStore

Deliver:

- secret metadata schema;
- macOS Keychain adapter;
- environment-variable adapter for headless use;
- project/environment namespacing;
- scoped worker injection;
- plaintext migration plan/apply; and
- redaction tests.

Acceptance:

- a newly created project contains no plaintext secret values;
- dev/prod values are isolated;
- list/show/export reveal names only;
- support bundles contain no seeded test secrets; and
- failed migration retains recoverable original data and reports its location.

### Phase 8 — Diagnostics

Deliver:

- `DiagnosticSink`;
- home rotation;
- bounded failed-run diagnostics;
- redaction;
- diagnostic CLI;
- diagnostic IDs in operator failures; and
- support bundles.

Acceptance:

- ActiveGraph events are not duplicated;
- ROI-H can report a store-open failure without the store;
- diagnostics remain optional/failure-isolated;
- unwritable diagnostics never mask the original error; and
- support bundle has automated secret-leak tests.

### Phase 9 — Project export/import

Deliver:

- archive format and manifest;
- definition/full export;
- consistent store snapshots;
- secure streaming import;
- verify-only;
- collision policy;
- staged migration;
- atomic activation; and
- missing-secret/trust report.

Acceptance:

- full round trip succeeds into a different home path;
- imported automations run without source-machine paths;
- archive attacks fail before extraction;
- existing projects remain unchanged on failure;
- secrets/runtime/diagnostics are absent; and
- every included file is manifest-listed and hash-verified.

### Phase 10 — Layout migration

Deliver:

- version-3 inventory;
- plan;
- backup;
- resumable journal;
- structural mapping;
- path normalization;
- skill/package conflict reports;
- secret migration integration;
- activation; and
- rollback.

Acceptance:

- repeated migration after interruption is safe;
- ambiguous data is never silently merged;
- old project remains recoverable;
- all migrated runs replay and reconcile; and
- doctor passes before activation.

### Phase 11 — Retention and recoverable deletion

Deliver:

- `RetentionPlanner`;
- GC plan/show/apply;
- conservative defaults;
- pinned runs;
- stale staging cleanup;
- recoverable project deletion; and
- explicit permanent purge.

Acceptance:

- no durable deletion occurs without a plan;
- stale plans cannot apply after state changes;
- active runs block relevant deletion;
- pinned runs are excluded; and
- applied deletion reports exact identities and recoverability.

### Phase 12 — Compaction qualification

Deliver only after need:

- snapshot projection qualification;
- archive backup;
- fork-horizon UX;
- dry-run;
- apply/recovery;
- observer support; and
- upgrade compatibility tests.

Acceptance:

- every compacted run remains observable and replayable to the documented horizon;
- loss of old fork points is explicit;
- archive history is restorable; and
- interruption cannot silently lose the only copy of history.

### Phase 13 — Scale adapter, only when triggered

Deliver only after metrics cross a documented gate:

- supported ActiveGraph Postgres adapter configuration;
- migration journey;
- backup/restore procedures;
- concurrency qualification; and
- unchanged project/run/path interfaces.

## 36. Agent Execution Rules

Implementation agents must:

1. read this document and the distribution document before editing;
2. inspect the current worktree and preserve unrelated user changes;
3. implement phases in dependency order;
4. avoid mixing layout migration with unrelated runtime features;
5. add or update tests in the same change as behavior;
6. use the canonical terminology;
7. keep ActiveGraph authoritative;
8. avoid persisting physical paths in new durable data;
9. avoid destructive cleanup without a plan;
10. never print or fixture real secret values;
11. validate package/archive contents, not only command exit status;
12. test both clean and interrupted state transitions;
13. document compatibility and rollback;
14. run publication-boundary checks after adding fixtures; and
15. report incomplete external prerequisites honestly.

Suggested commit boundaries follow the phases above. A commit should be independently
reviewable and should not claim a migration or export journey is complete until its
failure and rollback paths are tested.

## 37. Acceptance Criteria for the Completed Refactor

The refactor is complete only when all statements below are true.

### Storage

- One versioned layout module resolves all home, project, environment, run, artifact,
  package, runtime, and diagnostic paths.
- Project definitions and environment execution state are visibly separate.
- Mutable workspaces and durable artifacts are separate.
- No new recipe/event/manifest requires a source-machine absolute path.
- Custom skills, packages, artifacts, databases, and runtime state live outside the core
  checkout and Python distribution.

### ActiveGraph

- ActiveGraph remains the only durable run-history authority.
- Run, phase, invocation, approval, artifact, feedback, and policy state are reconstructable
  from ActiveGraph.
- Store health, backup, restore, and migration are supported operator journeys.
- Backup of an active SQLite store is consistent.
- Observer projections do not become a second authority.

### Diagnostics

- There is no duplicated project run log.
- Database/startup/update failures can be diagnosed when ActiveGraph is unavailable.
- Diagnostic retention and size are bounded.
- Seeded secret-leak tests pass.

### Portability

- `project export` and `project import` round-trip a full project between different
  absolute homes.
- Definition exports omit run history.
- Full exports include consistent run history and durable registered files.
- Both modes exclude secret values, workspaces, runtime state, browser profiles, caches,
  locks, and diagnostics.
- Imports are staged, validated, collision-safe, and atomic.

### Security

- Arbitrary physical paths are not part of normal recipe/tool execution.
- Declared filesystem capabilities are enforced.
- Production packages are self-contained and do not read ambient custom skills.
- Secret values use an environment-isolated provider.
- Malicious archive and observer traversal tests pass.

### Recovery

- Interrupted artifact attachment is reconcilable.
- Interrupted export leaves the destination unchanged.
- Interrupted import leaves existing projects unchanged.
- Interrupted migration resumes or rolls back.
- Restore validates before activation.
- Project deletion is recoverable by default.

### Operations

- Project doctor explains exact failures and safe next actions.
- GC and compaction are plan-first.
- Store durability settings are observed and reported rather than assumed.
- Concurrent-run SQLite behavior is measured and qualified.
- Application updates do not incidentally migrate user data.

### Publication

- GitHub, wheel, and source-distribution gates exclude all user-owned state.
- Project archives and migration backups cannot enter release artifacts.
- CI inspects built artifacts in addition to repository paths.

## 38. Recommended First Milestone

The first production-worthy milestone is Phases 0 through 4:

1. canonical model;
2. typed layout;
3. logical paths and enforced filesystem capabilities;
4. run workspace/artifact separation; and
5. store health plus consistent backup.

This milestone should land before project export/import. Without logical paths and a
consistent store snapshot, an archive can be syntactically valid while still depending
on the source machine or missing live WAL data.

The second milestone is Phases 5 through 9:

1. observer seam;
2. package/channel cleanup;
3. SecretStore;
4. narrow diagnostics; and
5. secure project export/import.

Layout migration and retention follow only after the target behavior is proven on newly
created projects.

## 39. Final Architectural Rule

ROI-H should be understandable through four durable concepts:

```text
Project definition
    selects an immutable Automation package
        executed as an ActiveGraph Run
            producing durable Artifacts
```

Everything else has a narrower role:

- the run workspace is mutable execution scratch;
- runtime state helps a process continue;
- a projection helps a person understand events;
- a diagnostic record explains why ROI-H could not record or operate;
- a backup protects the store;
- an archive transports a project; and
- a retention plan controls cleanup.

Keeping those roles separate is the core of the refactor.
