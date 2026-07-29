"""Installed update command tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX updater helper test")
def test_update_hands_off_to_the_external_installer_helper(tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "home"
    helper = install_root / "installer" / "update.sh"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        """#!/bin/sh
set -eu
[ "$ROI_H_INSTALL_ROOT" = "$EXPECTED_INSTALL_ROOT" ]
[ "$ROI_H_HOME" = "$EXPECTED_DATA_HOME" ]
printf '{"changed":true,"installed_version":"0.2.0"}\\n'
""",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    environment = {
        **os.environ,
        "EXPECTED_INSTALL_ROOT": str(install_root),
        "EXPECTED_DATA_HOME": str(data_home),
    }

    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "roi_h",
            "update",
            "--install-root",
            str(install_root),
            "--home",
            str(data_home),
            "--output",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "changed": True,
        "installed_version": "0.2.0",
    }


def test_update_reports_a_missing_external_installer_helper(tmp_path: Path) -> None:
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "roi_h",
            "update",
            "--install-root",
            str(tmp_path / "missing"),
            "--home",
            str(tmp_path / "home"),
            "--output",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert "Run the one-line installer again" in payload["error"]
