from __future__ import annotations

from types import SimpleNamespace

from roi_h.harness import invocation_runtime
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.workspace import Workspace, create_project


def test_browser_worker_defaults_to_headed_in_dev_and_keeps_explicit_mode(
    tmp_path, monkeypatch
) -> None:
    create_project(tmp_path, "browser-env", set_active=True)
    workspace = Workspace.open(tmp_path, project="browser-env", env="dev")
    install_root = tmp_path / "managed-install"
    browser_root = install_root / "browsers"
    monkeypatch.setenv("ROI_H_INSTALL_ROOT", str(install_root))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_SKIP_BROWSER_GC", raising=False)
    monkeypatch.delenv("ROI_H_BROWSER_HEADED", raising=False)
    monkeypatch.setattr(invocation_runtime, "isolated_process_environment", dict)
    tool = SimpleNamespace(skill="browser", secret_names=())

    environment = invocation_runtime._worker_environment(
        tool,
        {},
        workspace=workspace,
        run_id="browser-run",
        idempotency_key="browser-call",
    )

    assert environment["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_root)
    assert environment["PLAYWRIGHT_SKIP_BROWSER_GC"] == "1"
    assert environment["ROI_H_BROWSER_HEADED"] == "1"

    state = RunStorage(workspace).prepare("browser-run").runtime / "browser-session.json"
    state.write_text('{"headed": false}', encoding="utf-8")
    continued_environment = invocation_runtime._worker_environment(
        tool,
        {},
        workspace=workspace,
        run_id="browser-run",
        idempotency_key="browser-snapshot",
    )
    assert continued_environment["ROI_H_BROWSER_HEADED"] == "0"

    prod_environment = invocation_runtime._worker_environment(
        tool,
        {},
        workspace=Workspace.open(tmp_path, project="browser-env", env="prod"),
        run_id="prod-browser-run",
        idempotency_key="prod-browser-call",
    )
    assert "ROI_H_BROWSER_HEADED" not in prod_environment

    monkeypatch.setenv("ROI_H_BROWSER_HEADED", "0")
    explicit_environment = invocation_runtime._worker_environment(
        tool,
        {},
        workspace=workspace,
        run_id="explicit-browser-run",
        idempotency_key="explicit-browser-call",
    )
    assert explicit_environment["ROI_H_BROWSER_HEADED"] == "0"
