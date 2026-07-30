"""Durable ActiveGraph approval rejection."""

from __future__ import annotations

from pathlib import Path

from roi_h.harness.application import RunSession
from roi_h.harness.custom import define_project_tool
from roi_h.harness.workspace import Workspace, create_project


def test_rejected_approval_stays_terminal_after_reload(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")
    define_project_tool(
        skill="approval",
        tool="request",
        description="Request approval without an external effect.",
        project_root=workspace.project_skills,
    )
    session = RunSession.create(workspace, run_id="reject-run", auto_approve=False)
    session.start_run("Reject a project tool")
    pending = session.invoke("approval", "request", {"value": "should-not-run"})
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
