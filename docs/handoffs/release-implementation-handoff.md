# ROI-H Release Implementation Handoff

Use this prompt in a Codex task opened at:

```text
/Users/rau/Desktop/Projects/roi-h-release-plan
```

The intended branch is:

```text
codex/release-work-plan
```

## Objective

Implement, qualify, stage, and release the first installable ROI-H version. Deliver the
one-line user-local installer, safe update and rollback, Playwright Chromium setup,
trusted release metadata, public package artifacts, and clean-machine proof.

Work through the plan in dependency order. Do not stop after package publication. A
release is complete only when a clean supported machine can install, run doctor, update,
roll back, and uninstall while user data remains safe.

## Read First

Read these files completely:

1. `AGENTS.md`
2. `docs/release-implementation-plan.md`
3. `docs/distribution-and-updates.md`
4. `docs/external-ai-cli-plan.md`
5. `docs/handoffs/external-ai-cli-implementation-handoff.md`
6. `docs/project-storage-activegraph-refactor.md`
7. `README.md`
8. `pyproject.toml`
9. `scripts/qualify_release.py`
10. `scripts/check_publication_boundary.py`

Use `docs/release-implementation-plan.md` as the implementation authority. Use the
distribution document for product policy and the storage document for user-data safety.
Use the external-AI contract for machine results and error behavior.

## Working-Tree Rules

1. Confirm the worktree path and branch before edits.
2. Inspect all current changes.
3. Preserve unrelated user changes.
4. Commit the approved release plan documents as a documentation commit before runtime
   implementation if they are not committed.
5. Use small dependency-ordered commits.
6. Do not push, publish, create a public repository, create a release, or advance a
   channel until the matching phase and user authority permit it.
7. Do not modify the main worktree at `/Users/rau/Desktop/Projects/roi-h`.

The main worktree can have an active external-AI CLI implementation. Do not copy,
overwrite, or reset it. Reconcile committed changes normally when this branch later
needs them. After the release-plan documentation commit and before runtime work, inspect
the latest committed `main`. Merge or rebase only committed changes. Never copy
uncommitted files from the main worktree.

## Non-Negotiable Decisions

- Python support is 3.12 only.
- npm is not the primary installer.
- GitHub Actions are not used.
- The active ROI-H environment never updates itself.
- Install, update, rollback, and uninstall are user-local.
- The general installer never invokes `sudo`.
- User data stays in `ROI_H_HOME` or `~/.roi-h`.
- Application versions install beside each other.
- Activation uses one atomic pointer.
- The previous known-good version remains for rollback.
- Playwright and its matching Chromium revision are one usable runtime.
- Release metadata uses TUF. Do not invent a signed-JSON format.
- Build once and publish the exact qualified bytes.
- Stable-channel promotion is the final release mutation.

## Mandatory Decision Gate

Before the first external upload, record these decisions:

1. Public PyPI or private index.
2. Ownership and availability of the `roi-h` package name.
3. Ownership and hosting of `get.roi-h.dev`.
4. Staging and production TUF repository locations.
5. TUF root, targets, snapshot, and timestamp key custody.
6. Exact Python 3.12 patch.
7. Exact pinned `uv` version and bootstrap hashes.
8. First release version.
9. Qualified operating-system matrix.

The default plan assumes:

- public PyPI;
- anonymous one-line installation;
- macOS 14 arm64;
- Windows 11 x86-64;
- Ubuntu 24.04 x86-64;
- Linux OCI for fully managed production workers; and
- local manual release execution with no GitHub Actions.

If code secrecy is required, stop before publication. Public PyPI exposes the wheel to
all users.

## Required Installed Commands

Implement:

```shell
roi-h --version
roi-h version --output json
roi-h doctor
roi-h doctor --full
roi-h update --check
roi-h update
roi-h update --version VERSION
roi-h rollback
roi-h rollback --version VERSION
roi-h repair
roi-h uninstall
```

The agent command catalog must also expose equivalent structured operations:

```text
system.version
system.doctor
install.inspect
install.repair
update.check
update.plan
update.apply
rollback.plan
rollback.apply
uninstall.plan
uninstall.apply
```

An editable source checkout must return `install.not_managed` for update, rollback,
repair, and uninstall. It must not change the development environment.

## Deep Installer Module

Create a separate package:

```text
packages/roi-h-installer/
```

Its public module interface is:

```python
plan(request: InstallRequest) -> InstallPlan
apply(plan: InstallPlan) -> InstallResult
inspect() -> InstallationState
```

Keep this interface small. Put transaction recovery, artifact verification, Python
installation, environment creation, browser installation, doctor, activation, rollback,
launcher installation, PATH handling, and uninstall behind it.

Do not create shallow pass-through modules for each transaction step. Internal files are
allowed when they own real behavior, but they are not new external interfaces.

### Release repository seam

Define:

```python
refresh() -> RepositoryState
select(channel: str, version: str | None) -> TrustedRelease
fetch(target: TrustedTarget, destination: Path) -> VerifiedFile
```

Implement:

- TUF HTTPS adapter;
- local filesystem adapter for tests and staging.

### Platform seam

Define:

```python
paths() -> PlatformPaths
install_launcher(target: Path) -> LauncherResult
activate(pointer: ActivationPointer) -> None
path_status() -> PathStatus
```

Implement:

- POSIX adapter;
- Windows adapter;
- temporary-directory test adapter.

Keep platform conditions local to these adapters.

## Install Layout

Use the layout from the plan:

```text
<install-root>/
  bootstrap/
  installer/
    current
    versions/
  python/
  versions/
  browsers/
  cache/
  transactions/
  current
  install-state.json

<bin-root>/
  roi-h

<data-home>/
  config.json
  skills/
  projects/
```

Default to:

- Unix install root: `$XDG_DATA_HOME/roi-h` or `~/.local/share/roi-h`;
- Unix executable root: `$XDG_BIN_HOME` or `~/.local/bin`;
- Windows install root: `%LOCALAPPDATA%\ROI-H`;
- Windows executable root: `%LOCALAPPDATA%\ROI-H\bin`;
- data home: `ROI_H_HOME` or `~/.roi-h`.

Test and reject nested install and data roots.

## TUF Work

Use the maintained Python TUF implementation.

Create:

- ROI-H TUF operating rules;
- initial trusted root;
- stable delegation;
- prerelease delegation;
- installer delegation;
- consistent-snapshot repository builder;
- metadata expiry policy;
- key rotation procedure;
- emergency revocation procedure;
- local test repository; and
- HTTPS client adapter.

Initial key policy:

```text
root       2 of 3, offline
targets    1 of 2, offline release signing
snapshot   1 of 1, publisher environment
timestamp  1 of 1, publisher environment
```

Never create real production private keys in repository tests. Tests use disposable
keys. Production key material stays outside Git, packages, logs, and shell arguments.

Test rejection of:

- invalid signature;
- expired metadata;
- rollback;
- freeze;
- mixed snapshot and targets metadata;
- wrong target;
- changed target bytes;
- excessive target size; and
- unsupported root rotation.

## Bootstrap Work

Add small versioned scripts for:

```text
https://get.roi-h.dev/install.sh
https://get.roi-h.dev/install.ps1
https://get.roi-h.dev/windows
```

Each script:

1. detects operating system and architecture;
2. selects a fixed `uv` binary;
3. verifies fixed size and SHA-256;
4. installs `uv` under the ROI-H install root without changing unrelated `uv` state;
5. installs one exact Python 3.12 patch under the ROI-H install root;
6. verifies the pinned initial installer wheel and lock;
7. creates the installer environment;
8. runs the installer package; and
9. prints one exact PATH action when required.

Do not download a moving latest `uv`.

Provide inspect-before-run instructions. The scripts must have no tracking, telemetry,
workspace upload, secret collection, or silent privilege request.

## Release Artifacts

Produce:

```text
roi_h-V-py3-none-any.whl
roi_h-V.tar.gz
roi_h_installer-I-py3-none-any.whl
roi_h-runtime-V.lock
roi_h-installer-I.lock
release-V.json
release-notes-V.md
SHA256SUMS
qualification-V.json
```

The same application wheel bytes go to PyPI and the TUF target repository.

Make `pyproject.toml` the one writable application version source. Make
`roi_h.__version__` read installed package metadata. Verify agreement across package
metadata, tags, release metadata, CLI output, and OCI labels.

The application and installer have separate versions.

## Runtime Locks

Generate locks from committed resolution state.

The runtime lock must:

- omit development packages;
- omit local source overrides;
- pin all versions;
- include artifact hashes;
- preserve platform markers; and
- work on all qualified targets.

Install dependencies with hash enforcement. Then install the application wheel without
dependency resolution.

Do not use open-ended dependency resolution during user install or update.

## Install and Update Transaction

Implement this order:

1. Take an exclusive install-root lock.
2. Inspect and recover interrupted staging.
3. Refresh TUF metadata.
4. Select channel or exact version.
5. Return no change when the current installation is already healthy.
6. Download into a content-addressed cache.
7. Verify trust, size, and digest.
8. Install the exact Python patch.
9. Create a staged version environment.
10. Install the hashed dependency lock.
11. Install the ROI-H wheel without resolving dependencies.
12. Install or verify matching Chromium.
13. Write staged release state.
14. Run staged doctor.
15. Rename staging to the final version directory.
16. Atomically change the current pointer.
17. Run the launcher smoke test.
18. Restore the prior pointer if the smoke test fails.
19. Record the transaction.
20. Retain active and previous versions and required browsers.

Do not change the current pointer before staged doctor passes.

The same request must be idempotent. A repeated completed request returns its result. A
changed request with the same transaction identity returns a conflict.

## Browser Work

Use an installer-controlled `PLAYWRIGHT_BROWSERS_PATH`.

Run:

```shell
<staged-python> -m playwright install chromium
```

Do not call a global Playwright executable.

Keep browser revisions required by the active and rollback versions. Disable automatic
browser garbage collection during update. Remove only revisions that no retained version
uses.

On Linux:

- detect missing system libraries;
- do not invoke `sudo`;
- report exact supported remediation; and
- qualify the OCI image with the required libraries.

## Doctor and Data Safety

Doctor is read-only. It checks:

- launcher;
- pointer;
- application metadata;
- Python 3.12;
- built-in skills;
- data-home access;
- layout compatibility;
- Playwright package;
- Chromium revision;
- minimal browser launch;
- TUF metadata freshness;
- updater helper; and
- update disk space.

Do not use doctor to repair state. `roi-h repair` performs safe repairs.

An application update does not silently change user data. Each release declares readable
and writable home-layout versions and ActiveGraph compatibility.

When migration is required:

1. show it in the update plan;
2. back up affected project stores;
3. create a migration plan;
4. require review;
5. run the new version migration module;
6. verify it; and
7. state rollback limits before activation.

## Rollback

Rollback verifies the retained version and data compatibility before pointer change.

It:

1. runs retained-version doctor;
2. changes the pointer atomically;
3. tests the launcher; and
4. restores the original pointer on failure.

`update --version VERSION` restores an older trusted release that is no longer local.

## Uninstall

Normal uninstall removes only ROI-H-owned:

- launcher;
- application versions;
- installer versions;
- managed Python;
- browser revisions;
- cache;
- transaction state.

It preserves `ROI_H_HOME` and reports the path.

Data purge uses a separate plan and apply. Do not add one `--force` flag that deletes
everything.

## Qualification Tooling

Extend `scripts/qualify_release.py`. Keep it local and authoritative.

It must:

1. require a clean worktree;
2. verify publication and optional public-history rules;
3. verify version agreement;
4. use Python 3.12;
5. compile, lint, format-check, type-check, and test;
6. run CLI and agent-contract tests;
7. build the application archives once;
8. build the installer wheel;
9. generate locks and hashes;
10. inspect archive paths and installed files;
11. install into isolated temporary roots;
12. run version and doctor;
13. install and launch Chromium;
14. rebuild and compare digests;
15. create release metadata and notes;
16. validate TUF targets;
17. run Twine checks; and
18. write `qualification-V.json`.

Do not publish from an unclean tree or rebuild after qualification.

## Implementation Phases

### Phase 0: Release decisions

Complete the mandatory decision gate. Add tests or checks that make the decisions
machine-verifiable where possible.

### Phase 1: Application identity

Implement single-source version, version command, doctor, managed-install detection, and
stable paths.

### Phase 2: Local installer

Implement installer models, deep module, local repository, platform adapters, launcher,
pointer, transactions, recovery, rollback, repair, and uninstall in temporary roots.

### Phase 3: Candidate builder

Implement hashed locks, installer package build, artifact inspection, reproducibility,
manifest, checksums, notes, and qualification report.

### Phase 4: TUF

Implement the repository operating rules, local builder, trusted root, delegations,
publisher, HTTPS adapter, key procedures, and adversarial tests.

### Phase 5: Bootstrap and browser

Implement POSIX and PowerShell bootstrap scripts, pinned `uv`, exact Python, PATH, browser
root, Chromium, Linux detection, and clean-machine tests.

### Phase 6: User commands

Implement check, update, exact version, rollback, repair, uninstall, external-helper
handoff, structured results, and migration preflight.

### Phase 7: Staging

Publish only to staging. Run the full platform matrix with exact staging artifacts.

### Phase 8: Public release

This phase requires explicit publication authority. Upload the qualified app artifacts,
publish production TUF metadata, test exact-version install, promote stable, publish
bootstrap endpoints, verify the one-line journey, and publish the OCI digest.

## Test Method

Use test-driven implementation:

1. Write a failing test at the installer interface or installed CLI.
2. Implement complete behavior behind the interface.
3. Run focused tests.
4. Run related tests.
5. Commit one coherent change.

Use local temporary repositories and roots for most tests. Do not write tests against the
real user install root or data home.

Required test groups:

```text
contract
platform paths
launcher
atomic activation
transaction recovery
local repository
TUF verification
bootstrap
runtime locks
browser management
doctor
update
rollback
uninstall
data preservation
archive contents
reproducibility
staging journeys
```

Before each release candidate, run:

```shell
uv run python scripts/qualify_release.py
```

Then run clean-machine staging qualification. A local green suite alone is not a release.

## Commit Order

Use this order:

1. Release plan documentation.
2. Single-source version and version command.
3. Doctor and managed-install detection.
4. Installer contract models.
5. Local repository and platform adapters.
6. Launcher, pointer, and activation transaction.
7. Rollback, repair, and uninstall.
8. Runtime locks and installer package build.
9. Release candidate and reproducibility gate.
10. TUF operating rules and local repository.
11. TUF HTTPS adapter and adversarial tests.
12. POSIX bootstrap.
13. PowerShell bootstrap.
14. Python and browser installation.
15. Update commands and external handoff.
16. Migration preflight.
17. Staging platform journeys.
18. OCI image.
19. Release documentation and recovery runbooks.

Do not create one large installer commit.

## Publication Rules

Do not use GitHub Actions.

For initial PyPI publication:

- use the exact qualified files;
- use a project-scoped token from a credential provider or environment;
- never put the token in shell arguments or repository files; and
- verify the published digests.

For later unattended publication, use a non-GitHub Trusted Publisher. Prefer Google
Cloud.

Publishing and stable promotion require separate explicit user authority. Do not infer
that authority from an implementation request.

## Stop Conditions

Stop and ask the user when:

- public versus private distribution is not approved before upload;
- package-name ownership changes;
- production host or DNS access is missing;
- TUF production key custody is not available;
- a supported platform cannot pass browser qualification without hidden privilege;
- an update needs an irreversible data migration;
- a new external paid provider is required;
- current user changes conflict with the release implementation; or
- publication, channel promotion, DNS mutation, or public source history needs new
  authority.

Do not stop only because the work is large.

## Definition of Done

Release work is complete only when:

- a clean supported machine with no Python installs ROI-H;
- no administrator access is used by the general installer;
- version and doctor pass;
- Chromium launches;
- a project can be created without a source checkout;
- update check is read-only;
- update activates only after staged health passes;
- failure leaves the old version active;
- rollback works;
- uninstall preserves user data;
- all downloaded release targets are trusted and verified;
- published bytes match the qualified candidate;
- stable points to the tested release;
- package archives contain no user or customer data; and
- the final report records release URLs, versions, digests, test targets, and recovery
  instructions.

## Final Report

Report:

- implementation phases completed;
- app and installer versions;
- supported targets;
- exact artifacts and SHA-256 digests;
- qualification results;
- staging results;
- PyPI result;
- TUF metadata and channel result;
- bootstrap endpoint result;
- OCI digest;
- update and rollback proof;
- uninstall and data-preservation proof;
- commits created;
- actions not performed because authority was not given; and
- remaining blockers.
