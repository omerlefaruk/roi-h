# ROI-H Distribution and Updates

This document is the authoritative contract for distributing and updating the ROI-H
application. It does not define `roi-h rpa ship`, which publishes an automation inside an
ROI-H project.

## Package identity

The application version has one source in `pyproject.toml`. The matching entry in
`uv.lock`, wheel metadata, source distribution metadata, release tag, and `roi-h --version`
output must use the same version. The installer is a separate package and can use its own
version.

Each application release produces one wheel and one source distribution:

```text
roi_h-<version>-py3-none-any.whl
roi_h-<version>.tar.gz
```

The release qualification also builds and checks the installer distribution. User projects,
artifacts, browser state, databases, secrets, and custom automations are not release inputs
and must not be tracked in this repository.

## Publication boundary

The repository contains generic product code, generic documentation, tests, release tools,
and the supported public skills: `browser`, `codex_chrome`, `excel`, `files`, and `pdf`.
The publication guard and packaging tests enforce this boundary. Customer data and project
state belong in the ROI-H data home.

## Local qualification

Run the package qualification from the repository root:

```shell
uv run python scripts/qualify_release.py
```

The command checks the publication boundary, locked Python environment, focused package
tests, fresh build artifacts, package identity, and Twine metadata. It must pass before a
tag or release is published.

Use `uv run python scripts/qualify_release.py --full` when complete installer and
application qualification is required.

## GitHub release

After qualification passes:

1. Confirm that the worktree contains only the intended release changes.
2. Commit the version and release changes.
3. Create an annotated tag named `v<version>`.
4. Push the release commit and tag to `origin`.
5. Create an immutable GitHub Release for the tag and attach the qualified artifacts.

Use the repository's authenticated GitHub account for publication. Do not put package,
registry, or GitHub credentials in files, Git arguments, release notes, or command output.
Do not replace a qualified artifact after it is attached to a release.

## Installation and updates

GitHub Releases host immutable application artifacts. Installers must verify the selected
artifact before activation and must keep the previous healthy version available for
rollback. Updating the application must not delete or modify user projects, automation
packages, run history, skills, secrets, or other data-home contents.
