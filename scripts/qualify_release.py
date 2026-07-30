"""Run the complete ROI-H release qualification on the maintainer's machine."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
INSTALLER_PROJECT = REPOSITORY / "packages" / "roi-h-installer"
INSTALLER_DISTRIBUTION = REPOSITORY / "dist" / "installer"
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


def _distribution_artifacts(
    distribution_directory: Path = REPOSITORY / "dist",
) -> list[str]:
    """Return one newly built wheel and source distribution."""
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
    shell = shutil.which("sh")
    if shell is None:
        sys.stdout.write("\n==> sh -n install.sh (skipped: sh is not installed)\n")
    else:
        _run([shell, "-n", "install.sh"])
    _run(["uv", "sync", "--locked", "--python", PYTHON_VERSION, "--group", "dev"])
    _run(
        [
            "uv",
            "sync",
            "--directory",
            str(INSTALLER_PROJECT),
            "--locked",
            "--python",
            PYTHON_VERSION,
            "--group",
            "dev",
        ],
    )
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
    for command in (
        ("ruff", "check", "."),
        ("ruff", "format", "--check", "."),
        ("mypy",),
        ("python", "-m", "pytest"),
    ):
        _uv_run(PYTHON_VERSION, *command)
    installer_prefix = [
        "uv",
        "run",
        "--directory",
        str(INSTALLER_PROJECT),
        "--python",
        PYTHON_VERSION,
        "--no-sync",
    ]
    for command in (
        ("ruff", "check", "."),
        ("ruff", "format", "--check", "."),
        ("mypy",),
        ("python", "-m", "pytest"),
    ):
        _run([*installer_prefix, *command])
    _run(["uv", "build", "--python", PYTHON_VERSION, "--clear", "--no-sources"])
    _run(
        [
            "uv",
            "build",
            "--directory",
            str(INSTALLER_PROJECT),
            "--python",
            PYTHON_VERSION,
            "--clear",
            "--no-sources",
            "--out-dir",
            str(INSTALLER_DISTRIBUTION),
        ],
    )
    for project, distribution in (
        ("pyproject.toml", _distribution_artifacts()),
        (
            str(INSTALLER_PROJECT / "pyproject.toml"),
            _distribution_artifacts(INSTALLER_DISTRIBUTION),
        ),
    ):
        _run(
            [
                sys.executable,
                "scripts/check_release_identity.py",
                "--project",
                project,
                *[item for artifact in distribution for item in ("--artifact", artifact)],
            ],
        )
    for distribution in (
        _distribution_artifacts(),
        _distribution_artifacts(INSTALLER_DISTRIBUTION),
    ):
        _uv_run(PYTHON_VERSION, "python", "-m", "twine", "check", *distribution)
    sys.stdout.write("\nROI-H release qualification: PASSED\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
