"""Release qualification command options."""

from __future__ import annotations

import runpy
from pathlib import Path

QUALIFIER = Path(__file__).resolve().parents[2] / "scripts" / "qualify_release.py"


def test_release_qualification_defaults_to_package_checks() -> None:
    parser = runpy.run_path(str(QUALIFIER))["_parser"]()

    assert parser.parse_args([]).full is False
    assert parser.parse_args(["--full"]).full is True
