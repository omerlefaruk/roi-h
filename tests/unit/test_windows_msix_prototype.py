from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_FOUNDATION = "http://schemas.microsoft.com/appx/manifest/foundation/windows10"
_UAP5 = "http://schemas.microsoft.com/appx/manifest/uap/windows10/5"
_RESCAP = "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"


def test_windows_msix_prototype_keeps_one_native_cli_and_browser_path() -> None:
    manifest = ET.parse(  # noqa: S314 - parses a trusted repository template.
        _ROOT / "packaging/windows/AppxManifest.xml.in"
    ).getroot()
    identity = manifest.find(f"{{{_FOUNDATION}}}Identity")
    application = manifest.find(f".//{{{_FOUNDATION}}}Application")
    alias = manifest.find(f".//{{{_UAP5}}}ExecutionAlias")
    capability = manifest.find(f".//{{{_RESCAP}}}Capability")

    assert identity is not None
    assert identity.attrib["ProcessorArchitecture"] == "x64"
    assert application is not None
    assert application.attrib == {
        "Id": "ROI-H",
        "Executable": "roi-h.exe",
        "EntryPoint": "Windows.FullTrustApplication",
    }
    assert alias is not None and alias.attrib["Alias"] == "roi-h.exe"
    assert capability is not None and capability.attrib["Name"] == "runFullTrust"

    launcher = (_ROOT / "packaging/windows/launcher.c").read_text(encoding="utf-8")
    assert "_wspawnv" in launcher
    assert 'L"PLAYWRIGHT_BROWSERS_PATH"' in launcher
    assert 'L"%ls\\\\ROI-H\\\\Browsers"' in launcher

    build = (_ROOT / "scripts/build_windows_msix_prototype.ps1").read_text(encoding="utf-8")
    proof = (_ROOT / "scripts/prove_windows_msix_journey.ps1").read_text(encoding="utf-8")
    assert all(
        command in build for command in ("MakeAppx.exe", "SignTool.exe", "browser-target.json")
    )
    assert all(
        evidence in proof
        for evidence in (
            "Get-AuthenticodeSignature",
            "ForceUpdateFromAnyVersion",
            "failed_doctor_restore",
            "offline_downgrade",
            "customer_home_sha256",
        )
    )
