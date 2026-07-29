# ROI-H Release, Install, Update, and Rollback Implementation Plan

**Status:** Proposed implementation plan

**Audience:** Maintainers and implementation agents

**Last updated:** 2026-07-29

**Repository baseline:** `d58e6df09a11319ef6a1959a354b5912a4c6a910`

This document turns the accepted distribution design into a release implementation plan.
It defines the modules, interfaces, artifacts, trust model, transactions, test matrix,
and release gates required before ROI-H can be offered through a one-line installer.

The authoritative product rules remain in
[`distribution-and-updates.md`](distribution-and-updates.md). This document is the
implementation authority for the installer and release work.

## 1. Outcome

The finished user journey is:

```shell
curl -LsSf https://get.roi-h.dev | sh
roi-h doctor
roi-h update --check
roi-h update
```

Windows uses:

```powershell
irm https://get.roi-h.dev/windows | iex
roi-h doctor
roi-h update --check
roi-h update
```

Normal users do not install Python, `uv`, Playwright, or Chromium themselves. The
installer works in the current user account and does not require administrator access.

The installed application is versioned and recoverable. An update stages a complete new
version beside the current version, validates it, and then changes one atomic pointer.
The previous version remains available for rollback.

## 2. Current State

The repository has:

- Python package version `0.1.0`;
- Python support `>=3.12,<3.13`;
- one `roi-h` console entry point;
- a wheel and source-distribution build;
- a publication-seam check;
- a local qualification script;
- generic packaged skills;
- user data separated under `ROI_H_HOME` or `~/.roi-h`; and
- an accepted distribution design.

The repository does not yet have:

- a published PyPI project;
- a branded bootstrap script;
- an installer or updater helper;
- trusted release metadata;
- release-specific runtime locks;
- `roi-h --version`;
- `roi-h doctor`;
- `roi-h update`;
- rollback or uninstall;
- installer-managed Playwright Chromium;
- clean-machine install tests; or
- a released OCI worker image.

The current files under `dist/` are inspection artifacts. They are not a public release
and are not proof that the current committed source passed the full release gate.

## 3. Release Scope

### 3.1 First stable release

The first public stable release is `0.1.0` only if that version has not been published
before the final source is ready. If any `0.1.0` artifact is uploaded to a package index,
do not replace it. Increment the version.

The first release includes:

- the ROI-H application wheel and source distribution;
- a separate minimal ROI-H installer wheel;
- a pinned `uv` bootstrap version;
- one exact Python 3.12 patch release;
- a hashed runtime dependency lock;
- Playwright Chromium installation metadata;
- TUF release metadata;
- POSIX and PowerShell bootstrap scripts;
- version, doctor, update, rollback, and uninstall commands;
- release notes; and
- clean-machine qualification evidence.

### 3.2 Initial qualification matrix

The first stable channel is qualified on:

| Target | Installation form | Minimum |
| --- | --- | --- |
| macOS arm64 | Workstation installer | macOS 14 |
| Windows x86-64 | Workstation installer | Windows 11 |
| Linux x86-64 | Workstation and OCI | Ubuntu 24.04 |

The Playwright-supported operating-system list is wider. Do not claim support for a
target until the complete install, browser, update, rollback, and uninstall journey has
passed on that target.

The general installer never runs `sudo`. On Linux, it detects missing browser system
libraries and prints the exact platform action. The OCI image is the fully managed Linux
worker path because it can include those libraries.

### 3.3 Publication visibility decision

Before the first upload, the owner must choose one:

1. **Public PyPI:** anyone can download the wheel and inspect its Python code. The
   proprietary license controls permitted use but does not hide code.
2. **Private index:** downloads require authentication. The installer needs a secure
   credential setup and is not anonymous.

The default plan assumes public PyPI because the requested one-line install is anonymous.
If code secrecy is required, stop before publication and change the release target.

## 4. Installed Payload

The workstation installer creates:

```text
<install-root>/
  bootstrap/
    uv
  installer/
    current
    versions/
      I/
  python/
  versions/
    V/
      environment/
      release.json
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

Defaults:

| System | Install root | Executable root |
| --- | --- | --- |
| macOS and Linux | `$XDG_DATA_HOME/roi-h` or `~/.local/share/roi-h` | `$XDG_BIN_HOME` or `~/.local/bin` |
| Windows | `%LOCALAPPDATA%\ROI-H` | `%LOCALAPPDATA%\ROI-H\bin` |

The data home remains `ROI_H_HOME` or `~/.roi-h` on all systems. The install root and data
home must not be nested inside each other.

The application version environment contains:

- the ROI-H wheel;
- locked runtime dependencies;
- the `roi-h` entry point;
- built-in generic skills;
- observer static assets; and
- package metadata and license.

The browser root contains the Chromium revision required by the installed Playwright
version.

The installer does not add:

- project automations;
- custom skills;
- artifacts or downloads;
- run history or ActiveGraph stores;
- browser profiles, cookies, or sessions;
- secrets;
- customer material;
- Codex or another AI product; or
- developer tests and release scripts.

## 5. Module Design

### 5.1 Installer module

Create a separate `roi-h-installer` package under:

```text
packages/roi-h-installer/
```

It has one deep module interface:

```python
plan(request: InstallRequest) -> InstallPlan
apply(plan: InstallPlan) -> InstallResult
inspect() -> InstallationState
```

The interface includes:

- install, update, exact-version, rollback, repair, and uninstall requests;
- selected channel and release;
- file, network, disk, and platform requirements;
- effects and recovery steps;
- trusted target identities and hashes;
- staging and activation rules;
- error codes;
- data compatibility; and
- no-change behavior.

The implementation owns:

- install-root locking;
- interrupted-transaction recovery;
- trusted metadata refresh;
- artifact download and verification;
- managed Python installation;
- version environment creation;
- dependency installation;
- Playwright browser installation;
- staged doctor execution;
- atomic activation;
- rollback retention;
- launcher installation;
- PATH handling; and
- default data preservation.

The application does not duplicate this behavior.

### 5.2 Release repository seam

Define one repository interface used by the installer module:

```python
refresh() -> RepositoryState
select(channel: str, version: str | None) -> TrustedRelease
fetch(target: TrustedTarget, destination: Path) -> VerifiedFile
```

Use two adapters:

- a TUF HTTPS adapter for production; and
- a local filesystem adapter for deterministic tests and staging.

This is a real seam because the same installer behavior must run against a remote trusted
repository and a local test repository.

### 5.3 Platform seam

Define one small platform interface for behavior that actually differs:

```python
paths() -> PlatformPaths
install_launcher(target: Path) -> LauncherResult
activate(pointer: ActivationPointer) -> None
path_status() -> PathStatus
```

Use:

- a POSIX adapter;
- a Windows adapter; and
- an in-memory or temporary-directory test adapter.

Do not spread platform conditions through the installer transaction.

### 5.4 Application adapter

The installed application exposes:

```shell
roi-h --version
roi-h doctor
roi-h update --check
roi-h update
roi-h update --version VERSION
roi-h rollback
roi-h uninstall
```

`update`, `rollback`, and `uninstall` call the external installer helper. They do not
change the active application environment directly.

An editable source checkout returns `install.not_managed` for update and uninstall. It
must not modify a developer environment.

### 5.5 Release publisher module

Release preparation remains repository tooling, not installed application behavior.

Use one interface:

```python
qualify(source_revision: str, version: str) -> ReleaseCandidate
publish(candidate: ReleaseCandidate, destination: ReleaseDestination) -> PublishedRelease
promote(release: PublishedRelease, channel: str) -> ChannelResult
```

Qualification, publication, and channel promotion are separate operations. A failed
publish cannot change the stable channel.

## 6. Trust and Release Repository

### 6.1 Decision

Use The Update Framework through its maintained Python implementation for the installer
release repository. Do not design a private signed-JSON protocol.

TUF metadata protects:

- target authenticity and integrity;
- rollback;
- freeze;
- mixed metadata;
- wrong target selection;
- key rotation; and
- consistent snapshots.

The installer package ships with trusted root metadata. The root trust update follows
the TUF client workflow.

### 6.2 Roles

Initial roles:

| Role | Key handling | Threshold |
| --- | --- | --- |
| Root | Offline, separate devices or custodians | 2 of 3 |
| Targets | Offline release signing | 1 of 2 |
| Snapshot | Release publisher environment | 1 of 1 |
| Timestamp | Release publisher environment | 1 of 1 |

Private keys never enter Git, package files, shell arguments, logs, or build artifacts.
Root and targets keys must not be present on the public release host.

Before implementation, write the ROI-H TUF repository operating rules, including:

- metadata format;
- target naming;
- expiry periods;
- key rotation;
- emergency revocation;
- mirror locations;
- channel delegation; and
- consistent-snapshot behavior.

### 6.3 Repository layout

```text
https://get.roi-h.dev/
  install.sh
  install.ps1
  windows
  tuf/
    metadata/
    targets/
      app/
      installer/
      locks/
      release-notes/
```

Use delegated target roles for:

- `stable`;
- `prerelease`; and
- installer updates.

The stable role only points to releases that passed public installation qualification.

### 6.4 Bootstrap trust

The bootstrap script is the initial trust root. Keep it small and versioned.

It:

1. detects the platform;
2. selects one pinned `uv` build;
3. downloads it from a fixed HTTPS origin;
4. verifies its hard-coded size and SHA-256 digest;
5. installs it under the ROI-H install root without shell-profile mutation;
6. installs one exact Python 3.12 patch under the ROI-H install root;
7. downloads the pinned installer wheel and lock;
8. verifies the hard-coded initial hashes;
9. creates the installer environment; and
10. delegates all remaining work to `roi-h-installer`.

The bootstrap never installs an unpinned latest `uv`. It must offer a download, inspect,
and execute form in addition to the pipe form.

Later installer updates use TUF and do not require a new bootstrap script unless the
bootstrap trust root changes.

## 7. Release Artifacts

Build once per application version:

```text
roi_h-V-py3-none-any.whl
roi_h-V.tar.gz
roi_h_installer-I-py3-none-any.whl
roi_h-runtime-V.lock
roi_h-installer-I.lock
release-V.json
release-notes-V.md
SHA256SUMS
```

The TUF repository also publishes the matching metadata.

`release-V.json` contains non-authoritative product metadata that is covered by the TUF
target hash:

```json
{
  "schema_version": 1,
  "application_version": "0.1.0",
  "installer_version": "0.1.0",
  "python": "3.12.x",
  "playwright_version": "1.61.0",
  "browser": "chromium",
  "contract_major": 1,
  "home_layout_min": 4,
  "home_layout_max": 4,
  "minimum_installer_version": "0.1.0",
  "artifacts": []
}
```

The exact Python patch is fixed when the release candidate is built. Do not use a moving
`3.12` request during installation.

The runtime lock:

- excludes development dependencies;
- excludes local source overrides;
- fixes all dependency versions;
- contains artifact hashes;
- preserves required environment markers;
- is generated from the committed lock state; and
- is tested on each qualified platform.

The application wheel is the same byte sequence in PyPI and the TUF target repository.
Do not rebuild for a second destination.

## 8. Version Authority

Make `pyproject.toml` the one writable application version source.

`roi_h.__version__` reads installed package metadata. It does not contain a second
literal version.

The following must agree:

- `pyproject.toml`;
- installed package metadata;
- application wheel name;
- source-distribution name;
- release tag;
- TUF target metadata;
- release notes;
- `roi-h --version`; and
- OCI image label.

The installer package has an independent version because it can change without changing
the application.

## 9. Installation Transaction

### 9.1 Plan

The installer resolves a typed plan before state changes. It includes:

- requested channel and version;
- current installation state;
- trusted target versions and hashes;
- download bytes;
- required disk space;
- Python and browser changes;
- PATH change;
- data compatibility;
- versions retained for rollback;
- effects; and
- recovery action.

`--check` creates and returns a plan but does not download application targets or change
state.

### 9.2 Apply

Apply:

1. takes an exclusive install-root lock;
2. recovers or removes verified stale staging;
3. refreshes trusted TUF metadata;
4. verifies that the plan is still current;
5. downloads targets into a content-addressed cache;
6. verifies TUF trust, size, and hashes before use;
7. installs the exact managed Python patch;
8. creates a new version environment in staging;
9. installs the locked dependencies with hash enforcement;
10. installs the ROI-H wheel without dependency resolution;
11. installs or verifies the required Chromium revision;
12. writes staged release metadata;
13. runs the staged doctor;
14. atomically renames staging to the final version directory;
15. atomically changes the current-version pointer;
16. runs a launcher smoke test;
17. records the completed transaction; and
18. retains the previous version and required browser revision.

If any action before pointer change fails, the current version remains active.

If the launcher smoke test fails after pointer change, the installer atomically restores
the previous pointer and records `update.activation_rolled_back`.

### 9.3 Idempotency

Running the installer again with the same selected version:

- verifies current state;
- repairs a missing launcher or pointer when safe;
- returns `changed: false` when healthy; and
- does not reinstall or delete user data.

The installer persists a transaction ID and normalized request. Repeating the same
request returns the completed result.

## 10. Update and Rollback

### 10.1 Update check

```shell
roi-h update --check
```

Returns:

- installed version;
- selected channel;
- latest trusted version;
- update available;
- download size;
- migration requirement;
- support status; and
- exact next operation.

It does not modify state and does not install silently.

### 10.2 Update

```shell
roi-h update
roi-h update --version 0.1.0
```

The active application spawns the external installer helper and passes a typed request.
It does not run `pip`, modify its environment, or replace its own executable.

The updater keeps at least:

- the active version;
- the previous known-good version; and
- any version pinned by an operator or worker.

### 10.3 Rollback

```shell
roi-h rollback
roi-h rollback --version VERSION
```

Rollback:

1. verifies the requested installed version;
2. runs its doctor against the current data home;
3. checks data-layout compatibility;
4. atomically changes the pointer;
5. runs the launcher smoke test; and
6. restores the prior pointer if the smoke test fails.

Rollback does not download unless the user uses `roi-h update --version VERSION` to
restore a release that is no longer local.

## 11. Version, Doctor, Repair, and Uninstall

### 11.1 Version

```shell
roi-h --version
roi-h version --output json
```

Reports:

- application version;
- installer version;
- release channel;
- contract version;
- installation type: managed, editable, wheel, or unknown;
- Python version; and
- current release target digest.

### 11.2 Doctor

```shell
roi-h doctor
roi-h doctor --full
```

The default doctor is read-only and checks:

- launcher and current pointer;
- application metadata;
- Python 3.12;
- import and entry point;
- built-in skills;
- data-home access;
- project layout compatibility;
- Playwright package and Chromium revision;
- minimal browser launch;
- TUF metadata freshness;
- updater availability; and
- disk space for one update.

`--full` can also perform a temporary project and browser journey. It must not mutate
existing user projects.

Use:

```shell
roi-h repair
```

for safe launcher, pointer, metadata, and browser repairs. Doctor itself remains
read-only.

### 11.3 Uninstall

```shell
roi-h uninstall
```

Default uninstall removes:

- launcher;
- application version environments;
- installer environments;
- managed Python owned only by ROI-H;
- browser revisions owned only by ROI-H; and
- installer cache and state.

It preserves `ROI_H_HOME` and reports its path.

Data deletion is a separate destructive journey:

```shell
roi-h uninstall --plan-purge-data
roi-h uninstall --apply-purge PLAN_ID
```

## 12. Browser Management

Set one installer-managed browser root through `PLAYWRIGHT_BROWSERS_PATH`.

During install and update:

```shell
<staged-python> -m playwright install chromium
```

The installer does not call a global `playwright` executable.

Keep every Chromium revision required by:

- the active version; and
- the retained rollback version.

Disable automatic Playwright browser garbage collection during installer transactions.
The installer removes a browser revision only after no retained ROI-H version requires
it.

On Linux:

- detect required system libraries before activation;
- do not run `install-deps` with implicit privilege;
- return exact supported instructions when libraries are missing; and
- provide an OCI image with the libraries already installed.

## 13. Data Compatibility

Application replacement and data migration are separate operations.

Each release states:

- minimum and maximum readable home-layout versions;
- minimum and maximum writable home-layout versions;
- ActiveGraph compatibility; and
- migration requirements.

The staged doctor reads compatibility information without changing data.

If migration is required:

1. the update plan states the affected projects and environments;
2. ROI-H creates verified project or store backups;
3. the user reviews the migration plan;
4. the new version runs the migration through its supported migration module;
5. the migration is verified;
6. activation continues; and
7. rollback limits are stated before apply.

An update must not silently migrate, delete, compact, or rewrite user data.

## 14. Release Qualification

Extend `scripts/qualify_release.py` into a release-candidate builder. Keep the local
command authoritative and do not add GitHub Actions.

It must:

1. require a clean candidate worktree;
2. verify the publication seam and public-history state when source publication is
   requested;
3. verify version consistency;
4. use Python 3.12 only;
5. compile, lint, format-check, type-check, and test;
6. run agent-contract and CLI tests;
7. build the application wheel and source distribution once;
8. build the installer wheel;
9. generate hashed runtime and installer locks;
10. inspect every archive path and installed file;
11. install both wheels into isolated temporary roots;
12. run version and doctor smoke tests;
13. install and launch Chromium;
14. rebuild and compare artifact hashes for reproducibility;
15. generate the release manifest, checksums, and notes;
16. validate TUF target metadata;
17. validate packages with Twine; and
18. write a machine-readable qualification report.

The release candidate is immutable after qualification. Publication uses its exact
artifacts and digests.

## 15. Staging and Publication

### 15.1 Staging

Publish the candidate first to:

```text
https://staging.get.roi-h.dev/
```

Use a separate TUF root and metadata repository from production.

Run clean-machine tests from the public staging URL on every qualified target. Do not
install from the source checkout.

### 15.2 PyPI

For the first release, an authorized maintainer can publish locally with a project-scoped
token supplied through a credential provider or environment variable. Never put the
token in an argument, repository file, or shell history.

If unattended publishing is later required, use a non-GitHub PyPI Trusted Publisher.
Google Cloud is the preferred option because it does not require GitHub Actions or a
GitLab mirror.

Upload the already-qualified wheel and source distribution. Do not rebuild.

### 15.3 Production update repository

After PyPI upload:

1. verify PyPI file digests match the candidate;
2. publish the same application wheel and release targets to the production TUF
   repository;
3. publish the release through the `prerelease` delegation, including new targets,
   snapshot, and timestamp metadata;
4. perform public exact-version install, update, and rollback tests through the
   `prerelease` channel;
5. promote the same trusted target into `stable`;
6. publish the stable targets, snapshot, and timestamp metadata; and
7. perform one final clean stable install through the one-line endpoint.

Channel promotion is the last mutation.

## 16. OCI Worker Image

Build the OCI image from the same qualified candidate.

It contains:

- exact ROI-H application version;
- Python 3.12 patch;
- locked dependencies;
- Playwright Chromium;
- Linux system libraries;
- a non-root runtime user; and
- an empty data-home mount point.

Tag with the application version and publish the digest. Do not use `latest` in
production instructions.

OCI publishing does not block the macOS or Windows workstation installer, but the Linux
production claim requires it.

## 17. Failure Model

Stable installer codes include:

```text
install.not_managed
install.platform_unsupported
install.path_unavailable
install.locked
install.disk_space_insufficient
install.bootstrap_verification_failed
release.metadata_expired
release.metadata_untrusted
release.channel_not_found
release.version_not_found
release.target_verification_failed
release.version_mismatch
python.install_failed
environment.create_failed
dependency.lock_invalid
dependency.install_failed
browser.install_failed
browser.system_dependency_missing
doctor.failed
update.plan_stale
update.migration_required
update.activation_failed
update.activation_rolled_back
rollback.incompatible
uninstall.data_preserved
```

Every result states:

- request and transaction ID;
- installed and requested version;
- selected channel;
- changed;
- active pointer state;
- data state;
- staging state;
- retryable;
- diagnostic ID; and
- safe recovery action.

## 18. Implementation Phases

### Phase 0: Freeze release decisions

Deliver:

- public or private publication decision;
- package-name ownership check;
- qualified platform matrix;
- install-root and executable-root rules;
- initial Python patch and `uv` version policy;
- TUF operating rules and key custody;
- staging and production host decision; and
- `0.1.0` version availability decision.

Exit:

- no release identity or trust decision remains implicit.

### Phase 1: Installed application contract

Deliver:

- single application version source;
- `roi-h --version`;
- `roi-h version --output json`;
- read-only `roi-h doctor`;
- managed-install detection;
- stable installation paths; and
- contract and CLI tests.

Exit:

- a manually installed wheel can report its exact identity and health.

### Phase 2: Installer package and local adapter

Deliver:

- `packages/roi-h-installer`;
- typed request, plan, state, result, and error models;
- deep installer interface;
- local repository adapter;
- POSIX, Windows, and test platform adapters;
- launcher and atomic pointer;
- staging, activation, rollback, and recovery;
- install-root lock; and
- temporary-root integration tests.

Exit:

- a local trusted repository can install, update, roll back, repair, and uninstall
  without network access.

### Phase 3: Reproducible release artifacts

Deliver:

- runtime and installer locks with hashes;
- release manifest;
- checksum file;
- version consistency check;
- archive-content checks;
- installed-wheel smoke tests;
- reproducibility comparison; and
- machine-readable qualification report.

Exit:

- one command creates one immutable release candidate from a clean revision.

### Phase 4: TUF repository and publisher

Deliver:

- TUF repository operating rules;
- initial trusted root;
- key-generation and offline-signing procedure;
- stable, prerelease, and installer delegations;
- local TUF repository builder;
- TUF HTTPS installer adapter;
- metadata expiry and rotation tests; and
- rollback, freeze, mix-and-match, and wrong-target tests.

Exit:

- installer tests reject every untrusted or stale update case.

### Phase 5: Bootstrap and browser

Deliver:

- small POSIX bootstrap;
- small PowerShell bootstrap;
- pinned `uv` verification;
- exact Python 3.12 installation;
- installer environment setup;
- launcher PATH handling;
- shared Playwright browser root;
- Chromium install and health check;
- Linux system-library detection; and
- inspect-before-run documentation.

Exit:

- a clean supported workstation installs without a preinstalled Python.

### Phase 6: Application update commands

Deliver:

- `update --check`;
- `update`;
- exact-version restore;
- `rollback`;
- `repair`;
- `uninstall`;
- external updater handoff;
- idempotent rerun;
- migration preflight; and
- structured results through the external-AI command interface.

Exit:

- the active application never changes its own environment and all failure cases preserve
  or restore the last known-good version.

### Phase 7: Staging qualification

Deliver:

- staging release repository;
- clean macOS arm64 test;
- clean Windows x86-64 test;
- clean Ubuntu x86-64 test;
- browser journey;
- update from prior version;
- failed-update recovery;
- rollback;
- exact-version restore;
- uninstall with data preservation; and
- evidence report.

Exit:

- every acceptance test passes from public staging artifacts.

### Phase 8: First public release

Deliver:

- approved release notes;
- exact package version;
- qualified app and installer artifacts;
- PyPI publication;
- production TUF targets and metadata;
- stable-channel promotion;
- bootstrap endpoint publication;
- public install and update verification;
- OCI image and digest where required; and
- recovery and key-rotation runbooks.

Exit:

- a new user can install and update ROI-H through the documented command, and a failed
  update cannot remove the working version or user data.

## 19. Acceptance Tests

### Install

- No Python is present before the test.
- No administrator access is used.
- The one-line command installs the exact release.
- `roi-h` is available in a new shell.
- `roi-h --version` matches release metadata.
- Built-in skills load.
- Chromium launches.
- A project can be created outside a source checkout.

### Update

- `--check` does not change state.
- Same-version update returns no change.
- New-version update stages beside the old version.
- Current pointer changes only after doctor passes.
- Previous version remains installed.
- User data is unchanged.
- Interrupted download resumes or restarts safely.
- Bad hash, signature, expiry, lock, browser, or doctor leaves the old version active.

### Rollback

- Previous version activates without download.
- Data compatibility is checked.
- Failed rollback doctor restores the original pointer.
- Exact older version can be restored from the trusted repository.

### Uninstall

- Launcher, application, installer, owned Python, and owned browsers are removed.
- Data home remains.
- Shared or non-ROI-H runtimes are not removed.
- Purge requires a separate plan and apply.

### Supply chain

- Untrusted metadata is rejected.
- Expired metadata is rejected.
- Older metadata is rejected.
- Mixed metadata is rejected.
- Wrong target is rejected.
- Artifact hash mismatch is rejected.
- Package archives contain only approved generic product files.
- Published file digests match the qualified candidate.

## 20. Explicit Exclusions

Do not:

- use npm as the primary installer;
- use GitHub Actions;
- update an active environment in place;
- install a moving latest application or `uv` version;
- trust TLS alone for application update metadata;
- invent a private signature format;
- rebuild after qualification;
- put signing or publishing credentials in Git or arguments;
- store user data under an application version;
- run `sudo` from the general installer;
- install Playwright without its matching browser;
- delete the data home during normal uninstall;
- publish customer data, local automations, custom skills, or agent artifacts;
- claim an untested platform; or
- move the stable channel before public installation tests pass.

## 21. Primary References

- [Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
  for wheel and source-distribution publication.
- [PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/) for short-lived
  publishing credentials and supported non-GitHub publishers.
- [uv installation](https://docs.astral.sh/uv/getting-started/installation/) for pinned
  bootstrap installation.
- [uv Python management](https://docs.astral.sh/uv/guides/install-python/) for managed
  Python installation.
- [uv storage](https://docs.astral.sh/uv/reference/storage/) for controlled install
  locations.
- [Playwright browser management](https://playwright.dev/python/docs/browsers) for shared
  browser paths and revision handling.
- [Playwright system requirements](https://playwright.dev/python/docs/intro#system-requirements)
  for the initial target matrix.
- [The Update Framework overview](https://theupdateframework.io/docs/overview/) and
  [TUF specification](https://theupdateframework.github.io/specification/latest/) for
  trusted release metadata.

The paste-ready implementation handoff is
[`handoffs/release-implementation-handoff.md`](handoffs/release-implementation-handoff.md).
