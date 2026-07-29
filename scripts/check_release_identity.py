"""Verify that one release build has one package name and version."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

_EXPECTED_ARTIFACT_COUNT = 2
_INVALID_ARTIFACT_METADATA = "release.invalid_artifact_metadata"
_INVALID_ARTIFACT_SET = "release.invalid_artifact_set"
_INVALID_PROJECT_METADATA = "release.invalid_project_metadata"
_VERSION_MISMATCH = "release.version_mismatch"


class ReleaseIdentityError(RuntimeError):
    """A stable release identity check failure."""

    def __init__(self, code: str, message: str) -> None:
        """Store a machine-readable code with the operator message."""
        super().__init__(message)
        self.code = code


def _project_identity(pyproject: Path) -> tuple[str, str]:
    with pyproject.open("rb") as stream:
        project = tomllib.load(stream).get("project", {})
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise ReleaseIdentityError(
            _INVALID_PROJECT_METADATA,
            f"{pyproject} must contain string project.name and project.version values",
        )
    return name, version


def _metadata_identity(text: str, source: Path) -> tuple[str, str]:
    metadata = Parser().parsestr(text)
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise ReleaseIdentityError(
            _INVALID_ARTIFACT_METADATA,
            f"{source.name} has no Name or Version package metadata",
        )
    return name, version


def _wheel_identity(artifact: Path) -> tuple[str, str]:
    with zipfile.ZipFile(artifact) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA") and name.count("/") == 1
        ]
        if len(metadata_names) != 1:
            raise ReleaseIdentityError(
                _INVALID_ARTIFACT_METADATA,
                f"{artifact.name} must contain one top-level .dist-info/METADATA file",
            )
        text = archive.read(metadata_names[0]).decode("utf-8")
    return _metadata_identity(text, artifact)


def _sdist_identity(artifact: Path) -> tuple[str, str]:
    with tarfile.open(artifact, "r:gz") as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.name.endswith("/PKG-INFO") and member.name.count("/") == 1
        ]
        if len(members) != 1:
            raise ReleaseIdentityError(
                _INVALID_ARTIFACT_METADATA,
                f"{artifact.name} must contain one top-level PKG-INFO file",
            )
        stream = archive.extractfile(members[0])
        if stream is None:
            raise ReleaseIdentityError(
                _INVALID_ARTIFACT_METADATA,
                f"{artifact.name} PKG-INFO is not readable",
            )
        text = stream.read().decode("utf-8")
    return _metadata_identity(text, artifact)


def verify_release_identity(
    pyproject: Path,
    artifacts: list[Path],
) -> dict[str, object]:
    """Return the verified identity of one wheel and one source archive."""
    wheel = [path for path in artifacts if path.suffix == ".whl"]
    sdist = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(wheel) != 1 or len(sdist) != 1 or len(artifacts) != _EXPECTED_ARTIFACT_COUNT:
        raise ReleaseIdentityError(
            _INVALID_ARTIFACT_SET,
            "Expected exactly one wheel and one .tar.gz source archive",
        )

    expected = _project_identity(pyproject)
    normalized_name = re.sub(r"[-_.]+", "_", expected[0]).lower()
    expected_wheel_prefix = f"{normalized_name}-{expected[1]}-"
    expected_sdist_name = f"{normalized_name}-{expected[1]}.tar.gz"
    if not wheel[0].name.startswith(expected_wheel_prefix):
        raise ReleaseIdentityError(
            _VERSION_MISMATCH,
            f"{wheel[0].name} does not identify {expected[0]} {expected[1]}",
        )
    if sdist[0].name != expected_sdist_name:
        raise ReleaseIdentityError(
            _VERSION_MISMATCH,
            f"{sdist[0].name} does not identify {expected[0]} {expected[1]}",
        )
    observed = {
        wheel[0].name: _wheel_identity(wheel[0]),
        sdist[0].name: _sdist_identity(sdist[0]),
    }
    mismatches = {name: identity for name, identity in observed.items() if identity != expected}
    if mismatches:
        details = ", ".join(
            f"{name} reports {identity[0]} {identity[1]}" for name, identity in mismatches.items()
        )
        raise ReleaseIdentityError(
            _VERSION_MISMATCH,
            f"Expected {expected[0]} {expected[1]}; {details}",
        )
    return {
        "artifacts": [path.name for path in artifacts],
        "name": expected[0],
        "ok": True,
        "version": expected[1],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    return parser


def main() -> int:
    """Run the release identity gate."""
    args = _parser().parse_args()
    try:
        result = verify_release_identity(args.project, args.artifact)
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ReleaseIdentityError) as exc:
        code = getattr(exc, "code", "release.artifact_read_failed")
        sys.stderr.write(json.dumps({"code": code, "message": str(exc)}, sort_keys=True) + "\n")
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
