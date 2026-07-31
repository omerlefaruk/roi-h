# Project Storage and ActiveGraph Authority

**Status:** Current storage contract

## Ownership

The application repository contains generic product code and Markdown guidance. Customer
projects, automation source, packages, runs, artifacts, databases, reference files, and
secrets belong in the ROI-H data home.

One project has this managed structure:

```text
projects/<project>/
  project.json
  secrets.meta.json
  reference/
  skills/                         # project Markdown guidance
  sources/automations/<name>/     # editable modular source
    automation.json
    phases/*.py
    lib/*.py
  packages/automations/<name>/<version>/
    manifest.json
    source/                       # immutable verified snapshot
  channels/dev/*.json
  channels/prod/*.json
  environments/dev/
    store/activegraph.sqlite
    runs/<run-id>/
  environments/prod/
    store/activegraph.sqlite
    runs/<run-id>/
```

## ActiveGraph authority

ActiveGraph events and objects are the sole authority for run state. They record:

- the frozen source digest and file list;
- materialized input names, sizes, and hashes;
- phase start, success, failure, blocking, and cancellation;
- attempt identities and diagnostic references;
- artifact identities, sizes, hashes, and phase lineage; and
- verification and terminal run status.

Run directories store files and redacted diagnostics. They do not contain a second event
log. `run-files.json` is a workspace lifecycle manifest, not run authority.

## Paths

Manifests, events, results, and packages use portable relative or logical paths. They must
not contain customer-machine absolute paths. Physical paths can exist only inside the local
runner process while it materializes files.

Phase source reads:

- inputs from `context.input_dir`;
- stable project material from `context.reference_dir`; and
- prior phase artifacts from `context.dependencies`.

It writes temporary state under `context.work_dir` and returned files under
`context.output_dir` through `context.output_path()`.

## Source and package identity

ROI-H rejects symbolic links, bytecode, missing phase modules, dependency cycles, duplicate
phase IDs, and source trees without a verification phase. A development run receives an
immutable source snapshot before execution.

A package contains schema-2 metadata, the exact source snapshot, the source run ID, the
source digest, the phase graph, declared secrets and hosts, and one package digest.
Package versions are immutable. Production channel changes select a verified version but do
not change its content.

## Concurrency

The manifest is a dependency graph. The runner starts only phases whose dependencies
succeeded. A non-parallel phase runs alone. Dependency-ready phases can run together only
when each declares `parallel_safe` and the group stays within `max_parallel`.

Each attempt has a separate read-only source copy, work directory, and output directory.
The coordinator checks the frozen digest before and after each wave. A failed phase blocks
its dependent phases. Independent phases can still complete and keep their evidence.

Artifact files are not authority by themselves. Artifact list, show, and export operations
first load the ActiveGraph artifact object and then verify the file size and digest. An
orphan or changed file is not a valid artifact.

## Secrets

Secret values stay in the operating-system secret provider. The coordinator resolves only
declared names and gives a phase a short-lived environment mapping. Requests, events,
results, diagnostics, manifests, packages, and files must not contain secret values.
The coordinator redacts diagnostics and result metadata. It removes phase work or output
that contains a declared secret and rejects the artifact. Durable result metadata cannot
contain a physical path.
