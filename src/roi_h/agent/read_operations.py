"""Read-only operation handlers over supported product projections."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from roi_h.harness.automation import list_automations, load_automation
from roi_h.harness.automation_source import show_source
from roi_h.harness.diagnostics import DiagnosticSink
from roi_h.harness.guidance_skills import load_guidance_skills
from roi_h.harness.records import evidenced_artifacts
from roi_h.harness.retention import RetentionPlanner
from roi_h.harness.secrets import get_secret, list_secrets
from roi_h.harness.store_lifecycle import StoreLifecycle
from roi_h.harness.workspace import Workspace
from roi_h.observer.activegraph_adapter import ActiveGraphProjectionAdapter

if TYPE_CHECKING:
    from roi_h.agent.contract import CommandRequest

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def list_runs(request: CommandRequest) -> dict[str, Any]:
    """Return a bounded stable run page."""
    adapter = _adapter(request)
    limit = _limit(request)
    offset = _cursor_offset(request.arguments.get("cursor"))
    rows = adapter.list_run_headers(limit=limit + 1, offset=offset)
    items = rows[:limit]
    has_more = len(rows) > limit
    return {
        "items": items,
        "next_cursor": f"offset:{offset + limit}" if has_more else None,
        "has_more": has_more,
        "snapshot": adapter.snapshot(),
    }


def show_run(request: CommandRequest) -> dict[str, Any]:
    """Return one run and its typed object projection."""
    run_id = _run_id(request)
    adapter = _adapter(request)
    header = adapter.run_header(run_id)
    if header is None:
        msg = f"run not found: {run_id}"
        raise FileNotFoundError(msg)
    projection = adapter.project_run(run_id)
    return cast(
        "dict[str, Any]",
        _safe({"run_id": run_id, "header": header, "objects": projection["objects"]}),
    )


def run_events(request: CommandRequest) -> dict[str, Any]:
    """Return bounded ordered run events."""
    run_id = _run_id(request)
    adapter = _adapter(request)
    after = _event_sequence(request.arguments.get("after"))
    rows = adapter.list_events(run_id, limit=_limit(request) + 1, after_sequence=after)
    limit = _limit(request)
    items = rows[:limit]
    return cast(
        "dict[str, Any]",
        _safe(
            {
                "run_id": run_id,
                "items": items,
                "next_cursor": items[-1]["event_id"] if len(rows) > limit and items else None,
                "has_more": len(rows) > limit,
                "snapshot": adapter.snapshot(),
            }
        ),
    )


def run_trace(request: CommandRequest) -> dict[str, Any]:
    """Return a bounded product trace grouped by public record type."""
    run_id = _run_id(request)
    projection = _adapter(request).project_run(run_id)
    grouped: dict[str, list[dict[str, Any]]] = {
        "runs": [],
        "phases": [],
        "artifacts": [],
    }
    mapping = {
        "rpa.run": "runs",
        "rpa.phase": "phases",
        "rpa.artifact": "artifacts",
    }
    for item in projection["objects"][: _limit(request)]:
        group = mapping.get(str(item.get("type")))
        if group:
            grouped[group].append(item)
    return cast(
        "dict[str, Any]",
        _safe({"run_id": run_id, **grouped, "bounded": True}),
    )


def project_show(request: CommandRequest) -> dict[str, Any]:
    """Show logical project identity and environment."""
    workspace = _workspace(request)
    return {
        "project": workspace.project,
        "project_id": workspace.project_id,
        "environment": workspace.env,
        "layout_version": workspace.layout_version,
        "logical_roots": [
            "project://reference",
            "run://input",
            "run://work",
            "run://output",
            "artifact://",
        ],
    }


def store_status(request: CommandRequest) -> dict[str, Any]:
    """Show store state without its physical location."""
    return cast(
        "dict[str, Any]",
        _safe(StoreLifecycle().inspect(_workspace(request)).to_dict()),
    )


def store_check(request: CommandRequest) -> dict[str, Any]:
    """Run a read-only store check."""
    level: Literal["quick", "full"] = "full" if request.arguments.get("full") is True else "quick"
    return cast(
        "dict[str, Any]",
        _safe(StoreLifecycle().check(_workspace(request), level).to_dict()),
    )


def artifact_list(request: CommandRequest) -> dict[str, Any]:
    """List durable artifact metadata."""
    run_id = _run_id(request)
    items = [
        cast("dict[str, Any]", _safe(item.to_dict()))
        for item in evidenced_artifacts(_workspace(request), run_id)
    ]
    return {"run_id": run_id, **_page(items, request, snapshot=f"artifacts:{len(items)}")}


def artifact_show(request: CommandRequest) -> dict[str, Any]:
    """Show one artifact by identity."""
    artifact_id = str(request.arguments.get("artifact_id") or "")
    run_id = _run_id(request)
    item = next(
        (
            _safe(item.to_dict())
            for item in evidenced_artifacts(_workspace(request), run_id)
            if item.artifact_id == artifact_id
        ),
        None,
    )
    if item is None:
        msg = f"artifact not found: {artifact_id}"
        raise FileNotFoundError(msg)
    return cast("dict[str, Any]", item)


def skill_list(request: CommandRequest) -> dict[str, Any]:
    """List Markdown-only guidance skills."""
    workspace = _workspace(request)
    skills = load_guidance_skills(
        shared_root=workspace.shared_skills,
        project_root=workspace.project_skills,
    )
    items = [skills[name].to_dict() for name in sorted(skills)]
    return _page(items, request, snapshot=f"skills:{len(items)}")


def skill_show(request: CommandRequest) -> dict[str, Any]:
    """Show one guidance skill and its Markdown references."""
    name = _name(request)
    workspace = _workspace(request)
    skills = load_guidance_skills(
        shared_root=workspace.shared_skills,
        project_root=workspace.project_skills,
    )
    skill = skills.get(name)
    if skill is None:
        msg = f"skill not found: {name}"
        raise FileNotFoundError(msg)
    return skill.to_dict(include_documents=True)


def automation_source_list(request: CommandRequest) -> dict[str, Any]:
    """List editable automation sources in the selected project."""
    workspace = _workspace(request)
    items: list[dict[str, Any]] = []
    if workspace.automation_sources.is_dir():
        roots = (
            path
            for path in workspace.automation_sources.iterdir()
            if path.is_dir() and (path / "automation.json").is_file()
        )
        for root in sorted(roots):
            data = show_source(root)
            items.append(
                {
                    "name": data["name"],
                    "source_digest": data["source_digest"],
                    "phases": data["manifest"]["phases"],
                }
            )
    return _page(items, request, snapshot=f"automation-sources:{len(items)}")


def automation_source_show(request: CommandRequest) -> dict[str, Any]:
    """Show one editable automation source and its portable text files."""
    return show_source(_workspace(request).automation_sources / _name(request))


def automation_list(request: CommandRequest) -> dict[str, Any]:
    """List immutable automation packages."""
    items = list_automations(_workspace(request))
    return _page(items, request, snapshot=f"automations:{len(items)}")


def automation_show(request: CommandRequest) -> dict[str, Any]:
    """Show and verify one immutable automation."""
    data = load_automation(
        _workspace(request),
        _name(request),
        version=request.arguments.get("version"),
    )
    data.pop("source_root", None)
    data.pop("source", None)
    return cast("dict[str, Any]", _safe(data))


def automation_compare(request: CommandRequest) -> dict[str, Any]:
    """Compare two automation manifests."""
    workspace = _workspace(request)
    name = _name(request)
    left = load_automation(workspace, name, version=str(request.arguments["version_a"]))
    right = load_automation(workspace, name, version=str(request.arguments["version_b"]))
    return {
        "name": name,
        "version_a": left["version"],
        "version_b": right["version"],
        "same_digest": left["package_digest"] == right["package_digest"],
        "digest_a": left["package_digest"],
        "digest_b": right["package_digest"],
    }


def secret_list(request: CommandRequest) -> dict[str, Any]:
    """List names-only secret metadata."""
    return list_secrets(_workspace(request))


def secret_status(request: CommandRequest) -> dict[str, Any]:
    """Show names-only declaration and provider status for one secret."""
    name = _name(request)
    data = secret_list(request)
    declared = name in data["names"]
    available = False
    provider_error: str | None = None
    if declared:
        try:
            available = get_secret(_workspace(request), name) is not None
        except RuntimeError as exc:
            provider_error = str(exc)
    return {
        "name": name,
        "configured": declared,
        "available": available,
        "status": (
            "available"
            if available
            else "provider_error"
            if provider_error
            else "missing"
            if declared
            else "undeclared"
        ),
        "project": data["project"],
        "environment": data["environment"],
        "provider": data["provider"],
        "provider_error": provider_error,
    }


def retention_show(request: CommandRequest) -> dict[str, Any]:
    """Show one existing retention plan."""
    plan_id = str(request.arguments.get("plan_id") or "")
    return RetentionPlanner().inspect(_workspace(request), plan_id).to_dict()


def diagnostic_list(request: CommandRequest) -> dict[str, Any]:
    """Read bounded redacted diagnostics."""
    items = DiagnosticSink(request.arguments.get("home")).read(limit=_limit(request))
    return _page(items, request, snapshot=f"diagnostics:{len(items)}")


def _objects_page(request: CommandRequest, object_type: str) -> dict[str, Any]:
    run_id = _run_id(request)
    objects = _adapter(request).project_run(run_id)["objects"]
    items = [
        cast("dict[str, Any]", _safe(item)) for item in objects if item.get("type") == object_type
    ]
    return {"run_id": run_id, **_page(items, request, snapshot=_adapter(request).snapshot())}


def _object_show(
    request: CommandRequest,
    object_type: str,
    argument_name: str,
) -> dict[str, Any]:
    object_id = str(request.arguments.get(argument_name) or "")
    page = _objects_page(request, object_type)
    item = next(
        (
            item
            for item in page["items"]
            if item.get("id") == object_id
            or (isinstance(item.get("data"), dict) and item["data"].get(argument_name) == object_id)
        ),
        None,
    )
    if item is None:
        msg = f"{object_type} not found: {object_id}"
        raise FileNotFoundError(msg)
    return cast("dict[str, Any]", item)


def _page(
    items: list[dict[str, Any]],
    request: CommandRequest,
    *,
    snapshot: str,
) -> dict[str, Any]:
    limit = _limit(request)
    offset = _cursor_offset(request.arguments.get("cursor"))
    selected = items[offset : offset + limit]
    has_more = offset + limit < len(items)
    return {
        "items": selected,
        "next_cursor": f"offset:{offset + limit}" if has_more else None,
        "has_more": has_more,
        "snapshot": snapshot,
    }


def _name(request: CommandRequest) -> str:
    value = request.arguments.get("name")
    if not isinstance(value, str) or not value:
        msg = "name is required"
        raise ValueError(msg)
    return value


def _adapter(request: CommandRequest) -> ActiveGraphProjectionAdapter:
    workspace = _workspace(request)
    return ActiveGraphProjectionAdapter(workspace.db)


def _workspace(request: CommandRequest) -> Workspace:
    return Workspace.open(
        request.arguments.get("home"),
        project=request.context.project or request.arguments.get("project"),
        env=request.context.environment or request.arguments.get("environment"),
        db=request.arguments.get("db"),
    )


def _run_id(request: CommandRequest) -> str:
    value = request.context.run_id or request.arguments.get("run_id")
    if not isinstance(value, str) or not value:
        msg = "run_id is required"
        raise ValueError(msg)
    return value


def _limit(request: CommandRequest) -> int:
    value = request.arguments.get("limit", _DEFAULT_LIMIT)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        msg = "limit must be a positive integer"
        raise ValueError(msg)
    return min(value, _MAX_LIMIT)


def _cursor_offset(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, str) or not value.startswith("offset:"):
        msg = "cursor is invalid"
        raise ValueError(msg)
    return int(value.removeprefix("offset:"))


def _event_sequence(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, str) or not value.startswith("event:"):
        msg = "event cursor is invalid"
        raise ValueError(msg)
    return int(value.removeprefix("event:"))


def _safe(value: Any) -> Any:  # noqa: ANN401 - Recursive JSON values are dynamic.
    if isinstance(value, dict):
        return {
            key: _safe(item)
            for key, item in value.items()
            if key.lower()
            not in {
                "db",
                "database",
                "path",
                "project_root",
                "skills_root",
                "artifact_root",
            }
        }
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str) and value.startswith("/"):
        return "<physical-path-redacted>"
    return value


__all__ = [
    "artifact_list",
    "artifact_show",
    "automation_compare",
    "automation_list",
    "automation_show",
    "automation_source_list",
    "automation_source_show",
    "diagnostic_list",
    "list_runs",
    "project_show",
    "retention_show",
    "run_events",
    "run_trace",
    "secret_list",
    "secret_status",
    "show_run",
    "skill_list",
    "skill_show",
    "store_check",
    "store_status",
]
