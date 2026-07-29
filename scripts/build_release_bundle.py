"""Build one immutable local ROI-H release bundle from a complete wheelhouse."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import tarfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

_APPLICATION_NAME = "roi-h"
_INSTALLER_NAME = "roi-h-installer"
_SCHEMA_VERSION = "1.0"


class ReleaseBundleError(RuntimeError):
    """A stable release bundle failure."""

    def __init__(self, code: str, message: str) -> None:
        """Store a machine-readable code and an operator message."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReleaseBundleRequest:
    """All inputs required to create one immutable bundle."""

    wheelhouse: Path
    output: Path
    version: str
    installer_version: str
    python_version: str
    browser_revision: str
    activegraph_version: str
    channel: str


def _target(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "name": path.name,
        "length": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _tar_info(name: str, length: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = length
    info.mode = 0o644
    info.mtime = 0
    return info


def _write_bundle(
    output: Path,
    release: dict[str, object],
    wheels: list[Path],
) -> None:
    release_content = (json.dumps(release, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        output.open("xb") as raw_stream,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, mtime=0) as gzip_stream,
        tarfile.open(mode="w", fileobj=gzip_stream) as archive,
    ):
        archive.addfile(
            _tar_info("release.json", len(release_content)),
            BytesIO(release_content),
        )
        for wheel in wheels:
            content = wheel.read_bytes()
            archive.addfile(_tar_info(wheel.name, len(content)), BytesIO(content))


def build_release_bundle(request: ReleaseBundleRequest) -> dict[str, object]:
    """Build and return the identity of one self-contained release bundle."""
    if request.output.exists():
        code = "release.output_exists"
        raise ReleaseBundleError(code, f"Release output already exists: {request.output}")
    if not request.wheelhouse.is_dir():
        code = "release.wheelhouse_not_found"
        raise ReleaseBundleError(
            code,
            f"Wheelhouse does not exist: {request.wheelhouse}",
        )

    wheels = sorted(request.wheelhouse.glob("*.whl"), key=lambda path: path.name)
    if not wheels or any(path.is_symlink() or not path.is_file() for path in wheels):
        code = "release.invalid_wheelhouse"
        raise ReleaseBundleError(code, "The wheelhouse must contain only regular wheel files.")

    normalized_name = re.sub(r"[-_.]+", "_", _APPLICATION_NAME)
    application_prefix = f"{normalized_name}-{request.version}-"
    application_wheels = [path for path in wheels if path.name.startswith(application_prefix)]
    if len(application_wheels) != 1:
        code = "release.version_not_found"
        message = f"The wheelhouse must have one {_APPLICATION_NAME} {request.version} wheel."
        raise ReleaseBundleError(code, message)

    normalized_installer_name = re.sub(r"[-_.]+", "_", _INSTALLER_NAME)
    installer_prefix = f"{normalized_installer_name}-{request.installer_version}-"
    installer_wheels = [path for path in wheels if path.name.startswith(installer_prefix)]
    if len(installer_wheels) != 1:
        code = "release.installer_version_not_found"
        message = (
            f"The wheelhouse must have one {_INSTALLER_NAME} {request.installer_version} wheel."
        )
        raise ReleaseBundleError(code, message)

    targets = [_target(path) for path in wheels]
    release: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "version": request.version,
        "channel": request.channel,
        "application_target": application_wheels[0].name,
        "installer_target": installer_wheels[0].name,
        "installer_version": request.installer_version,
        "python_version": request.python_version,
        "browser_revision": request.browser_revision,
        "data_compatibility": {
            "readable_home_layouts": [1],
            "writable_home_layout": 1,
            "activegraph_version": request.activegraph_version,
        },
        "targets": targets,
    }
    _write_bundle(request.output, release, wheels)
    return {
        "bundle": str(request.output),
        "channel": request.channel,
        "sha256": hashlib.sha256(request.output.read_bytes()).hexdigest(),
        "target_count": len(targets),
        "version": request.version,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--installer-version", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--browser-revision", required=True)
    parser.add_argument("--activegraph-version", required=True)
    parser.add_argument("--channel", required=True)
    return parser


def main() -> int:
    """Run the release bundle builder."""
    args = _parser().parse_args()
    try:
        result = build_release_bundle(
            ReleaseBundleRequest(
                wheelhouse=args.wheelhouse,
                output=args.output,
                version=args.version,
                installer_version=args.installer_version,
                python_version=args.python_version,
                browser_revision=args.browser_revision,
                activegraph_version=args.activegraph_version,
                channel=args.channel,
            )
        )
    except (OSError, ReleaseBundleError) as exc:
        code = getattr(exc, "code", "release.bundle_write_failed")
        sys.stderr.write(json.dumps({"code": code, "message": str(exc)}, sort_keys=True) + "\n")
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
