"""Phase state machine: open/close, handoff, seed, artifact contracts.

Owns graph field layout for ``rpa.phase`` together with disk handoffs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from activegraph import Object, Runtime

from roi_h.harness import artifacts as artifact_store
from roi_h.harness import phases as phase_store
from roi_h.harness.domain import PhaseStatus, infer_phase_role, validate_phase_name
from roi_h.harness.graph_access import (
    current_phase_object,
    find_phase,
    next_phase_index,
    patch_run,
    phase_dict,
    phase_info,
    phase_objects,
    plan_entry,
)
from roi_h.harness.records import ArtifactRecord, PhaseRecord
from roi_h.harness.workspace import Workspace


def begin_phase(
    runtime: Runtime,
    name: str,
    *,
    description: str = "",
    require_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    """Open a new phase; only one phase may be open at a time."""
    validate_phase_name(name)
    open_phase = current_phase_object(runtime)
    if open_phase is not None and open_phase.data.get("status") == "open":
        msg = (
            f"phase {open_phase.data.get('name')!r} is still open; "
            "end, fail, or skip it before beginning another"
        )
        raise RuntimeError(msg)

    entry = plan_entry(runtime, name)
    desc = description or (entry.description if entry else "")
    role = infer_phase_role(name, entry.role if entry is not None else None)
    req = (
        list(require_artifacts)
        if require_artifacts is not None
        else (list(entry.require_artifacts) if entry else [])
    )
    if role == "verify" and require_artifacts is None and not req:
        req = []
    index = next_phase_index(runtime)
    record = PhaseRecord(
        run_id=runtime.run_id,
        name=name,
        index=index,
        status="open",
        description=desc,
        role=role,
        require_artifacts=req,
    )
    phase = runtime.graph.add_object("rpa.phase", record.to_graph())
    patch_run(
        runtime,
        {
            "current_phase_id": phase.id,
            "current_phase": name,
        },
    )
    return phase_dict(runtime, phase)


def end_phase(
    runtime: Runtime,
    workspace: Workspace,
    *,
    summary: dict[str, Any] | None = None,
    require_artifacts: list[str] | None = None,
    status: PhaseStatus = "done",
    error: str | None = None,
) -> dict[str, Any]:
    """Close the open phase, enforce artifact contract, write handoff package."""
    if status not in {"done", "failed", "skipped"}:
        msg = f"invalid end status for phase: {status!r}"
        raise ValueError(msg)
    phase = current_phase_object(runtime)
    if phase is None or phase.data.get("status") != "open":
        msg = "no open phase to end"
        raise RuntimeError(msg)

    names = list(phase.data.get("artifact_names") or [])
    required = (
        list(require_artifacts)
        if require_artifacts is not None
        else list(phase.data.get("require_artifacts") or [])
    )
    _assert_required_artifacts(phase, names, required, status=status)
    artifact_paths = _resolve_phase_artifact_paths(workspace, runtime.run_id, names, status=status)
    handoff = _write_phase_handoff(
        runtime,
        workspace,
        phase,
        status=status,
        artifact_paths=artifact_paths,
        summary=summary,
        required=required,
        names=names,
    )
    runtime.graph.patch_object(
        phase.id,
        {
            "status": status,
            "summary": dict(summary or {}),
            "error": error,
            "require_artifacts": required,
            "handoff_path": handoff["handoff_path"] if handoff else None,
            "end_event_id": None,
        },
    )
    events = list(runtime.graph.events)
    end_event_id = events[-1].id if events else None
    if end_event_id is not None:
        runtime.graph.patch_object(phase.id, {"end_event_id": end_event_id})
    patch_run(runtime, {"current_phase_id": None, "current_phase": None})
    refreshed = runtime.graph.get_object(phase.id)
    if refreshed is None:
        msg = f"phase object disappeared after end: {phase.id}"
        raise RuntimeError(msg)
    result = phase_dict(runtime, refreshed)
    if handoff:
        result["handoff"] = handoff
    return result


def fail_phase(
    runtime: Runtime,
    workspace: Workspace,
    *,
    error: str,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not error.strip():
        msg = "error must be non-empty"
        raise ValueError(msg)
    return end_phase(runtime, workspace, summary=summary, status="failed", error=error)


def skip_phase(
    runtime: Runtime,
    workspace: Workspace,
    name: str,
    *,
    reason: str = "",
) -> dict[str, Any]:
    validate_phase_name(name)
    open_phase = current_phase_object(runtime)
    if open_phase is not None and open_phase.data.get("status") == "open":
        if open_phase.data.get("name") != name:
            msg = f"cannot skip {name!r} while {open_phase.data.get('name')!r} is open"
            raise RuntimeError(msg)
        return end_phase(
            runtime,
            workspace,
            summary={"reason": reason} if reason else {},
            status="skipped",
            error=None,
        )
    entry = plan_entry(runtime, name)
    index = next_phase_index(runtime)
    role = infer_phase_role(name, entry.role if entry is not None else None)
    record = PhaseRecord(
        run_id=runtime.run_id,
        name=name,
        index=index,
        status="skipped",
        description=entry.description if entry else "",
        role=role,
        require_artifacts=list(entry.require_artifacts) if entry else [],
        summary={"reason": reason} if reason else {},
        end_event_id=(list(runtime.graph.events)[-1].id if runtime.graph.events else None),
    )
    phase = runtime.graph.add_object("rpa.phase", record.to_graph())
    return phase_dict(runtime, phase)


def retry_phase(
    runtime: Runtime,
    name: str,
    *,
    description: str = "",
    require_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    prior = find_phase(runtime, name)
    if prior is None:
        msg = f"no prior phase named {name!r} to retry"
        raise KeyError(msg)
    if prior.data.get("status") == "open":
        msg = f"phase {name!r} is already open"
        raise RuntimeError(msg)
    return begin_phase(
        runtime,
        name,
        description=description or str(prior.data.get("description") or ""),
        require_artifacts=(
            require_artifacts
            if require_artifacts is not None
            else list(prior.data.get("require_artifacts") or [])
        ),
    )


def list_phases(runtime: Runtime) -> list[dict[str, Any]]:
    return [phase_info(runtime, obj).model_dump(mode="json") for obj in phase_objects(runtime)]


def seed_from_handoff(
    runtime: Runtime,
    workspace: Workspace,
    handoff_path: str | Path,
) -> dict[str, Any]:
    packages = phase_store.discover_handoffs(handoff_path)
    seeded: list[dict[str, Any]] = []
    for manifest, package in packages:
        files = phase_store.list_handoff_files(package, manifest)
        phase_store.copy_artifacts_to_run(
            workspace.artifacts,
            run_id=runtime.run_id,
            files=files,
        )
        handoff = phase_store.write_handoff(
            workspace.artifacts,
            run_id=runtime.run_id,
            index=manifest.index,
            name=manifest.phase,
            phase_id=f"seeded:{manifest.phase_id}",
            status="done",
            artifact_paths=[
                artifact_store.get_artifact_path(
                    workspace.artifacts,
                    run_id=runtime.run_id,
                    name=name,
                )
                for name in manifest.artifacts
            ],
            summary=dict(manifest.summary),
            require_artifacts=list(manifest.require_artifacts),
            source_run_id=manifest.run_id,
        )
        for name in manifest.artifacts:
            path = artifact_store.get_artifact_path(
                workspace.artifacts,
                run_id=runtime.run_id,
                name=name,
            )
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            art = ArtifactRecord(
                run_id=runtime.run_id,
                name=name,
                path=str(path),
                bytes=path.stat().st_size,
                sha256=digest,
                phase=manifest.phase,
                phase_id=None,
                seeded=True,
            )
            runtime.graph.add_object("rpa.artifact", art.to_graph())
        record = PhaseRecord(
            run_id=runtime.run_id,
            name=manifest.phase,
            index=manifest.index,
            status="done",
            description=f"seeded from {manifest.run_id}",
            require_artifacts=list(manifest.require_artifacts),
            artifact_names=list(manifest.artifacts),
            summary=dict(manifest.summary),
            handoff_path=handoff["handoff_path"],
            end_event_id=(list(runtime.graph.events)[-1].id if runtime.graph.events else None),
            seeded=True,
            source_run_id=manifest.run_id,
            source_phase_id=manifest.phase_id,
        )
        phase = runtime.graph.add_object("rpa.phase", record.to_graph())
        seeded.append(phase_dict(runtime, phase))

    patch_run(
        runtime,
        {
            "seeded_from": str(Path(handoff_path).expanduser().resolve()),
            "current_phase_id": None,
            "current_phase": None,
        },
    )
    return {
        "ok": True,
        "run_id": runtime.run_id,
        "seeded_phases": seeded,
        "count": len(seeded),
        "handoff_path": str(Path(handoff_path).expanduser().resolve()),
    }


def put_artifact(
    runtime: Runtime,
    workspace: Workspace,
    source: str | Path,
    *,
    name: str | None = None,
) -> dict[str, Any]:
    meta = artifact_store.put_artifact(
        workspace.artifacts,
        run_id=runtime.run_id,
        source=source,
        name=name,
    )
    current = current_phase_object(runtime)
    phase_name = current.data.get("name") if current is not None else None
    phase_id = current.id if current is not None else None
    record = ArtifactRecord(
        run_id=runtime.run_id,
        name=meta["name"],
        path=meta["path"],
        bytes=meta["bytes"],
        sha256=meta["sha256"],
        phase=str(phase_name) if phase_name is not None else None,
        phase_id=phase_id,
    )
    obj = runtime.graph.add_object("rpa.artifact", record.to_graph())
    if current is not None:
        names = list(current.data.get("artifact_names") or [])
        if meta["name"] not in names:
            names.append(meta["name"])
        runtime.graph.patch_object(current.id, {"artifact_names": names})
    meta["object_id"] = obj.id
    meta["phase"] = phase_name
    meta["phase_id"] = phase_id
    return meta


def list_artifacts(workspace: Workspace, run_id: str) -> list[dict[str, Any]]:
    return artifact_store.list_artifacts(workspace.artifacts, run_id=run_id)


def _assert_required_artifacts(
    phase: Object,
    names: list[str],
    required: list[str],
    *,
    status: PhaseStatus,
) -> None:
    if status != "done" or not required:
        return
    missing = [item for item in required if item not in names]
    if missing:
        msg = f"phase {phase.data.get('name')!r} missing required artifacts: {missing}"
        raise ValueError(msg)


def _resolve_phase_artifact_paths(
    workspace: Workspace,
    run_id: str,
    names: list[str],
    *,
    status: PhaseStatus,
) -> list[Path]:
    paths: list[Path] = []
    for name in names:
        try:
            paths.append(
                artifact_store.get_artifact_path(
                    workspace.artifacts,
                    run_id=run_id,
                    name=name,
                )
            )
        except FileNotFoundError:
            if status == "done":
                raise
    return paths


def _write_phase_handoff(
    runtime: Runtime,
    workspace: Workspace,
    phase: Object,
    *,
    status: PhaseStatus,
    artifact_paths: list[Path],
    summary: dict[str, Any] | None,
    required: list[str],
    names: list[str],
) -> dict[str, Any] | None:
    if not names and status != "done":
        return None
    return phase_store.write_handoff(
        workspace.artifacts,
        run_id=runtime.run_id,
        index=int(phase.data.get("index") or 0),
        name=str(phase.data["name"]),
        phase_id=phase.id,
        status=status,
        artifact_paths=artifact_paths,
        summary=summary,
        require_artifacts=required,
    )
