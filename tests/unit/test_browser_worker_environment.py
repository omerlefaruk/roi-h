from __future__ import annotations

from types import SimpleNamespace

from roi_h.harness import invocation_runtime
from roi_h.harness.workspace import Workspace, create_project


def test_isolated_worker_keeps_the_managed_playwright_root(tmp_path, monkeypatch) -> None:
    create_project(tmp_path, "browser-env", set_active=True)
    workspace = Workspace.open(tmp_path, project="browser-env", env="dev")
    install_root = tmp_path / "managed-install"
    browser_root = install_root / "browsers"
    monkeypatch.setenv("ROI_H_INSTALL_ROOT", str(install_root))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_SKIP_BROWSER_GC", raising=False)
    monkeypatch.setattr(invocation_runtime, "isolated_process_environment", dict)

    environment = invocation_runtime._worker_environment(
        SimpleNamespace(secret_names=()),
        {},
        workspace=workspace,
        run_id="browser-run",
        idempotency_key="browser-call",
    )

    assert environment["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_root)
    assert environment["PLAYWRIGHT_SKIP_BROWSER_GC"] == "1"
