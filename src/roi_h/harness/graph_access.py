"""Locality for ``rpa.run`` / ``rpa.phase`` graph lookups on a Runtime."""

from __future__ import annotations

from typing import Any, cast

from activegraph import Object, Runtime

from roi_h.harness.domain import PhaseInfo, PhasePlanEntry, PhaseStatus, infer_phase_role


def run_object(runtime: Runtime) -> Object | None:
    runs = list(runtime.graph.objects(type="rpa.run"))
    return runs[0] if runs else None


def patch_run(runtime: Runtime, fields: dict[str, Any]) -> None:
    run = run_object(runtime)
    if run is None:
        msg = "rpa.run object missing; call start_run first"
        raise RuntimeError(msg)
    runtime.graph.patch_object(run.id, fields)


def phase_objects(runtime: Runtime) -> list[Object]:
    items = list(runtime.graph.objects(type="rpa.phase"))
    return sorted(items, key=lambda obj: (int(obj.data.get("index") or 0), obj.id))


def next_phase_index(runtime: Runtime) -> int:
    items = phase_objects(runtime)
    if not items:
        return 1
    return max(int(obj.data.get("index") or 0) for obj in items) + 1


def current_phase_object(runtime: Runtime) -> Object | None:
    run = run_object(runtime)
    if run is None:
        return None
    phase_id = run.data.get("current_phase_id")
    if not phase_id:
        return None
    obj = runtime.graph.get_object(str(phase_id))
    if obj is not None:
        return obj
    return next(
        (item for item in runtime.graph.objects(type="rpa.phase") if item.id == phase_id),
        None,
    )


def phase_tags(runtime: Runtime) -> tuple[str | None, str | None]:
    phase = current_phase_object(runtime)
    if phase is None or phase.data.get("status") != "open":
        return None, None
    return str(phase.data.get("name")), phase.id


def find_phase(
    runtime: Runtime,
    name: str,
    *,
    prefer_status: str | None = None,
) -> Object | None:
    matches = [obj for obj in phase_objects(runtime) if obj.data.get("name") == name]
    if not matches:
        return None
    if prefer_status is not None:
        preferred = [obj for obj in matches if obj.data.get("status") == prefer_status]
        if preferred:
            return preferred[-1]
    return matches[-1]


def plan_entry(runtime: Runtime, name: str) -> PhasePlanEntry | None:
    run = run_object(runtime)
    if run is None:
        return None
    for raw in run.data.get("phase_plan") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("name") == name:
            return PhasePlanEntry.model_validate(raw)
    return None


def phase_info(runtime: Runtime, obj: Object) -> PhaseInfo:
    phase_id = obj.id
    steps = [
        step
        for step in runtime.graph.objects(type="rpa.step")
        if step.data.get("phase_id") == phase_id
    ]
    raw_status = obj.data.get("status") or "open"
    status: PhaseStatus = cast("PhaseStatus", raw_status)
    role_raw = obj.data.get("role")
    role = infer_phase_role(
        str(obj.data.get("name") or ""),
        role_raw if role_raw in {"explore", "work", "verify"} else None,
    )
    return PhaseInfo(
        phase_id=phase_id,
        name=str(obj.data.get("name") or ""),
        index=int(obj.data.get("index") or 0),
        status=status,
        description=str(obj.data.get("description") or ""),
        role=role,
        require_artifacts=list(obj.data.get("require_artifacts") or []),
        artifact_names=list(obj.data.get("artifact_names") or []),
        summary=dict(obj.data.get("summary") or {}),
        error=cast("str | None", obj.data.get("error")),
        handoff_path=cast("str | None", obj.data.get("handoff_path")),
        end_event_id=cast("str | None", obj.data.get("end_event_id")),
        step_count=len(steps),
        ok_steps=sum(1 for step in steps if step.data.get("status") == "ok"),
        error_steps=sum(1 for step in steps if step.data.get("status") == "error"),
    )


def phase_dict(runtime: Runtime, obj: Object) -> dict[str, Any]:
    return phase_info(runtime, obj).model_dump(mode="json")
