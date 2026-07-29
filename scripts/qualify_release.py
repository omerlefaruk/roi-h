"""Run the complete ROI-H release qualification on the maintainer's machine."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12"
EXPECTED_ARTIFACT_COUNT = 2


def _run(command: list[str]) -> None:
    """Run one visible qualification command from the repository root."""
    rendered = " ".join(command)
    sys.stdout.write(f"\n==> {rendered}\n")
    sys.stdout.flush()
    subprocess.run(command, cwd=REPOSITORY, check=True)  # noqa: S603


def _uv_run(python_version: str, *command: str) -> None:
    """Run a command in the already-synchronized environment for one Python."""
    _run(["uv", "run", "--python", python_version, "--no-sync", *command])


def _distribution_artifacts() -> list[str]:
    """Return the newly built wheel and source distribution."""
    distribution_directory = REPOSITORY / "dist"
    artifacts = sorted(
        path
        for path in distribution_directory.iterdir()
        if path.suffix == ".whl" or path.name.endswith(".tar.gz")
    )
    if len(artifacts) != EXPECTED_ARTIFACT_COUNT:
        names = ", ".join(path.name for path in artifacts) or "none"
        message = f"Expected one wheel and one source distribution; found: {names}"
        raise RuntimeError(message)
    return [str(path) for path in artifacts]


def main() -> int:
    """Qualify the supported Python runtime and build release artifacts once."""
    _run([sys.executable, "scripts/check_publication_boundary.py"])
    _run(["uv", "sync", "--locked", "--python", PYTHON_VERSION, "--group", "dev"])
    _uv_run(
        PYTHON_VERSION,
        "python",
        "-m",
        "compileall",
        "-q",
        "src",
        "skills",
        "scripts",
    )
    _uv_run(PYTHON_VERSION, "ruff", "check", ".")
    _uv_run(PYTHON_VERSION, "ruff", "format", "--check", ".")
    _uv_run(PYTHON_VERSION, "mypy")
    _uv_run(PYTHON_VERSION, "python", "-m", "pytest")
    _run(["uv", "build", "--python", PYTHON_VERSION, "--clear", "--no-sources"])
    _uv_run(PYTHON_VERSION, "python", "-m", "twine", "check", *_distribution_artifacts())
    sys.stdout.write("\nROI-H release qualification: PASSED\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
