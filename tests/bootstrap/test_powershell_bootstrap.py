import os
import subprocess
from pathlib import Path

import pytest


def test_powershell_bootstrap_matches_the_pinned_install_contract() -> None:
    script = (Path(__file__).parents[2] / "install.ps1").read_text(encoding="utf-8")

    assert '$uvVersion = "0.11.16"' in script
    assert (
        '$uvInstallerSha256 = "a885d46d3105506fdabc1febd2673313968605c8434e17e5841750cb20b28989"'
        in script
    )
    assert '$pythonVersion = "3.12.13"' in script
    assert '$defaultInstallerVersion = "0.1.4"' in script
    assert "roi-h-release-windows-x86_64-0.1.8.tar.gz" in script
    assert (
        "$defaultReleaseBundleSha256 = "
        '"ff952335326f23a349ff2450e3dade1a4d795dd68ee9dc8e9ca4dac1ac8a922a"' in script
    )
    assert "$env:ROI_H_INSTALLER_VERSION" in script
    assert "$env:ROI_H_RELEASE_BUNDLE_URL" in script
    assert "$env:ROI_H_RELEASE_BUNDLE_SHA256" in script
    assert '"This release supports Windows x86-64 only."' in script
    assert "WindowsPrincipal" in script
    assert "WindowsBuiltInRole]::Administrator" in script
    assert '"Do not run the user installer as Administrator.' in script
    assert 'Join-Path $env:USERPROFILE ".roi-h"' in script
    assert "function Assert-UserWritablePath" in script
    assert "Assert-UserWritablePath -Path $installRoot" in script
    assert "Assert-UserWritablePath -Path $dataHome" in script
    assert "[Runtime.InteropServices.RuntimeInformation]::OSArchitecture" not in script
    assert '"PROCESSOR_ARCHITECTURE"' in script
    assert '"PROCESSOR_ARCHITEW6432"' in script
    assert "https://astral.sh/uv/$uvVersion/install.ps1" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "[ScriptBlock]::Create(" in script
    assert "& $uvInstallerPath" not in script
    assert "System.IO.Compression.ZipFile" not in script
    assert "release.json" in script
    assert "*.whl" in script
    assert "@($members | Group-Object | Where-Object Count -gt 1).Length" in script
    assert '@($members | Where-Object { $_ -eq "release.json" }).Length' in script
    assert '@($members | Where-Object { $_ -like "*.whl" }).Length' in script
    assert ".Count" not in script
    assert '"roi-h-installer==$installerVersion"' in script
    assert "--no-index" in script
    assert "--find-links $releaseRoot" in script
    assert "$installerOperation = if (" in script
    assert '"install-state.json"' in script
    assert "--release-description" in script
    assert "--install-root" in script
    assert "--data-home" in script
    assert "--output json" in script
    assert "$env:ROI_H_HOME =" not in script
    assert '"versions\\" + $installState.active_version' in script
    assert 'set /p "ROI_H_ACTIVE_VERSION="<"%ROI_H_INSTALL_ROOT%\\current"' in script
    assert '"roi-h.cmd"' in script
    assert 'set "PLAYWRIGHT_BROWSERS_PATH=%ROI_H_INSTALL_ROOT%\\browsers"' in script
    assert 'set "PLAYWRIGHT_SKIP_BROWSER_GC=1"' in script
    assert '"installer\\update.ps1"' in script
    assert '"https://raw.githubusercontent.com/omerlefaruk/roi-h/main/install.ps1"' in script
    assert '[Environment]::SetEnvironmentVariable("Path"' in script


def test_powershell_bootstrap_disables_strict_mode_for_uv() -> None:
    script = (Path(__file__).parents[2] / "install.ps1").read_text(encoding="utf-8")

    assert "Set-StrictMode -Off" in script
    assert "& $uvInstallerScript" in script


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell is only available on Windows")
def test_powershell_bootstrap_runs_in_windows_powershell() -> None:
    script_path = Path(__file__).parents[2] / "install.ps1"
    windows_powershell = (
        Path(os.environ["SYSTEMROOT"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    environment = {
        **os.environ,
        "ROI_H_RELEASE_BUNDLE_URL": "not-a-url",
    }
    environment.pop("ROI_H_INSTALLER_VERSION", None)
    environment.pop("ROI_H_RELEASE_BUNDLE_SHA256", None)

    completed = subprocess.run(  # noqa: S603 - fixed Windows PowerShell executable
        [
            str(windows_powershell),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script_path),
        ],
        cwd=script_path.parent,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "ROI_H_RELEASE_BUNDLE_URL must use HTTPS." in output
    assert "OSArchitecture" not in output
