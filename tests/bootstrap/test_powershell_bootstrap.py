from pathlib import Path


def test_powershell_bootstrap_matches_the_pinned_install_contract() -> None:
    script = (Path(__file__).parents[2] / "install.ps1").read_text(encoding="utf-8")

    assert '$uvVersion = "0.11.16"' in script
    assert (
        '$uvInstallerSha256 = "a885d46d3105506fdabc1febd2673313968605c8434e17e5841750cb20b28989"'
        in script
    )
    assert '$pythonVersion = "3.12.13"' in script
    assert '$defaultInstallerVersion = "0.1.0"' in script
    assert (
        "$defaultReleaseBundleSha256 = "
        '"92c66971acda05937764c35023e494de82172e38fb0bc733a7666f7bd1be0660"' in script
    )
    assert "$env:ROI_H_INSTALLER_VERSION" in script
    assert "$env:ROI_H_RELEASE_BUNDLE_URL" in script
    assert "$env:ROI_H_RELEASE_BUNDLE_SHA256" in script
    assert "https://astral.sh/uv/$uvVersion/install.ps1" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "System.IO.Compression.ZipFile" not in script
    assert "release.json" in script
    assert "*.whl" in script
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
    assert '"current\\Scripts\\roi-h.exe"' in script
    assert '"roi-h.cmd"' in script
    assert '"installer\\update.ps1"' in script
    assert '"https://get.roi-h.dev/windows"' in script
    assert '[Environment]::SetEnvironmentVariable("Path"' in script
