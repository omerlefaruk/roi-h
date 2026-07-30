# Windows MSIX prototype

**Status:** Build and static checks implemented. Clean Windows 11 evidence is still required.

This prototype packages one x64 CPython runtime, ROI-H, its locked dependencies, a native
`roi-h.exe` launcher, the full-trust execution alias, and signed browser archive metadata
in one MSIX. Chromium stays at `%LOCALAPPDATA%\ROI-H\Browsers`. `ROI_H_HOME` stays outside
the package and browser store.

Run this from a Windows 11 x64 Visual Studio Developer PowerShell with `uv`, MSVC, and the
Windows SDK on `PATH`:

```powershell
pwsh scripts/build_windows_browser_archive.ps1 -Output dist/windows/chromium-1228.zip
$a = pwsh scripts/build_windows_msix_prototype.ps1 `
  -Version 0.1.3 `
  -BrowserArchive dist/windows/chromium-1228.zip `
  -OutputDirectory dist/windows/a `
  -CreateTestCertificate | Select-Object -Last 1 | ConvertFrom-Json
$bad = pwsh scripts/build_windows_msix_prototype.ps1 `
  -Version 0.1.4 `
  -BrowserArchive dist/windows/chromium-1228.zip `
  -OutputDirectory dist/windows/bad `
  -StateBrowserRevision chromium-failure-injection `
  -CertificateThumbprint $a.certificate_thumbprint | Select-Object -Last 1 | ConvertFrom-Json
$b = pwsh scripts/build_windows_msix_prototype.ps1 `
  -Version 0.1.5 `
  -BrowserArchive dist/windows/chromium-1228.zip `
  -OutputDirectory dist/windows/b `
  -CertificateThumbprint $a.certificate_thumbprint | Select-Object -Last 1 | ConvertFrom-Json
pwsh scripts/prove_windows_msix_journey.ps1 `
  -PackageA $a.package `
  -PackageBadB $bad.package `
  -PackageB $b.package `
  -BrowserArchive dist/windows/chromium-1228.zip `
  -Certificate $a.certificate
```

The journey verifies the trusted test signature, execution alias, installed CLI,
subprocess skill, digest-bound browser extraction, real browser launch, interrupted
acquisition cleanup, update, restore after a failed doctor, local offline downgrade,
package uninstall, scripted managed-browser cleanup, and an unchanged customer-home digest.

The test certificate proves MSIX mechanics only. It does not prove public certificate
trust, timestamping, SmartScreen, App Installer URI behavior, Settings uninstall, or the
minimum supported clean Windows 11 build. MSIX activates an update before ROI-H doctor
runs, so the prototype restores the retained package after a failed post-activation
doctor. Final issue #15 acceptance needs the same script on clean Windows 11 with the
release certificate, timestamp service, and hosted `.appinstaller` URI.
