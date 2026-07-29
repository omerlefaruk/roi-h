# ROI-H Distribution, Installation, and Updates

**Status:** Accepted design; storage, publication seam, and local release gate implemented;
installer pending
**Audience:** Release engineers and implementation agents
**Last updated:** 2026-07-29

This document is the authoritative product and implementation contract for distributing
ROI-H itself. It describes how an operator installs and updates the `roi-h` application.
It does not describe `roi-h rpa ship`, which publishes an automation package inside an
ROI-H workspace.

## Decision

ROI-H remains a Python distribution internally, published as a wheel and source
distribution to PyPI or an approved private Python index. Normal operators must not need
to understand Python, virtual environments, `pip`, `uv`, or Playwright.

The supported installation surface is a branded, user-local bootstrap installer:

```shell
curl -LsSf https://get.roi-h.dev | sh
```

Windows PowerShell uses an equivalent endpoint:

```powershell
irm https://get.roi-h.dev/windows | iex
```

The supported update surface is:

```shell
roi-h update
```

Running the original installer again must also be idempotent and update an existing
installation.

ROI-H will not use npm as its primary package or installation mechanism. An npm wrapper
may be considered later only if there is a demonstrated JavaScript-user requirement. It
must not become a second implementation of installation, version selection, or updates.

## Core publication boundary

The repository and Python distribution contain only the generic ROI-H product:

- the `roi_h` CLI, public Python interfaces, harness, and observer;
- observer static assets and `py.typed`;
- the generic `browser`, `files`, `excel`, `http`, `pdf`, `shell`, and `feedback`
  core skills;
- packaging metadata, the proprietary license notice, generic documentation, tests, and
  release tooling in the Git repository; and
- only the runtime subset above in the wheel and source distribution.

The following are user-owned data and must never be tracked in the generic core
repository, included in Python artifacts, or copied into a public GitHub repository:

- project automations and immutable automation snapshots;
- project and reusable custom skills;
- artifacts, downloads, screenshots, spreadsheets, recordings, and transcripts;
- SQLite databases, run histories, browser profiles, cookies, and sessions;
- project secrets or secret-bearing configuration;
- customer-specific source modules, fixtures, documents, and business terminology; and
- customer handoffs, extracted scripts, analysis, and design-QA captures.

`scripts/check_publication_boundary.py` enforces the tracked-file allowlist in the local
release gate and pre-push hook.
Distribution-content tests independently enforce the wheel and source-archive boundary.
Moving a project capability into `src/roi_h` or the packaged `skills/` tree requires an
explicit product decision that it is generic, supported, documented, and safe for every
ROI-H user.

### GitHub publication and legacy history

Ignoring or deleting a file does not remove it from older Git commits. The current local
repository predates this publication boundary and its legacy history contains customer
documents and generated automation material. No existing ref or tag may be pushed to a
public or shared GitHub repository until that history is sanitized.

The repository uses `.githooks/pre-push` to run:

```shell
python3 scripts/check_publication_boundary.py --history
```

The hook intentionally blocks pushes while a forbidden path exists anywhere in local
history. The current-tree check remains part of local qualification:

```shell
python scripts/check_publication_boundary.py
```

Before the first GitHub publication, work from a clean, fully qualified tree and choose
one of these proof-preserving procedures:

1. Create a new public repository with one fresh root commit containing only the
   qualified generic-core tree. Keep the old private Git directory under
   `~/.roi-h/private/` for local recovery.
2. Back up every private ref, use a history-filtering tool to remove every path rejected
   by the history check across every ref and tag, then run the history check again.

The fresh-root procedure is preferred before the first public release because the local
repository has no remote and no public history needs to be preserved. Never disable the
pre-push hook to work around a failing history check.

### User-owned storage

The storage seam is `resolve_home`: an explicit `--home` path wins, followed by
`ROI_H_HOME`, followed by `~/.roi-h`. The default must never depend on the repository or
the shell's current working directory.

```text
~/.roi-h/
  config.json
  skills/                         # reusable user-shared skills
  private/                        # non-runtime research/reference material
  projects/
    <name>/
      config.json
      reference/                  # project documents and source references
      secrets.json
      browser-profile/
      feedback/
      dev/
        rpa.sqlite
        skills/                   # editable project skills
        automations/              # immutable dev packages
        artifacts/
      prod/
        rpa.sqlite
        skills/
        automations/              # immutable promoted packages
        artifacts/
```

Skill resolution order is packaged core, user-shared, then project-local. A later scope
may override a tool name. Frozen automation execution does not read ambient shared
skills: every referenced non-core skill is copied into the immutable automation package
at ship time.

The operation historically called “promote to global” targets `~/.roi-h/skills`; it must
reject the packaged core skill directory. Promotion into the generic product requires a
normal reviewed source change, not a runtime copy.

## User contract

### Install

The one-line installer must:

1. Detect the operating system and architecture.
2. Install entirely in the current user's account without `sudo` or administrator access.
3. Provision a private installer runtime and a supported Python version.
4. Install the selected ROI-H release and its locked runtime dependencies.
5. Install the matching Playwright Chromium revision.
6. Create or preserve a durable ROI-H data home.
7. Make `roi-h` available on `PATH`, or print one exact action when the current shell
   cannot observe a changed `PATH`.
8. Run an installation health check before reporting success.

Successful output should be short and operational:

```text
ROI-H 0.1.0 installed successfully.
Run: roi-h doctor
```

Installation is not successful merely because the Python wheel was installed. The
`roi-h` command, its built-in skills, and the browser runtime must all pass the health
check.

### Inspect

The installed application must provide:

```shell
roi-h --version
roi-h doctor
roi-h update --check
```

`roi-h --version` must report the application version and may also report the release
channel. `roi-h doctor` must validate the executable, Python runtime, built-in skills,
workspace access, and Playwright browser compatibility without mutating user automation
data.

### Update

The normal update command is:

```shell
roi-h update
```

The operator must also be able to select or restore an exact release:

```shell
roi-h update --version 0.1.0
```

Update behavior must be:

1. Resolve signed release metadata for the configured channel.
2. Return successfully without changes when the requested version is already active.
3. Install the new version beside the active version, never over it.
4. Install or verify the Playwright browser revision required by the new version.
5. Run the new version's installation health check.
6. Atomically make the validated version current.
7. Retain at least the previous working version for rollback.
8. Leave the current version active if any earlier step fails.

An update must never modify automation packages, run history, project skills, secrets, or
workspace artifacts as an incidental part of replacing application code. Any data-schema
migration requires its own compatibility design, backup behavior, and release note.

ROI-H must not silently install updates. It may perform a lightweight update check and
show a non-blocking notice:

```text
ROI-H 0.2.0 is available. Run: roi-h update
```

### Uninstall

The application should provide:

```shell
roi-h uninstall
```

The default uninstall removes application versions, launchers, and managed browser
artifacts while preserving the ROI-H data home. Deleting projects, secrets, histories,
or automation artifacts requires a separate explicit destructive flag and confirmation.

## Installation architecture

The public installer endpoint is a small platform bootstrap, not the ROI-H application.
Its responsibilities are detection, verification, version selection, installation, and
recovery.

The implementation should use the following components:

- **Release index:** signed metadata identifying stable and optional prerelease versions.
- **Bootstrap scripts:** POSIX shell and PowerShell entrypoints hosted at stable HTTPS
  URLs.
- **Installer runtime:** a user-local, pinned `uv` binary or an equivalently small
  independently updateable installer.
- **Versioned environments:** one isolated Python environment per installed ROI-H
  version.
- **Launcher:** a stable `roi-h` executable or script that dispatches to the active
  version.
- **Updater helper:** code outside the active version's environment that can install and
  switch versions safely, including on Windows where running files may be locked.
- **Browser store:** a managed Playwright browser location shared by installed versions
  while retaining every revision needed by the current and rollback versions.
- **Data home:** durable operator state that is never placed inside a versioned
  environment.

The installer must not update a running environment in place. `roi-h update` should hand
off to the updater helper, which stages and validates the replacement before changing the
active-version pointer.

### Conceptual layout

Exact platform paths may follow native conventions, but the separation is mandatory:

```text
<install-root>/
  installer/
  launcher/
  release.json
  current
  versions/
    0.1.0/
    0.2.0/
  browsers/

<data-home>/
  projects/
  config.json
```

On Unix-like systems, `current` may be an atomically replaced symbolic link. On Windows,
the launcher may read an atomically replaced pointer file instead. User data must not be
reachable through the version switch.

The installed launcher should set or pass a stable `ROI_H_HOME` when the user has not
configured one explicitly. Repository development uses the same user-owned default and
must pass an explicit temporary `--home` for isolated tests.

## Playwright handling

Playwright is part of the usable ROI-H runtime, not an optional afterthought in the
installer experience.

Each Playwright Python release expects matching browser binaries. Installation and update
therefore must run the equivalent of:

```shell
python -m playwright install chromium
```

from the newly staged ROI-H environment before activation. Users must not need to run
this command themselves.

The implementation should add an internal setup operation that invokes Playwright through
the active environment's `sys.executable`. It must not depend on a globally installed
`playwright` command.

On Linux, required system libraries need an explicit policy:

- Prefer an OCI image with browser system dependencies already installed for unattended
  production workers.
- For workstation installation, detect missing system libraries and report exact
  platform instructions.
- Do not request root access implicitly from the general installer.

## Release artifacts and reproducibility

Every application release must produce:

- `roi_h-<version>-py3-none-any.whl`
- `roi_h-<version>.tar.gz`
- locked runtime dependency metadata for supported platforms
- hashes for every installer-consumed artifact
- signed release metadata
- release notes, including compatibility and migration information

A wheel with open-ended dependency resolution is not sufficient for a reproducible
operator installation. The installer must use release-specific locked dependency
metadata, or the application's published runtime dependencies must be constrained tightly
enough to recreate the qualified environment.

The Python package version must have one authoritative source. `pyproject.toml`,
`roi_h.__version__`, wheel metadata, release tags, the release index, and
`roi-h --version` must agree.

Public PyPI releases should use Trusted Publishing with short-lived OIDC credentials.
Private releases must use an approved authenticated index or artifact service. Installer
logs, command lines, and cached metadata must not expose registry credentials.

### Local qualification and publishing

ROI-H does not depend on GitHub Actions. GitHub may host the sanitized source repository
and release notes, while qualification and the initial package upload run explicitly on a
maintainer machine:

```shell
uv run python scripts/qualify_release.py
```

That one command synchronizes the locked development environment, checks the publication
boundary, compiles, lints, checks formatting and types, runs the tests on the supported
Python 3.12 runtime, builds a fresh wheel and source distribution with local source
overrides disabled, and validates both artifacts with Twine. It stops on the first failure
and clears stale files from `dist/` before building.

After reviewing the version and the two files in `dist/`, an authorized maintainer can
upload them with:

```shell
uv publish --check-url https://pypi.org/simple dist/*
```

`uv publish` must receive credentials through its credential provider or an
`UV_PUBLISH_TOKEN` environment variable populated outside repository files and shell
history. Use a project-scoped token for a manual local release, rotate it after suspected
exposure, and never store it in `.env`, Git configuration, release scripts, or command
arguments.

If unattended publishing becomes necessary, use a PyPI-supported non-GitHub Trusted
Publisher. The supported choices are Google Cloud, GitLab.com CI/CD, and ActiveState.
Google Cloud is the least coupled to the source host; GitLab requires a GitLab.com
repository or mirror. Keep the local qualification command authoritative so changing the
execution service does not create a second release process.

## Bootstrap security

A one-line remote installer is a privileged trust boundary even when it runs without
administrator access. The implementation must:

- serve scripts and metadata only over HTTPS from an owned stable domain;
- keep the bootstrap scripts small, versioned, and independently reviewable;
- verify release metadata signatures and artifact hashes before execution;
- reject an unsupported platform or an unverifiable artifact;
- avoid sending secrets, workspace paths, or project data to the release service;
- never require `sudo` or silently weaken TLS verification;
- avoid executing application code before its artifacts are verified; and
- publish a download-and-inspect alternative to piping directly into a shell.

Example inspectable flow:

```shell
curl -fLo roi-h-install.sh https://get.roi-h.dev/install.sh
less roi-h-install.sh
sh roi-h-install.sh
```

The release service should publish provenance or attestations where the selected package
index supports them.

## Release and channel policy

The default channel is `stable`. A prerelease channel may be added later:

```shell
roi-h update --channel prerelease
```

Version selection must follow semantic versioning:

- patch: compatible bug and security fixes;
- minor: backward-compatible features and explicitly managed data migrations;
- major: intentionally incompatible behavior requiring operator action.

Production automation workers should be pinnable to an exact ROI-H version or container
digest. Easy updates do not justify uncontrolled fleet drift.

The release order is:

1. Qualify source on every supported Python and operating-system target.
2. Build the wheel and source distribution once.
3. Validate package metadata and installed wheel contents.
4. Test a clean install, browser setup, `roi-h doctor`, update, rollback, and uninstall.
5. Publish to a staging index and run an end-to-end installer test.
6. Publish the immutable Python artifacts.
7. Publish signed release metadata that makes the release discoverable.
8. Update the stable channel only after the published artifacts pass installation tests.

Do not point the stable channel at an artifact that is still being uploaded or qualified.

## OCI distribution

The one-line workstation installer and an OCI image are complementary:

- use the installer for interactive operators and Python API consumers;
- use an OCI image for unattended workers, CI, and controlled production deployments.

The OCI image should contain the exact ROI-H release, Python runtime, locked
dependencies, Playwright Chromium, and required Linux system libraries. Tag it with the
application version and publish its digest. Production documentation should recommend
pinning the digest rather than relying on a mutable `latest` tag.

## Required CLI work

Before the installer is advertised, ROI-H must implement:

- `roi-h --version`
- `roi-h doctor`
- `roi-h update`
- `roi-h update --check`
- `roi-h update --version <version>`
- the internal Playwright setup/verification operation
- updater-helper handoff
- stable installed-data-home resolution

`roi-h uninstall` may follow in the same release, but the standalone installer must
already have a documented recoverable uninstall path.

These commands are an external product interface. Their machine-readable results and exit
codes should be stable and covered by tests.

## Acceptance criteria

The distribution experience is ready only when all of the following are proven on macOS,
Windows, and supported Linux targets:

- A clean machine with no suitable Python can install ROI-H with the documented one-line
  command and no administrator privileges.
- `roi-h` is available in a new shell after installation.
- `roi-h --version` matches the requested release.
- `roi-h doctor` verifies built-in skills and launches a minimal Playwright check.
- A first ROI-H project can be initialized without repository access.
- Updating from the previous release preserves the data home byte-for-byte except for an
  explicitly tested migration.
- A failed download, bad signature, missing dependency, or failed health check leaves the
  previous version active.
- An exact older version can be restored without reinstalling or deleting user data.
- Re-running the original installer is safe and idempotent.
- Uninstall preserves user data by default.
- Installer output never includes secrets or registry credentials.

## Implementation sequence for agents

Work in dependency order:

1. **CLI contract:** add version, doctor, setup internals, and stable exit behavior.
2. **Release metadata:** define the signed release-index schema, locked dependency
   artifact, and channel rules.
3. **Filesystem model:** implement platform paths, version staging, active pointer, data
   separation, and rollback.
4. **Updater helper:** implement install, update, exact-version restore, verification, and
   failure recovery outside the active environment.
5. **Bootstrap scripts:** add POSIX and PowerShell entrypoints that delegate to the same
   updater behavior.
6. **Release execution:** qualify and publish locally first; if unattended publishing is
   later required, run the same gate through a non-GitHub Trusted Publisher. Build once,
   publish through trusted credentials, sign metadata, and advance channels only after
   installer qualification.
7. **Platform qualification:** prove the complete acceptance matrix on clean macOS,
   Windows, and Linux environments.
8. **OCI image:** publish the matching worker image and verify application-version parity.

Do not collapse the updater into the application environment, store user data beneath a
version directory, make Playwright setup a manual user step, or report installation
success before the health check passes.
