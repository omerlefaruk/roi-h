"""Thin invocation interface over ActiveGraph authority and approvals."""

from __future__ import annotations

from typing import Any

from activegraph import Runtime

from roi_h.harness.domain import InvocationIdentity, StepResult
from roi_h.harness.invocation_runtime import (
    rejected_step,
    request_invocation_approval,
    submit_invocation,
)
from roi_h.harness.loader import SkillCatalog
from roi_h.harness.workspace import Workspace

_ACTION_CLASS = {
    "read": "R0",
    "write": "R3",
    "destructive": "R4",
}


def invoke(
    runtime: Runtime,
    catalog: SkillCatalog,
    workspace: Workspace,
    *,
    auto_approve: bool,
    skill: str,
    tool: str,
    args: dict[str, Any] | None = None,
    actor: str = "ai",
    force: bool = False,
    identity: InvocationIdentity | None = None,
) -> StepResult:
    """Evaluate authority, then dispatch or propose through ActiveGraph."""
    if not runtime.budget.remaining():
        exhausted = runtime.budget.exhausted_by() or "unknown"
        msg = f"ActiveGraph budget exhausted: {exhausted}"
        raise RuntimeError(msg)

    invocation = identity or InvocationIdentity.fresh(runtime.run_id)
    skill_tool = catalog.resolve(skill, tool)
    payload = dict(args or {})
    action_class = _ACTION_CLASS[skill_tool.effect]
    if skill_tool.scope == "project" and workspace.env == "dev":
        action_class = "R3"
    decision = runtime.evaluate_capability_authority(
        capability=skill_tool.name,
        action_class=action_class,
        actor=actor,
    )
    needs_approval = not decision.auto_approved or skill_tool.requires_approval

    try:
        if needs_approval and not force and not auto_approve:
            return request_invocation_approval(
                runtime,
                skill_tool,
                payload,
                workspace=workspace,
                actor=actor,
                identity=invocation,
                reason=decision.reason,
            )
        return submit_invocation(
            runtime,
            skill_tool,
            payload,
            workspace=workspace,
            actor=actor,
            identity=invocation,
        )
    except Exception as exc:  # noqa: BLE001 — persist canonical rejection.
        return rejected_step(
            runtime,
            skill_tool,
            payload,
            workspace=workspace,
            identity=invocation,
            exc=exc,
        )


def approve(
    runtime: Runtime,
    catalog: SkillCatalog,
    workspace: Workspace,
    approval_id: str,
    *,
    approved_by: str = "user",
) -> StepResult:
    """Grant a native ActiveGraph approval and return its tool result."""
    pending = next(
        (item for item in runtime.pending_approvals() if item.id == approval_id),
        None,
    )
    if pending is None:
        msg = f"approval not found: {approval_id}"
        raise KeyError(msg)
    invocation_id = str(pending.data["invocation_id"])
    identity = InvocationIdentity(
        invocation_id=invocation_id,
        idempotency_key=str(pending.data["idempotency_key"]),
        attempt=int(pending.data.get("attempt") or 1),
    )
    skill_tool = catalog.resolve(str(pending.data["skill"]), str(pending.data["tool"]))
    pending.data["approval_id"] = approval_id
    object_id = runtime.approve(approval_id, approved_by=approved_by)
    runtime.run_until_idle()
    invocation = runtime.graph.get_object(object_id)
    if invocation is not None and invocation.data.get("step_id"):
        step = runtime.graph.get_object(str(invocation.data["step_id"]))
        if step is not None:
            data = dict(step.data)
            return StepResult(
                step_id=step.id,
                run_id=str(data["run_id"]),
                skill=str(data["skill"]),
                tool=str(data["tool"]),
                scope=data.get("scope", "global"),
                args=dict(data.get("args") or {}),
                output=dict(data.get("output") or {}),
                status=data["status"],
                error=str(data["error"]) if data.get("error") else None,
                failure=data.get("failure"),
                approval_id=approval_id,
                phase=str(data["phase"]) if data.get("phase") else None,
                phase_id=str(data["phase_id"]) if data.get("phase_id") else None,
                invocation_id=identity.invocation_id,
                idempotency_key=identity.idempotency_key,
                attempt=identity.attempt,
            )
    return rejected_step(
        runtime,
        skill_tool,
        dict(pending.data.get("args") or {}),
        workspace=workspace,
        identity=identity,
        exc=RuntimeError("approved invocation ended without a durable result"),
    )


def list_approvals(runtime: Runtime, *, status: str = "pending") -> list[dict[str, Any]]:
    """Project ActiveGraph's durable pending queue for operator output."""
    if status not in {"pending", "all"}:
        msg = "approval status must be pending or all"
        raise ValueError(msg)
    return [
        {
            "id": item.id,
            "data": {
                **dict(item.data),
                "status": "pending",
                "reason": item.reason,
                "object_type": item.object_type,
            },
        }
        for item in runtime.pending_approvals()
    ]
