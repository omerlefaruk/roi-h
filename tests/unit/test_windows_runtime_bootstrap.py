from __future__ import annotations

import os

import pytest

from roi_h.harness.runtime_environment import (
    inspect_isolated_runtime_bootstrap,
    isolated_process_environment,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows runtime regression")
def test_isolated_windows_environment_bootstraps_socket_and_tls() -> None:
    environment = isolated_process_environment()
    normalized_names = {name.upper() for name in environment}

    assert {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE"} <= normalized_names

    report = inspect_isolated_runtime_bootstrap()

    assert report.healthy is True
    assert {check.code: check.ok for check in report.checks} == {
        "runtime.socket_bootstrap": True,
        "runtime.tls_bootstrap": True,
    }


def test_isolated_environment_matches_names_without_case_sensitivity() -> None:
    environment = isolated_process_environment(
        {
            "Path": "bin",
            "SystemRoot": r"C:\Windows",
            "not_allowed": "secret",
        }
    )

    assert environment["Path"] == "bin"
    if os.name == "nt":
        assert environment["SystemRoot"] == r"C:\Windows"
    assert "not_allowed" not in environment
