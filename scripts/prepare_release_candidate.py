"""Build a locked platform-specific ROI-H release candidate locally."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from build_release_bundle import ReleaseBundleRequest, build_release_bundle

_PIP_VERSION = "26.0.1"


class CandidateError(RuntimeError):
    """A stable candidate preparation failure."""

    def __init__(self, code: str, message: str) -> None:
        """Store a machine-readable code and operator message."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CandidateRequest:
    """All inputs required for one platform release candidate."""

    repository: Path
    output_dir: Path
    version: str
    installer_version: str
    python_version: str
    browser_revision: str
    activegraph_version: str
    channel: str


def _run(command: list[str], repository: Path) -> None:
    subprocess.run(command, cwd=repository, check=True)  # noqa: S603


def prepare_candidate(request: CandidateRequest) -> dict[str, object]:
    """Build a wheelhouse, hashed lock, and deterministic release bundle."""
    if request.output_dir.exists():
        code = "release.output_exists"
        raise CandidateError(
            code,
            f"Candidate output already exists: {request.output_dir}",
        )
    if not (request.repository / "pyproject.toml").is_file():
        code = "release.repository_invalid"
        raise CandidateError(
            code,
            f"ROI-H repository not found: {request.repository}",
        )

    wheelhouse = request.output_dir / "wheelhouse"
    runtime_lock = request.output_dir / "runtime.lock"
    bundle = request.output_dir / f"roi-h-release-{request.version}.tar.gz"
    wheelhouse.mkdir(parents=True)
    try:
        _run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--no-annotate",
                "--no-header",
                "--output-file",
                str(runtime_lock),
            ],
            request.repository,
        )
        _run(
            [
                "uv",
                "build",
                "--directory",
                str(request.repository / "packages" / "roi-h-installer"),
                "--wheel",
                "--no-sources",
                "--out-dir",
                str(wheelhouse),
            ],
            request.repository,
        )
        _run(
            [
                "uv",
                "build",
                "--wheel",
                "--no-sources",
                "--out-dir",
                str(wheelhouse),
            ],
            request.repository,
        )
        _run(
            [
                "uvx",
                "--python",
                request.python_version,
                "--from",
                f"pip=={_PIP_VERSION}",
                "pip",
                "download",
                "--require-hashes",
                "--only-binary=:all:",
                "--dest",
                str(wheelhouse),
                "--requirement",
                str(runtime_lock),
            ],
            request.repository,
        )
        return build_release_bundle(
            ReleaseBundleRequest(
                wheelhouse=wheelhouse,
                output=bundle,
                version=request.version,
                installer_version=request.installer_version,
                python_version=request.python_version,
                browser_revision=request.browser_revision,
                activegraph_version=request.activegraph_version,
                channel=request.channel,
            )
        )
    except Exception:
        shutil.rmtree(request.output_dir, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--installer-version", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--browser-revision", required=True)
    parser.add_argument("--activegraph-version", required=True)
    parser.add_argument("--channel", required=True)
    return parser


def main() -> int:
    """Run local release candidate preparation."""
    args = _parser().parse_args()
    try:
        result = prepare_candidate(
            CandidateRequest(
                repository=args.repository.expanduser().resolve(),
                output_dir=args.output_dir.expanduser().resolve(),
                version=args.version,
                installer_version=args.installer_version,
                python_version=args.python_version,
                browser_revision=args.browser_revision,
                activegraph_version=args.activegraph_version,
                channel=args.channel,
            )
        )
    except (CandidateError, OSError, subprocess.CalledProcessError) as exc:
        code = getattr(exc, "code", "release.candidate_failed")
        sys.stderr.write(json.dumps({"code": code, "message": str(exc)}, sort_keys=True) + "\n")
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
