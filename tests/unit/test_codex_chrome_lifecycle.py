"""Focused tests for the fake Codex Chrome lifecycle skill."""

from __future__ import annotations

from pathlib import Path

import pytest

from roi_h import RunSession
from roi_h.harness.loader import default_skills_root, load_skills
from roi_h.harness.workspace import Workspace, create_project


def _workspace(tmp_path: Path) -> Workspace:
    home = tmp_path / ".roi-h"
    create_project(home, "runtime", set_active=True)
    return Workspace.open(home, project="runtime", env="dev")


def test_codex_chrome_lifecycle_tools_have_closed_typed_contracts() -> None:
    catalog = load_skills(default_skills_root())

    names = {tool.name for tool in catalog.tools if tool.skill == "codex_chrome"}
    assert names == {
        "codex_chrome.start",
        "codex_chrome.status",
        "codex_chrome.stop",
    }
    for name in names:
        tool = catalog.get(name)
        assert tool.input_schema["additionalProperties"] is False
        assert tool.output_schema["additionalProperties"] is False
        assert tool.allow_in_prod is True
        assert tool.secret_names == ()
        assert tool.network_hosts == ()
        assert tool.filesystem_roots == ()
        assert tool.timeout_seconds == 30.0

    assert catalog.resolve("codex_chrome", "start").effect == "write"
    assert catalog.resolve("codex_chrome", "start").idempotency == "key"
    assert catalog.resolve("codex_chrome", "status").effect == "read"
    assert catalog.resolve("codex_chrome", "status").idempotency == "none"
    assert catalog.resolve("codex_chrome", "stop").effect == "write"
    assert catalog.resolve("codex_chrome", "stop").idempotency == "reconcile"


def test_start_status_stop_records_phase_evidence_and_handshake(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    harness = RunSession.create(
        workspace,
        run_id="codex-lifecycle",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("prove the fake Codex Chrome lifecycle")
    phase = harness.begin_phase("explore")

    started = harness.invoke(
        "codex_chrome",
        "start",
        {"profile_binding": "reviews"},
        identity=None,
    )
    status = harness.invoke(
        "codex_chrome",
        "status",
        {"profile_binding": "reviews"},
    )
    stopped = harness.invoke(
        "codex_chrome",
        "stop",
        {"profile_binding": "reviews"},
    )

    assert started.status == "ok"
    assert started.output["ok"] is True
    assert started.output["status"] == "active"
    assert started.output["ownership"] == "started"
    assert started.output["session_id"].startswith("cx_session_")
    assert started.output["tab_id"].startswith("cx_tab_")
    assert started.output["handshake"] == {
        "bridge_version": "fake-1.0",
        "extension_version": "fake-1.0",
        "profile_identity": started.output["handshake"]["profile_identity"],
        "capabilities": [
            "session.start",
            "session.attach",
            "session.status",
            "session.stop",
        ],
    }
    assert status.output["session_id"] == started.output["session_id"]
    assert status.output["tab_id"] == started.output["tab_id"]
    assert stopped.output["status"] == "closed"
    assert stopped.output["session_id"] == started.output["session_id"]

    steps = list(harness.runtime.graph.objects(type="rpa.step"))
    assert len(steps) == 3
    assert {step.data["phase_id"] for step in steps} == {phase["phase_id"]}
    assert all(step.data["phase"] == "explore" for step in steps)
    assert all(step.data["output"]["provider_event_id"].startswith("cx_event_") for step in steps)
    requested = [event for event in harness.runtime.graph.events if event.type == "tool.requested"]
    responded = [event for event in harness.runtime.graph.events if event.type == "tool.responded"]
    assert len(requested) == 3
    assert len(responded) == 3


def test_profile_binding_is_exclusive_and_stop_respects_ownership(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    first = RunSession.create(
        workspace,
        run_id="codex-owner-a",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    second = RunSession.create(
        workspace,
        run_id="codex-owner-b",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    first.start_run("own the fake profile")
    second.start_run("try to share the fake profile")

    started = first.invoke("codex_chrome", "start", {"profile_binding": "shared"})
    blocked = second.invoke("codex_chrome", "start", {"profile_binding": "shared"})
    forbidden_stop = second.invoke("codex_chrome", "stop", {"profile_binding": "shared"})

    assert started.output["ok"] is True
    assert blocked.output["error"]["code"] == "profile.in_use"
    assert blocked.output["error"]["remediation"]
    assert forbidden_stop.output["error"]["code"] == "profile.in_use"
    assert (
        first.invoke("codex_chrome", "stop", {"profile_binding": "shared"}).output["status"]
        == "closed"
    )


def test_attach_detaches_and_provider_failures_are_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    harness = RunSession.create(
        workspace,
        run_id="codex-attach",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("attach to the fake profile")

    attached = harness.invoke(
        "codex_chrome",
        "start",
        {"profile_binding": "existing", "mode": "attach"},
    )
    detached = harness.invoke("codex_chrome", "stop", {"profile_binding": "existing"})
    missing = harness.invoke("codex_chrome", "status", {"profile_binding": "existing"})

    assert attached.output["ownership"] == "attached"
    assert detached.output["status"] == "detached"
    assert missing.output["error"]["code"] == "session.missing"

    for mode, code in (
        ("missing", "provider.missing"),
        ("incompatible", "provider.incompatible"),
        ("unavailable", "provider.unavailable"),
    ):
        monkeypatch.setenv("ROI_H_CODEX_CHROME_FAKE_MODE", mode)
        result = harness.invoke(
            "codex_chrome",
            "start",
            {"profile_binding": f"{mode}-profile"},
        )
        assert result.output["ok"] is False
        assert result.output["error"]["code"] == code
        assert result.output["error"]["remediation"]
