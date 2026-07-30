# macOS and Windows install and update mechanism

**Ticket:** [#9](https://github.com/omerlefaruk/roi-h/issues/9)  
**Decision:** Replace the public shell/PowerShell + `uv` + Python-installer chain with one native signed artifact for each OS: a notarized macOS distribution package (`.pkg`) and a signed Windows MSIX package installed from an `.appinstaller` file. Keep Python wheels only as internal build inputs. Do not make PyPI, OCI, WinGet, or a second update repository part of the customer path.

## Retained mechanism

Build the locked ROI-H application, CPython, dependencies, CLI launcher, and generic skills into each OS artifact. Keep customer data outside the installed application:

- macOS application and managed browsers: `~/Library/Application Support/ROI-H/`; customer data: `ROI_H_HOME` or `~/.roi-h`.
- Windows application: the MSIX package; managed browsers: `%LOCALAPPDATA%\ROI-H\Browsers`; customer data: `ROI_H_HOME` or `%USERPROFILE%\.roi-h`.

This keeps the current `resolve_home` contract and makes OS package servicing the only application delivery system. A macOS distribution package can enable only the current-user home domain. Apple states that this mode runs as the current user and cannot write outside that home, so it does not need a root installation path. [Apple Distribution XML reference](https://developer.apple.com/library/archive/documentation/DeveloperTools/Reference/DistributionDefinitionRef/Chapters/Distribution_XML_Ref.html)

Windows MSIX is the native fit for a CLI: Microsoft documents packaging a command-line executable and exposing it through an app execution alias. [Microsoft CLI packaging guide](https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/guides/packaging-cli) App Installer supplies non-Store update and repair settings on Windows 10 2004 and later, including Windows 11. [Microsoft App Installer update and repair](https://learn.microsoft.com/en-us/windows/msix/app-installer/auto-update-and-repair--overview)

## Exact flow

### Install

1. Release qualification creates one macOS `.pkg` and one Windows `.msix` for each supported architecture. The payload already contains the exact CPython and locked ROI-H runtime. It does not resolve packages on the customer computer.
2. The customer downloads the OS artifact from the ROI-H release site. Windows can use a small `.appinstaller` descriptor, but the signed MSIX is the one Windows application installer.
3. macOS Installer verifies the package, installs the versioned payload in the current-user domain, runs the staged `roi-h doctor`, and changes the stable launcher pointer only after success. Windows App Installer verifies and registers the MSIX and its `roi-h.exe` execution alias.
4. The stable launcher runs the browser acquisition check. It then runs `doctor`. Install is complete only when the application and browser checks pass.

### Update

- **macOS:** `roi-h update` downloads the exact next notarized `.pkg` to a temporary file, checks its Developer ID signature, and passes it to macOS Installer. The package writes the new payload beside the active payload. Its script gets the required browser, runs staged `doctor`, and atomically changes the launcher pointer. A failure leaves the old pointer active.
- **Windows:** `roi-h update` hands the `.appinstaller` URI to App Installer. Keep automatic background installation off unless product policy changes. App Installer downloads and validates the newer signed MSIX and Windows services the package. The update command then runs `doctor`. Keep the prior signed MSIX in the application cache until the new version passes. Windows supports update settings outside the Store and package integrity enforcement for signed MSIX packages. [Microsoft App Installer update and repair](https://learn.microsoft.com/en-us/windows/msix/app-installer/auto-update-and-repair--overview) [Microsoft MSIX signing](https://learn.microsoft.com/en-us/windows/msix/package/signing-package-overview)

### Rollback

- **macOS:** if the prior version is retained, `roi-h rollback` runs its doctor and atomically restores the prior launcher pointer. If it is not retained, download its notarized `.pkg`, install it, run doctor, and then switch. The rollback package must explicitly support downgrade; Apple documents that downgradability is set by the package that performs the downgrade. [Apple packaging workflow](https://developer.apple.com/library/archive/documentation/DeveloperTools/Conceptual/PackageMakerUserGuide/Workflow/Workflow.html)
- **Windows:** install the retained or downloaded older signed MSIX with `ForceUpdateFromAnyVersion`, then run doctor. If doctor fails, reinstall the previously active signed MSIX. Microsoft states that an earlier MSIX can be installed without uninstalling first and that the operation keeps app-data content. It does not undo data changes made by the newer version. [Microsoft MSIX downgrade](https://learn.microsoft.com/en-us/windows/msix/desktop/managing-your-msix-deployment-downgrading) ROI-H customer data is outside package storage, so package servicing must not touch it.

No application update can migrate `ROI_H_HOME` as a side effect. A data migration needs its own plan, backup, compatibility check, and approval. Default uninstall removes the application and managed browsers but preserves `ROI_H_HOME`.

## Browser acquisition

Use the Playwright Python module from the installed runtime, not a global command:

```text
PLAYWRIGHT_BROWSERS_PATH=<managed-browser-root>
<installed-python> -m playwright install chromium
```

Before this command, check for the browser revision required by the installed Playwright version. If it exists and passes a launch probe, do not download it. Use the same `PLAYWRIGHT_BROWSERS_PATH` for install and run. Playwright ties each release to browser binaries and documents custom shared paths. It can remove stale browsers; set `PLAYWRIGHT_SKIP_BROWSER_GC=1` during install, update, and rollback, and delete a revision only when neither the active nor retained rollback version needs it. [Playwright browser documentation](https://playwright.dev/python/docs/browsers)

The browser download is not part of the signed OS installer. This is acceptable only after qualification proves Playwright's downloaded archive verification and the exact proxy, offline, and interrupted-download behavior. An enterprise release can add an approved Playwright download host through Playwright's documented environment settings; it must not add a second ROI-H package channel.

## Signing and notarization

### macOS

- Sign every executable in the payload with **Developer ID Application**, with Hardened Runtime and a secure timestamp.
- Sign the flat installer with **Developer ID Installer**.
- Submit the final package with `notarytool`, staple the ticket, and verify both the code signatures and package signature before publication.
- Do not use `altool`, ad-hoc signatures, or only a checksum. Apple requires Developer ID signatures, Hardened Runtime for command-line targets, secure timestamps, and valid signatures for all executables. Apple supports notarized flat installer packages and stapled tickets. [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) [Apple notarization issue guide](https://developer.apple.com/documentation/security/resolving-common-notarization-issues)

### Windows

- Sign and timestamp the MSIX with a certificate that chains to a root trusted by Windows. Use Microsoft Artifact Signing when ROI-H meets its identity and region rules, or use a public CA code-signing certificate.
- Keep the package identity and publisher stable. Plan certificate rotation before the first customer release.
- Enable package integrity enforcement where the supported Windows baseline permits it.

Microsoft requires a valid, trusted signature for MSIX deployment and recommends timestamping so an expired certificate does not invalidate a package that was valid when signed. [Microsoft MSIX signing](https://learn.microsoft.com/en-us/windows/msix/package/signing-package-overview)

## Existing code disposition

### Keep and adapt

- `src/roi_h/installation.py` — keep application version, data-home, skill, runtime, and doctor checks. Replace the directory-marker browser check with a real Playwright launch probe. **Severity: high** because the current marker can report a broken browser as healthy.
- `src/roi_h/cli.py` — keep `version`, `doctor`, and the external update handoff. Replace the downloaded mutable `update.sh` or `update.ps1` handoff with the OS package operation. Add exact-version rollback only after native qualification.
- `src/roi_h/harness/workspace.py` and `docs/project-storage-activegraph-refactor.md` — keep `resolve_home` and the customer-data boundary.
- `scripts/qualify_release.py`, `scripts/check_publication_boundary.py`, and the locked wheel build in `scripts/prepare_release_candidate.py` — keep as build inputs and release gates. Adapt final output to `.pkg` and `.msix`.

### Delete after native replacement passes acceptance

- `install.sh` and `install.ps1` — delete as public installers. They execute downloaded `uv` scripts and create a parallel Python delivery system instead of using the signed OS package path.
- `packages/roi-h-installer/` — delete the installed Python installer and its duplicate plan/apply platform logic. Native Installer and MSIX must own package verification and servicing. Preserve only tests that can be rewritten as native end-to-end acceptance checks.
- `scripts/build_release_bundle.py` and the wheelhouse tar release path in `scripts/prepare_release_candidate.py` — delete after native artifacts contain the same locked runtime. Do not publish both paths.
- The generated `installer/update.sh` and `installer/update.ps1` behavior in the bootstraps — delete. It downloads mutable scripts from `main`; a SHA-256 pinned release tar is not a signed, rollback-safe update channel.

## Risks and unresolved facts

1. **Blocker:** Prove a current-user, notarized `.pkg` can install the full CPython payload, run the browser setup and doctor scripts, and perform an explicit downgrade on the minimum macOS version. Apple's clearest current-user and downgrade text is archived.
2. **Blocker:** Build a minimal full-trust Python CLI MSIX and prove the `roi-h.exe` execution alias, subprocess skills, browser execution from the external browser root, update, downgrade, and uninstall on clean Windows 11. MSIX file and registry virtualization can break desktop applications that assume normal paths. [Microsoft desktop app package preparation](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-prepare)
3. **High:** Decide whether Windows update is allowed to activate before ROI-H doctor completes. If not, the thin updater must retain the old signed MSIX and automatically reinstall it after a failed doctor.
4. **High:** Confirm Playwright archive integrity, offline cache import, proxy, and interrupted-download behavior from Playwright source or a captured qualification run. The public browser docs do not state all verification details.
5. **High:** Obtain Apple Developer Program signing identities and a Windows trusted signing identity. Confirm Artifact Signing eligibility and SmartScreen behavior.
6. **Medium:** Define supported CPU artifacts. “One installer per OS” cannot mean one binary if ROI-H adds both Intel and Apple silicon without a universal macOS payload or adds Windows Arm64.
7. **Medium:** Define retention and disk policy for one prior application and all browser revisions that it needs.

## Resolution

Retain one build and one customer journey: locked Python is an internal payload; macOS receives a Developer ID-signed, notarized, user-domain `.pkg`; Windows receives a trusted, timestamped MSIX through App Installer; Playwright downloads only the missing compatible Chromium revision into an application-owned cache; native package servicing plus a thin ROI-H doctor/rollback handoff updates application code while `ROI_H_HOME` remains separate and unchanged.

**Map gist:** `locked build -> {notarized user .pkg | signed MSIX/App Installer} -> missing Chromium only -> doctor -> native update/rollback; ~/.roi-h never moves`
