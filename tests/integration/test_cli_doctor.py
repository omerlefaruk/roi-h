"""Public CLI tests for read-only installation health."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_doctor_reports_json_without_creating_user_paths(tmp_path: Path) -> None:
    install_root = tmp_path / "managed-install"
    data_home = tmp_path / "data-home"
    environment = dict(os.environ)
    environment["ROI_H_INSTALL_ROOT"] = str(install_root)
    environment["ROI_H_HOME"] = str(data_home)

    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", "doctor", "--output", "json"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["healthy"] is True
    assert report["install_root"] == str(install_root)
    assert report["data_home"] == str(data_home)
    assert report["managed_install_state"] == "unmanaged"
    assert report["python_compatible"] is True
    assert report["built_in_skills"] == {
        "browser": True,
        "excel": True,
        "files": True,
        "pdf": True,
    }
    assert {check["code"] for check in report["checks"]} == {
        "application.version",
        "browser.launch",
        "data_home.access",
        "install.managed_state",
        "python.version",
        "runtime.socket_bootstrap",
        "runtime.tls_bootstrap",
        "skills.built_in",
    }
    assert completed.stderr == ""
    assert not install_root.exists()
    assert not data_home.exists()
