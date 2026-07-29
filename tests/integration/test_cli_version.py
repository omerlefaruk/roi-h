"""Public CLI tests for the installed application identity."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _project_version() -> str:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    return str(project["version"])


def test_version_flag_reports_the_project_version(tmp_path: Path) -> None:
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", "--version"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == f"roi-h {_project_version()}\n"
    assert completed.stderr == ""


def test_version_command_reports_json_identity(tmp_path: Path) -> None:
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", "version", "--output", "json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "name": "roi-h",
        "version": _project_version(),
    }
    assert completed.stderr == ""
