"""Read-only operation handlers over supported product projections."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

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
        "steps": [],
        "invocations": [],
        "approvals": [],
        "artifacts": [],
        "reconciliation": [],
    }
    mapping = {
        "rpa.run": "runs",
        "rpa.phase": "phases",
        "rpa.step": "steps",
        "rpa.invocation": "invocations",
        "rpa.approval": "approvals",
        "rpa.artifact": "artifacts",
        "rpa.reconciliation": "reconciliation",
    }
    for item in projection["objects"][: _limit(request)]:
        group = mapping.get(str(item.get("type")))
        if group:
            grouped[group].append(item)
    return cast(
        "dict[str, Any]",
        _safe({"run_id": run_id, **grouped, "bounded": True}),
    )


def _adapter(request: CommandRequest) -> ActiveGraphProjectionAdapter:
    workspace = _workspace(request)
    return ActiveGraphProjectionAdapter(workspace.db)


def _workspace(request: CommandRequest) -> Workspace:
    return Workspace.open(
        request.arguments.get("home"),
        project=request.context.project or request.arguments.get("project"),
        env=request.context.environment or request.arguments.get("environment"),
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
            if key.lower() not in {
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


__all__ = ["list_runs", "run_events", "run_trace", "show_run"]
