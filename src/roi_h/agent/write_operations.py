"""Safe write handlers that adapt existing domain modules."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from roi_h.agent.contract import CommandContext, CommandResult, DestructivePlan
from roi_h.agent.tasks import TaskStore
from roi_h.harness.atomicfs import atomic_write_json
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
    "project_create",
    "project_delete_apply",
    "project_delete_plan",
    "store_backup",
]
