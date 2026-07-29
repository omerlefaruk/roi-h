"""Safe write handlers that adapt existing domain modules."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from roi_h.agent.contract import CommandContext, CommandResult, DestructivePlan
from roi_h.agent.tasks import TaskStore
from roi_h.harness.application import RunSession
from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.domain import InvocationIdentity
from roi_h.harness.store_lifecycle import StoreLifecycle
from roi_h.harness.workspace import (
    Workspace,
    create_project,
    delete_project,
    resolve_home,
)

if TYPE_CHECKING:
    from pathlib import Path

    from roi_h.agent.contract import CommandRequest


def project_create(request: CommandRequest) -> dict[str, Any]:
    """Create one project and return only logical identity."""
    arguments = request.arguments
    result = create_project(
        arguments.get("home"),
        str(arguments.get("name") or ""),
        display_name=str(arguments.get("display_name") or ""),
        set_active=arguments.get("use") is not False,
        env=str(arguments.get("environment") or "dev"),
    )
    return _safe(result)


def project_delete_plan(request: CommandRequest) -> dict[str, Any]:
    """Create a reviewable recoverable-deletion plan."""
    home = resolve_home(request.arguments.get("home"))
    name = str(request.arguments.get("name") or "")
    project = home / "projects" / name
    if not project.is_dir():
        msg = f"project not found: {name}"
        raise FileNotFoundError(msg)
    plan = DestructivePlan(
        plan_id=f"plan_{uuid4().hex}",
        operation="project.delete",
        arguments={"name": name},
        effects=[
            {
                "action": "move_to_recoverable_trash",
                "project": name,
            }
        ],
        state_digest=_tree_digest(project),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        apply_operation="project.delete.apply",
    )
    atomic_write_json(
        _plan_path(home, plan.plan_id),
        plan.model_dump(mode="json"),
        mode=0o600,
    )
    return plan.model_dump(mode="json")


def project_delete_apply(request: CommandRequest) -> dict[str, Any]:
    """Apply a valid deletion plan."""
    home = resolve_home(request.arguments.get("home"))
    plan_id = str(request.arguments.get("plan_id") or "")
    plan = _load_plan(home, plan_id)
    if plan.apply_operation != "project.delete.apply":
        msg = "plan.state_changed: plan is for another operation"
        raise RuntimeError(msg)
    if plan.expires_at < datetime.now(UTC):
        msg = "plan.expired: the deletion plan expired"
        raise RuntimeError(msg)
    name = str(plan.arguments["name"])
    project = home / "projects" / name
    if not project.is_dir() or _tree_digest(project) != plan.state_digest:
        msg = "plan.state_changed: project changed after this plan was created"
        raise RuntimeError(msg)
    result = delete_project(home, name, force=True)
    _plan_path(home, plan_id).unlink(missing_ok=True)
    return _safe(result)


def store_backup(request: CommandRequest) -> dict[str, Any]:
    """Create a consistent backup through a durable task."""
    workspace = Workspace.open(
        request.arguments.get("home"),
        project=request.context.project or request.arguments.get("project"),
        env=request.context.environment or request.arguments.get("environment"),
    )
    request_id = request.request_id or f"req_{uuid4().hex}"
    tasks = TaskStore(request.arguments.get("home"))
    task = tasks.begin("store.backup", request_id)
    result = StoreLifecycle().backup(workspace, str(request.arguments.get("output") or ""))
    safe_result = _safe(result.to_dict())
    terminal = CommandResult(
        operation="store.backup",
        request_id=request_id,
        ok=True,
        changed=True,
        context=CommandContext(project=workspace.project, environment=workspace.env),
        result=safe_result,
    )
    task = tasks.succeed(task, terminal)
    return {"task": task.model_dump(mode="json")}


def run_start(request: CommandRequest) -> dict[str, Any]:
    """Create one durable ActiveGraph run."""
    workspace = _workspace(request)
    goal = str(request.arguments.get("goal") or "")
    run_id = str(request.arguments.get("run_id") or "")
    if not run_id:
        if not request.idempotency_key:
            msg = "run_id or idempotency_key is required"
            raise ValueError(msg)
        run_id = f"run_{hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:24]}"
    session = RunSession.create(
        workspace,
        run_id=run_id,
        skills_root=request.arguments.get("skills"),
        auto_approve=False,
    )
    run = session.start_run(
        goal,
        actor=str(request.arguments.get("actor") or "ai"),
        phase_plan=request.arguments.get("phase_plan"),
    )
    return {
        "run_id": session.runtime.run_id,
        "object_id": run.id,
        "status": "open",
        "project": workspace.project,
        "environment": workspace.env,
    }


def tool_invoke(request: CommandRequest) -> dict[str, Any]:
    """Invoke one tool with caller-controlled ActiveGraph identity."""
    session = _session(request)
    name = str(request.arguments.get("name") or "")
    skill, separator, tool = name.partition(".")
    if not separator or not skill or not tool:
        msg = "tool name must have the form skill.tool"
        raise ValueError(msg)
    key = request.idempotency_key
    if not key:
        msg = "idempotency_key is required"
        raise ValueError(msg)
    token = hashlib.sha256(key.encode()).hexdigest()
    identity = InvocationIdentity(
        invocation_id=f"inv_{token[:24]}",
        idempotency_key=f"agent:{session.runtime.run_id}:{key}",
    )
    supplied = request.arguments.get("arguments") or {}
    if not isinstance(supplied, dict):
        msg = "tool arguments must be an object"
        raise TypeError(msg)
    _check_invocation_conflict(session, name, supplied, identity)
    pending = next(
        (
            item
            for item in session.runtime.pending_approvals()
            if item.data.get("idempotency_key") == identity.idempotency_key
        ),
        None,
    )
    if pending is not None:
        return {
            "run_id": session.runtime.run_id,
            "status": "pending_approval",
            "approval_id": pending.id,
            "invocation_id": identity.invocation_id,
            "idempotency_key": identity.idempotency_key,
        }
    result = session.invoke(
        skill,
        tool,
        supplied,
        actor=str(request.arguments.get("actor") or "ai"),
        force=request.arguments.get("force") is True,
        identity=identity,
    )
    return result.model_dump(mode="json")


def approval_approve(request: CommandRequest) -> dict[str, Any]:
    """Approve and execute one deferred invocation."""
    result = _session(request).approve(
        str(request.arguments.get("approval_id") or ""),
        approved_by=str(request.arguments.get("by") or "user"),
    )
    return result.model_dump(mode="json")


def approval_reject(request: CommandRequest) -> dict[str, Any]:
    """Reject one deferred invocation without execution."""
    return _session(request).reject(
        str(request.arguments.get("approval_id") or ""),
        rejected_by=str(request.arguments.get("by") or "user"),
        reason=str(request.arguments.get("reason") or ""),
    )


def _workspace(request: CommandRequest) -> Workspace:
    return Workspace.open(
        request.arguments.get("home"),
        project=request.context.project or request.arguments.get("project"),
        env=request.context.environment or request.arguments.get("environment"),
    )


def _session(request: CommandRequest) -> RunSession:
    run_id = request.context.run_id or request.arguments.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        msg = "run_id is required"
        raise ValueError(msg)
    return RunSession.reopen(
        _workspace(request),
        run_id=run_id,
        skills_root=request.arguments.get("skills"),
        auto_approve=False,
    )


def _check_invocation_conflict(
    session: RunSession,
    name: str,
    arguments: dict[str, Any],
    identity: InvocationIdentity,
) -> None:
    for item in session.runtime.graph.objects(type="rpa.invocation"):
        if item.data.get("idempotency_key") != identity.idempotency_key:
            continue
        if item.data.get("name") != name or dict(item.data.get("args") or {}) != arguments:
            msg = "request.idempotency_conflict: tool arguments changed for this key"
            raise RuntimeError(msg)


def _load_plan(home: Path, plan_id: str) -> DestructivePlan:
    path = _plan_path(home, plan_id)
    if not path.is_file():
        msg = f"plan not found: {plan_id}"
        raise FileNotFoundError(msg)
    return DestructivePlan.model_validate_json(path.read_text(encoding="utf-8"))


def _plan_path(home: Path, plan_id: str) -> Path:
    if not plan_id.startswith("plan_") or not plan_id.removeprefix("plan_").isalnum():
        msg = f"invalid plan ID: {plan_id}"
        raise ValueError(msg)
    return home / "runtime" / "agent-plans" / f"{plan_id}.json"


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink")
        elif path.is_file():
            stat = path.stat()
            digest.update(str(stat.st_size).encode())
            digest.update(b":")
            digest.update(str(stat.st_mtime_ns).encode())
        else:
            digest.update(b"directory")
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _safe(value: dict[str, Any]) -> dict[str, Any]:
    return cast(
        "dict[str, Any]",
        _safe_value(value),
    )


def _safe_value(value: Any) -> Any:  # noqa: ANN401 - Recursive JSON values are dynamic.
    if isinstance(value, dict):
        return {
            key: _safe_value(item)
            for key, item in value.items()
            if key
            not in {
                "home",
                "path",
                "project_root",
                "trash_path",
                "db",
            }
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, str) and value.startswith("/"):
        return "<physical-path-redacted>"
    return value


__all__ = [
    "approval_approve",
    "approval_reject",
    "project_create",
    "project_delete_apply",
    "project_delete_plan",
    "run_start",
    "store_backup",
    "tool_invoke",
]
