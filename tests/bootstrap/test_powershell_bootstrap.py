from pathlib import Path


def test_powershell_bootstrap_matches_the_pinned_install_contract() -> None:
    script = (Path(__file__).parents[2] / "install.ps1").read_text(encoding="utf-8")

    assert '$uvVersion = "0.11.16"' in script
    assert (
        '$uvInstallerSha256 = "a885d46d3105506fdabc1febd2673313968605c8434e17e5841750cb20b28989"'
        in script
    )
    assert '$pythonVersion = "3.12.13"' in script
    assert '$defaultInstallerVersion = "0.1.2"' in script
    assert "roi-h-release-windows-x86_64-0.1.2.tar.gz" in script
    assert (
        "$defaultReleaseBundleSha256 = "
        '"c62c069b44a538d06357bd6ffc4eca5eec6386b0d4395ab02850afccbca50896"' in script
    )
    assert "$env:ROI_H_INSTALLER_VERSION" in script
    assert "$env:ROI_H_RELEASE_BUNDLE_URL" in script
    assert "$env:ROI_H_RELEASE_BUNDLE_SHA256" in script
    assert '"This release supports Windows x86-64 only."' in script
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
    assert '"installer\\update.ps1"' in script
    assert '"https://raw.githubusercontent.com/omerlefaruk/roi-h/main/install.ps1"' in script
    assert '[Environment]::SetEnvironmentVariable("Path"' in script
def test_powershell_bootstrap_disables_strict_mode_for_uv() -> None:
    script = (Path(__file__).parents[2] / "install.ps1").read_text(encoding="utf-8")

    assert "Set-StrictMode -Off" in script
    assert "& $uvInstallerScript" in script
