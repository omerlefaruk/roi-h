# Research: Verify managed Chromium acquisition and recovery

## Summary

ROI-H resolves Playwright 1.61.0 in `uv.lock`, and that Python release embeds upstream driver commit `1cc5a90cfa3eaa430b1a991963100f95126caa47`. The driver selects Chromium revision 1228 (Chrome for Testing 149.0.7827.55), but Playwright does not give a cryptographic integrity guarantee for the browser archive. Current ROI-H also does not make its managed shared path available at launch, does not run a real browser doctor check, and does not safely retain rollback browsers on POSIX.

**Issue result:** close the research ticket with implementation work required. Severity is **blocker** for managed browser launch and archive trust, and **high** for rollback retention and false-positive health checks.

## Exact version and source chain

- `uv.lock` selects `playwright==1.61.0` and hashes each platform wheel. The release wheelhouse is built from this frozen lock and verifies wheel targets by length and SHA-256 before install: [`uv.lock`](https://github.com/omerlefaruk/roi-h/blob/main/uv.lock), [`scripts/prepare_release_candidate.py`](https://github.com/omerlefaruk/roi-h/blob/main/scripts/prepare_release_candidate.py), [`packages/roi-h-installer/src/roi_h_installer/core.py`](https://github.com/omerlefaruk/roi-h/blob/main/packages/roi-h-installer/src/roi_h_installer/core.py).
- The project declaration is not pinned: `pyproject.toml` says `playwright>=1.61.0`. Thus, normal package installation can select a later Playwright and a different browser. Only the frozen release-candidate flow currently fixes 1.61.0. **High:** change the declaration to `playwright==1.61.0` or make the release-only pin an explicit, tested contract. [`pyproject.toml`](https://github.com/omerlefaruk/roi-h/blob/main/pyproject.toml)
- Playwright Python 1.61.0 rolls driver commit `1cc5a90cfa3eaa430b1a991963100f95126caa47`. The Python CLI passes the caller environment to that embedded driver. [Python 1.61.0 roll](https://github.com/microsoft/playwright-python/commit/613c3bfecda5c4dba207e177b4f427e4ca854776), [`playwright/__main__.py` at v1.61.0](https://github.com/microsoft/playwright-python/blob/v1.61.0/playwright/__main__.py), [`_impl/_driver.py` at v1.61.0](https://github.com/microsoft/playwright-python/blob/v1.61.0/playwright/_impl/_driver.py)
- The corresponding driver manifest selects `chromium` revision `1228`, browser `149.0.7827.55`; `playwright install chromium` also resolves the matching headless shell and FFmpeg, and WinLDD on Windows. [`browsers.json` at the exact driver commit](https://github.com/microsoft/playwright/blob/1cc5a90cfa3eaa430b1a991963100f95126caa47/packages/playwright-core/browsers.json), [`registry/index.ts` at the exact driver commit](https://github.com/microsoft/playwright/blob/1cc5a90cfa3eaa430b1a991963100f95126caa47/packages/playwright-core/src/server/registry/index.ts)

## Capability matrix

| Concern | Playwright 1.61.0 guarantee | Current ROI-H result | Required ROI-H check |
|---|---|---|---|
| Revision selection | Exact embedded `browsers.json` maps the host platform to revision 1228 and fixed executable subpaths. Unsupported platforms can use a fallback build; some descriptors can have platform overrides. | Release metadata accepts a free `browser_revision` string and checks only that directory. It does not prove that the string matches the installed Playwright manifest. | Read/derive the Playwright target set from the staged exact wheel; reject release metadata that does not match it. Record browser name, revision, browser version, platform, and all installed support targets, not one unbound string. |
| Archive integrity | HTTPS is used by default. The downloader requires HTTP 200, checks `Content-Length` only for non-chunked responses, extracts ZIP, fixes executable mode, and writes `INSTALLATION_COMPLETE` last. It does **not** verify a cryptographic digest; a chunked response has no size check. | ROI-H verifies Python wheels and the outer release bundle, but the browser is fetched later from the CDN and is not a declared hashed target. | **Blocker:** put each supported platform browser archive and SHA-256/length in trusted release metadata, or add an equivalent signed digest manifest and verify bytes before extraction. Do not call Playwright's marker an integrity proof. |
| Shared-path install and lookup | The same absolute `PLAYWRIGHT_BROWSERS_PATH` at install and run makes Playwright use a shared store. Relative paths are resolved against `INIT_CWD` or CWD. Default lookup is the OS cache. | Installer sets `<install-root>/browsers` only for install. POSIX launcher is a direct symlink; Windows launcher sets `ROI_H_INSTALL_ROOT` but not `PLAYWRIGHT_BROWSERS_PATH`; `_session.py` calls `p.chromium.executable_path` with no managed-path setup. **Blocker:** managed launch normally searches the wrong store. | Set the absolute managed path for installer, doctor, all skill subprocesses, and direct runtime launch. Test from an unrelated CWD with an empty default Playwright cache. |
| Proxy use | Official variables support `HTTPS_PROXY`, `NODE_EXTRA_CA_CERTS`, idle timeout, global/per-browser download hosts, and `PLAYWRIGHT_BROWSERS_PATH`. | Installer inherits the environment, so these can reach the driver. ROI-H does not model, validate, redact, or report them. | Preserve supported variables across bootstrap/updater boundaries, redact credentials, and test a proxy plus custom CA and a custom artifact host. |
| Offline use | Launch is offline when a complete target already exists at the selected browser path. The CLI has no general offline acquisition guarantee. `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD` is not a substitute for `playwright install chromium`. | Wheel installation is offline, but browser acquisition is network-only and plan reports a network requirement. | Support a verified local archive/repository input, or state that first acquisition cannot be offline. Prove launch with network disabled after acquisition. |
| Interrupted download | One invocation uses a unique temp directory, makes up to five attempts across mirrors, removes failed ZIP/destination between reported attempts, and writes the completion marker last. A later invocation re-downloads when the marker is absent and replaces the destination before extraction. There is no byte-range resume or durable transaction journal. A hard-killed process can leave temp data, a partial destination, or a lock. | ROI-H catches a returned failure and removes staging/final app state, but a hard kill bypasses Python cleanup. Replanning can see `.staging`; there is no browser-specific reconciliation. | Inspect completion marker **and executable**; remove incomplete target directories only while no installer owns the lock; clean owned stale temp/lock state under a bounded policy; retry from verified bytes; then launch-test. Never infer success from directory presence. |
| Concurrent acquisition | Playwright locks the registry, retries lock acquisition, and writes a package link before GC/install. | ROI-H has no higher-level transaction owner or recovery rule for a stale Playwright lock. | Serialize one browser-store writer and report lock owner/age. Do not delete an active lock. |
| Garbage collection | On install, Playwright follows files in `.links`, loads each linked package's `browsers.json`, retains marked revisions, deletes unreferenced browser directories, and removes broken links. `PLAYWRIGHT_SKIP_BROWSER_GC=1` disables install-time GC. | On POSIX, browser install runs in a staged environment that is then renamed. The `.links` target is the staged package path and becomes broken. A later install can remove the rollback browser. On Windows the environment is installed directly at its final path, but launch lookup is still missing. **High.** | Do not rely on Playwright links across environment relocation. For managed installs, disable Playwright GC and let ROI-H delete only revisions not required by the active and retained rollback releases; or create stable links only after final placement before any GC. |
| Update and rollback retention | Upstream GC can retain multiple versions only while valid package links exist. It does not know ROI-H's rollback policy. | App update retains old version directories, but no rollback command is implemented and browser retention is not bound to retained releases. `_restore_previous_install` can restore the app pointer after a normal failure, not after process death. | Keep at least current plus previous healthy app release and the union of their exact browser target sets. Switch pointers only after verified acquisition and real launch. Recovery must reconcile transaction records, pointer, version, browser set, and stale staging after restart. |
| Health check | `executablePathOrDie` checks executable accessibility; host dependency validation is separate. | `_install_browser` accepts return code plus a caller-named directory. `doctor` only checks that this directory exists and calls the result `browser.launch`; it never resolves or launches Chromium. **Blocker false positive.** | Resolve the expected executable through Playwright with the managed path, verify it is inside the expected revision directory, launch it, create one context/page, evaluate a value, close it, and report exact identity/failure. |

Primary upstream source for download/retry behavior: [`browserFetcher.ts`](https://github.com/microsoft/playwright/blob/1cc5a90cfa3eaa430b1a991963100f95126caa47/packages/playwright-core/src/server/registry/browserFetcher.ts) and [`oopDownloadBrowserMain.ts`](https://github.com/microsoft/playwright/blob/1cc5a90cfa3eaa430b1a991963100f95126caa47/packages/playwright-core/src/server/registry/oopDownloadBrowserMain.ts). Official configuration and GC contract: [Playwright Python browser docs](https://playwright.dev/python/docs/browsers).

## Unsupported assumptions

1. `browser_revision="chromium-1228"` in ROI-H metadata does not control Playwright selection. `ROI_H_BROWSER_REVISION` is an ROI-H-only variable; upstream does not read it.
2. `INSTALLATION_COMPLETE`, a target directory, or a successful ZIP extraction is not a SHA-256 integrity guarantee.
3. Playwright does not resume partial downloads after interruption.
4. Proxy support does not mean offline support. A local artifact host is still an acquisition source and must contain the exact platform URL layout.
5. A shared browser directory is not discovered from `ROI_H_INSTALL_ROOT`; `PLAYWRIGHT_BROWSERS_PATH` must be consistent at install and run.
6. Playwright GC does not retain “the previous ROI-H release.” It retains revisions referenced by valid `.links` package paths.
7. Retaining old application directories does not guarantee rollback while their browser targets can be collected.
8. `doctor` directory presence does not prove executable presence, host libraries, process startup, or protocol compatibility.
9. The current range in `pyproject.toml` is not an exact pin, even though the current lock is exact.
10. The selected Chromium build is Chrome for Testing, not a system Chrome installation and not a promise of all proprietary codecs or enterprise-policy behavior.

## Minimum managed contract

### Acquisition

1. Bind every ROI-H release to exact Playwright Python version 1.61.0 and the exact host target set derived from its embedded manifest.
2. Use one absolute `<install-root>/browsers` path for all managed operations.
3. Acquire only a browser archive whose platform, URL identity, length, and SHA-256 are in trusted release data. Support the same verification for local/offline bytes.
4. Serialize writers. Download to an owned temporary path, verify before extraction, extract to a new target, verify the expected executable, write ROI-H completion metadata last, then atomically expose the target where possible.
5. Preserve documented proxy/CA/timeout variables without logging credentials.

### Launch and health

1. Always pass the managed browser path into the process before Playwright starts.
2. Ask the pinned Playwright package for `chromium.executable_path`; reject a path outside the expected revision root.
3. Launch the managed executable, create a page, evaluate a constant, close cleanly, and include Playwright version, Chromium revision/version, platform, executable path, and target digest identity in structured doctor output.
4. Run this check before initial activation, after final placement, after pointer activation, and during rollback qualification.

### Retention

1. Store the exact browser target set with each installed ROI-H release.
2. Retain at least the active release, the previous healthy release, and the union of their target sets.
3. Disable upstream install-time GC for the managed store unless all `.links` targets are stable final paths. Prefer explicit ROI-H GC after a successful switch.
4. GC only unreferenced, complete targets after the rollback retention set is committed. Never delete browser profiles or user data.

### Recovery

1. At startup/inspect, reconcile transaction record, staging path, active pointer, installed release metadata, browser completion metadata, executable, and writer lock.
2. If acquisition is incomplete, keep the previous pointer active, remove only transaction-owned partial data after proving no active writer, and retry the same digest-bound target.
3. If activation or launch fails, atomically restore the prior pointer and state and keep its browser targets.
4. After a hard interruption, return a structured recoverable state. Do not treat an existing directory as success and do not use a new target identity for an ambiguous retry.

## Code to keep or change

### Keep

- `packages/roi-h-installer/src/roi_h_installer/core.py`: verified local wheel target checks, staging before activation, atomic pointer replacement, previous pointer/state capture, and normal exception rollback.
- `scripts/prepare_release_candidate.py` and `scripts/build_release_bundle.py`: frozen wheel acquisition and release target SHA-256/length metadata.
- Playwright's exact manifest-based executable selection, completion-marker-last behavior, unique temp directory, retry loop, and registry lock. Use these as lower-level mechanisms, not as stronger guarantees than source provides.

### Change

- **High — `pyproject.toml`:** replace the open Playwright range with the exact qualified version, or enforce an equally strict generated release constraint.
- **Blocker — `packages/roi-h-installer/src/roi_h_installer/core.py::_install_browser`:** validate release identity against Playwright's manifest; set managed path; add digest-bound archive acquisition; verify the actual executable and launch; do not check only `browser_root / browser_revision`.
- **Blocker — `src/roi_h/installation.py::_inspect_browser`:** replace directory-only `browser.launch` pass with a real, read-only launch/protocol check and exact identity details.
- **Blocker — `skills/browser/scripts/_session.py::_chromium_executable` and runtime process setup:** set/require the managed shared path before starting Playwright. Do not rely on caller CWD or default cache.
- **High — `packages/roi-h-installer/src/roi_h_installer/core.py::_execute_initial_install`:** prevent stale `.links` after POSIX staging rename. Use ROI-H retention/GC or create stable references after final placement.
- **High — `install.sh` and `install.ps1`:** make launchers pass stable install root, data home, and browser path consistently. POSIX currently uses only a symlink; Windows omits the browser path.
- **High — release metadata models and `scripts/build_release_bundle.py`:** replace the free single `browser_revision` with exact per-platform browser targets plus archive digest/length and retained-release references.
- **High — installer recovery/inspect:** add restart reconciliation for stale staging, partial browser targets, transaction state, and stale locks. Current exception cleanup is not process-death recovery.

## Tests required

1. Lock/manifest test: built wheel contains Playwright 1.61.0; its manifest resolves Chromium 1228 and expected support targets on each supported OS/architecture; arbitrary release revision is rejected.
2. Shared-path end-to-end test: clean default cache, unrelated CWD, managed install, launcher invocation, browser skill launch, and doctor all use `<install-root>/browsers`.
3. Integrity tests: corrupt same-length archive, truncated archive, chunked corrupt archive, wrong-platform archive, and tampered digest all fail before activation and keep the prior version active.
4. Proxy tests: HTTPS proxy, authenticated proxy with redacted output, custom CA, custom global/per-Chromium host, timeout, and fallback failure.
5. Offline tests: verified local archive installs with network denied; an already-installed browser launches with network denied; missing bytes return a stable non-retry loop failure.
6. Interruption tests at download, extraction, completion-metadata write, app-environment rename, pointer switch, and post-switch doctor. Restart must reconcile and preserve the prior active release.
7. Concurrency test: two updates target the same store; one writer succeeds, the other waits or returns a stable lock result; neither deletes the other's complete target.
8. GC/rollback test: install release A, update to B with a different browser revision, confirm A and B browsers remain, rollback offline to A, then install C and delete only targets outside the active-plus-previous retention set.
9. POSIX relocation regression: `.links` never points to removed staging, or managed GC is disabled and explicit ROI-H GC preserves A.
10. Doctor negative tests: missing executable, directory without marker, marker without executable, wrong revision path, missing host dependency, launch crash, and protocol mismatch must fail.
11. Native acceptance on macOS ARM64 and Windows x86-64; add each supported Linux target when distribution claims it.

No tests were added in this research-only change.

## Residual risks

- **Blocker:** upstream browser downloads have no cryptographic archive verification. ROI-H needs its own trusted digest boundary.
- **Blocker:** current managed installations can install into one path and launch from another.
- **High:** a future package install can select later than 1.61.0 because source metadata uses `>=`.
- **High:** POSIX staging relocation breaks Playwright `.links`; later upstream GC can remove rollback browsers.
- **High:** current doctor can report `browser.launch=pass` without launching a process.
- **High:** hard-kill recovery is not transactional and stale Playwright lock handling needs safe owner detection.
- **Medium:** browser archives are large; retaining active plus rollback targets needs disk-budget and low-space behavior.
- **Medium:** proxy credentials and custom CA paths can leak unless structured logs redact them.
- **Medium:** Linux host libraries are outside archive acquisition and need a separately qualified, non-root policy.

## Sources

### Kept primary sources

- [ROI-H `uv.lock`](https://github.com/omerlefaruk/roi-h/blob/main/uv.lock) — exact resolved Python package and wheel hashes.
- [ROI-H installer core](https://github.com/omerlefaruk/roi-h/blob/main/packages/roi-h-installer/src/roi_h_installer/core.py) — current acquisition, staging, activation, checks, and rollback callers.
- [ROI-H browser session](https://github.com/omerlefaruk/roi-h/blob/main/skills/browser/scripts/_session.py) — current executable lookup and launch caller.
- [ROI-H installation health](https://github.com/omerlefaruk/roi-h/blob/main/src/roi_h/installation.py) — current browser doctor check.
- [ROI-H POSIX bootstrap](https://github.com/omerlefaruk/roi-h/blob/main/install.sh) and [Windows bootstrap](https://github.com/omerlefaruk/roi-h/blob/main/install.ps1) — current launchers and updater boundaries.
- [Playwright Python 1.61.0 release](https://github.com/microsoft/playwright-python/releases/tag/v1.61.0) and [exact roll commit](https://github.com/microsoft/playwright-python/commit/613c3bfecda5c4dba207e177b4f427e4ca854776) — Python-to-driver source chain.
- [Exact upstream `browsers.json`](https://github.com/microsoft/playwright/blob/1cc5a90cfa3eaa430b1a991963100f95126caa47/packages/playwright-core/browsers.json) — revision and browser version.
- [Exact upstream registry](https://github.com/microsoft/playwright/blob/1cc5a90cfa3eaa430b1a991963100f95126caa47/packages/playwright-core/src/server/registry/index.ts) — path lookup, URLs, lock, `.links`, GC, revision retention, and target resolution.
- [Exact upstream fetcher](https://github.com/microsoft/playwright/blob/1cc5a90cfa3eaa430b1a991963100f95126caa47/packages/playwright-core/src/server/registry/browserFetcher.ts) and [out-of-process downloader](https://github.com/microsoft/playwright/blob/1cc5a90cfa3eaa430b1a991963100f95126caa47/packages/playwright-core/src/server/registry/oopDownloadBrowserMain.ts) — retries, temporary paths, size check, extraction, and marker order.
- [Official Playwright Python browser docs](https://playwright.dev/python/docs/browsers) — supported proxy, custom host, shared path, and GC public contract.

### Dropped

- GitHub issues, Stack Overflow, blogs, CDN commentary, and third-party package summaries — excluded because the ticket permits only primary sources.

## Gaps

No native install, proxy, interruption, or rollback experiment was run. The conclusions above come from the exact pinned source and current ROI-H callers. Exact per-platform archive digests are not present in the allowed sources; release engineering must obtain and publish them through ROI-H's trusted release process before implementation can claim archive integrity.

## Issue-ready resolution summary

Playwright 1.61.0 deterministically selects Chromium 1228 from its embedded driver manifest and supports a shared path, proxies/custom hosts, retry-with-clean-reinstall, marker-based completeness, locking, and link-based GC. It does not hash browser archives, resume interrupted bytes, provide first-install offline acquisition, or understand ROI-H rollback retention. ROI-H must pin the dependency exactly, bind and verify per-platform browser archive identity, pass one managed browser path at install and launch, replace directory-only doctor checks with a real launch, own active-plus-previous retention instead of staged `.links`, and add restart reconciliation and the listed native tests.

**One-line map gist:** `release(Playwright 1.61.0 + signed archive digest) -> verified <install-root>/browsers target -> real launch doctor -> atomic app switch -> retain active ∪ previous browser sets -> explicit safe GC/recovery`.
