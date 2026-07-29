"""Durable ActiveGraph approval rejection."""

from __future__ import annotations

from pathlib import Path

from roi_h.harness.application import RunSession
from roi_h.harness.workspace import Workspace, create_project


def test_rejected_approval_stays_terminal_after_reload(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")
    session = RunSession.create(workspace, run_id="reject-run", auto_approve=False)
    session.start_run("Reject a shell command")
    pending = session.invoke(
        "shell",
        "run",
        {"command": "printf should-not-run"},
    )
    assert pending.status == "pending_approval"
    assert pending.approval_id

    rejected = session.reject(
        pending.approval_id,
        rejected_by="operator",
        reason="The command is not approved.",
    )
    assert rejected == {
        "approval_id": pending.approval_id,
        "status": "denied",
        "rejected_by": "operator",
        "reason": "The command is not approved.",
    }
    assert session.runtime.pending_approvals() == []
    assert "tool.requested" not in [event.type for event in session.runtime.graph.events]
    assert "approval.rejected" in [event.type for event in session.runtime.graph.events]

    reopened = RunSession.reopen(
        workspace,
        run_id="reject-run",
        auto_approve=False,
    )
    assert reopened.runtime.pending_approvals() == []
