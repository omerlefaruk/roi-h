"""Reconcile ActiveGraph artifact/handoff authority with durable run files."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from activegraph import Object, Runtime

from roi_h.harness import phases as phase_store
from roi_h.harness.domain import ReconciliationIssue, ReconciliationReport
from roi_h.harness.graph_access import phase_objects
from roi_h.harness.records import ArtifactRecord
from roi_h.harness.run_storage import ArtifactAttachment, RunStorage
from roi_h.harness.workspace import Workspace


def reconcile_run(
    runtime: Runtime,
    workspace: Workspace,
    *,
    repair: bool = False,
) -> ReconciliationReport:
    """Report drift and apply only uniquely derivable metadata repairs."""
    issues: list[ReconciliationIssue] = []
    storage = RunStorage(workspace)
    paths = storage.paths(runtime.run_id)
    storage_report = storage.reconcile(runtime.run_id, repair=repair)
    for malformed in storage_report["malformed"]:
        issues.append(
            _issue(
                "artifact_directory_invalid",
                "error",
                f"invalid artifact directory: {malformed}",
            )
        )

    disk = {item.name: item for item in storage.list(runtime.run_id)}
    graph_artifacts = [
        obj
        for obj in runtime.graph.objects(type="rpa.artifact")
        if obj.data.get("run_id") == runtime.run_id
    ]
    by_name: dict[str, list[Object]] = {}
    for obj in graph_artifacts:
        by_name.setdefault(str(obj.data.get("name") or ""), []).append(obj)

    for name, objects in sorted(by_name.items()):
        if len(objects) > 1:
            issues.append(
                _issue(
                    "duplicate_artifact_record",
                    "error",
                    f"multiple graph artifact records use name {name!r}",
                    details={"object_ids": [obj.id for obj in objects]},
                )
            )
            continue
        obj = objects[0]
        attachment = disk.get(name)
        if attachment is None:
            issues.append(
                _issue(
                    "artifact_missing_file",
                    "error",
                    f"graph artifact {name!r} has no corresponding file",
                    object_id=obj.id,
                )
            )
            continue
        expected = _graph_metadata(attachment)
        actual = {key: obj.data.get(key) for key in expected}
        if actual != expected:
            repaired = False
            if repair:
                runtime.graph.patch_object(obj.id, expected)
                repaired = True
            issues.append(
                _issue(
                    "artifact_metadata_mismatch",
                    "error",
                    f"graph metadata for artifact {name!r} does not match its file",
                    object_id=obj.id,
                    path=str(attachment.path),
                    repaired=repaired,
                    details={"graph": actual, "filesystem": expected},
                )
            )

    for name, attachment in sorted(disk.items()):
        if name in by_name:
            continue
        repaired = False
        object_id: str | None = None
        if repair:
            record = ArtifactRecord(
                artifact_id=attachment.artifact_id,
                run_id=runtime.run_id,
                name=attachment.name,
                uri=attachment.uri,
                bytes=attachment.bytes,
                sha256=attachment.sha256,
                media_type=attachment.media_type,
                source="",
                created_at=attachment.created_at,
            )
            object_id = runtime.graph.add_object("rpa.artifact", record.to_graph()).id
            repaired = True
        issues.append(
            _issue(
                "orphan_artifact_file",
                "warning",
                f"artifact file {name!r} has no graph record",
                object_id=object_id,
                path=str(attachment.path),
                repaired=repaired,
            )
        )

    phases = [obj for obj in phase_objects(runtime) if obj.data.get("run_id") == runtime.run_id]
    expected_packages: set[Path] = set()
    for phase in phases:
        expected_package = (
            paths.phases / f"{int(phase.data.get('index') or 0):02d}-{phase.data.get('name') or ''}"
        ).resolve()
        expected_packages.add(expected_package)
        _reconcile_phase(
            runtime,
            workspace,
            phase,
            expected_package,
            issues,
            repair=repair,
        )
    if paths.phases.is_dir():
        for child in sorted(paths.phases.iterdir()):
            if (
                child.is_dir()
                and (child / "manifest.json").is_file()
                and child.resolve() not in expected_packages
            ):
                issues.append(
                    _issue(
                        "orphan_handoff_package",
                        "warning",
                        f"handoff package {child.name!r} has no matching phase record",
                        path=str(child.resolve()),
                    )
                )

    repairs = sum(1 for issue in issues if issue.repaired)
    return ReconciliationReport(
        run_id=runtime.run_id,
        repair_requested=repair,
        ok=not any(not issue.repaired for issue in issues),
        artifacts_scanned=len(graph_artifacts),
        phases_scanned=len(phases),
        repairs=repairs,
        issues=issues,
    )


def _reconcile_phase(
    runtime: Runtime,
    workspace: Workspace,
    phase: Object,
    expected: Path,
    issues: list[ReconciliationIssue],
    *,
    repair: bool,
) -> None:
    data = phase.data
    status = str(data.get("status") or "open")
    artifact_names = [str(name) for name in data.get("artifact_names") or []]
    if status != "done" and not artifact_names and not data.get("handoff_path"):
        return
    storage = RunStorage(workspace)
    source_paths: list[Path] = []
    missing_sources: list[str] = []
    for name in artifact_names:
        try:
            source_paths.append(storage.artifact_path(runtime.run_id, name=name))
        except FileNotFoundError:
            missing_sources.append(name)

    manifest_path = expected / "manifest.json"
    if not manifest_path.is_file():
        repaired = False
        if repair and not missing_sources and status in {"done", "failed", "skipped"}:
            handoff = _write_handoff(runtime, workspace, phase, status, source_paths)
            runtime.graph.patch_object(phase.id, {"handoff_path": handoff["handoff_uri"]})
            repaired = True
        issues.append(
            _issue(
                "phase_handoff_missing",
                "error",
                f"phase {data.get('name')!r} has no handoff manifest",
                object_id=phase.id,
                path=str(manifest_path),
                repaired=repaired,
                details={"missing_source_artifacts": missing_sources},
            )
        )
        return

    try:
        manifest, package = phase_store.read_handoff(expected)
    except (FileNotFoundError, ValueError, TypeError) as exc:
        issues.append(
            _issue(
                "phase_handoff_invalid",
                "error",
                f"phase {data.get('name')!r} handoff is invalid: {type(exc).__name__}: {exc}",
                object_id=phase.id,
                path=str(manifest_path),
            )
        )
        return

    if (
        manifest.phase_id != phase.id
        or manifest.run_id != runtime.run_id
        or manifest.phase != data.get("name")
        or manifest.index != int(data.get("index") or 0)
    ):
        issues.append(
            _issue(
                "phase_handoff_identity_mismatch",
                "error",
                f"phase {data.get('name')!r} handoff identity does not match graph state",
                object_id=phase.id,
                path=str(manifest_path),
            )
        )
        return

    content_drift: dict[str, Any] = {}
    missing_package = [name for name in manifest.artifacts if not (package / name).is_file()]
    if missing_package:
        content_drift["missing_package_artifacts"] = missing_package
    expected_content = {
        "status": status,
        "artifacts": artifact_names,
        "summary": dict(data.get("summary") or {}),
        "require_artifacts": list(data.get("require_artifacts") or []),
    }
    actual_content = {
        "status": manifest.status,
        "artifacts": manifest.artifacts,
        "summary": manifest.summary,
        "require_artifacts": manifest.require_artifacts,
    }
    if actual_content != expected_content:
        content_drift["manifest"] = {
            "graph": expected_content,
            "filesystem": actual_content,
        }
    sources = {path.name: path for path in source_paths}
    changed = [
        name
        for name in artifact_names
        if name in sources
        and (package / name).is_file()
        and _sha256(sources[name]) != _sha256(package / name)
    ]
    if changed:
        content_drift["changed_artifacts"] = changed
    if content_drift:
        repaired = False
        if repair and not missing_sources and status in {"done", "failed", "skipped"}:
            _write_handoff(runtime, workspace, phase, status, source_paths)
            repaired = True
        content_drift["missing_source_artifacts"] = missing_sources
        issues.append(
            _issue(
                "phase_handoff_content_mismatch",
                "error",
                f"phase {data.get('name')!r} handoff content differs from graph state",
                object_id=phase.id,
                path=str(package),
                repaired=repaired,
                details=content_drift,
            )
        )

    expected_uri = (
        f"run-handoff://{runtime.run_id}/"
        f"{package.relative_to(workspace.runs / runtime.run_id).as_posix()}"
    )
    graph_uri = str(data.get("handoff_path") or "")
    if graph_uri != expected_uri:
        repaired = False
        if repair:
            runtime.graph.patch_object(phase.id, {"handoff_path": expected_uri})
            repaired = True
        issues.append(
            _issue(
                "phase_handoff_path_mismatch",
                "error",
                f"phase {data.get('name')!r} graph handoff URI is stale or missing",
                object_id=phase.id,
                path=str(package),
                repaired=repaired,
                details={"graph_uri": graph_uri, "expected_uri": expected_uri},
            )
        )


def _write_handoff(
    runtime: Runtime,
    workspace: Workspace,
    phase: Object,
    status: str,
    source_paths: list[Path],
) -> dict[str, Any]:
    return phase_store.write_handoff(
        workspace.runs,
        run_id=runtime.run_id,
        index=int(phase.data.get("index") or 0),
        name=str(phase.data.get("name") or ""),
        phase_id=phase.id,
        status=status,
        artifact_paths=source_paths,
        summary=dict(phase.data.get("summary") or {}),
        require_artifacts=list(phase.data.get("require_artifacts") or []),
        source_run_id=phase.data.get("source_run_id"),
    )


def _graph_metadata(attachment: ArtifactAttachment) -> dict[str, Any]:
    return {
        "artifact_id": attachment.artifact_id,
        "uri": attachment.uri,
        "bytes": attachment.bytes,
        "sha256": attachment.sha256,
        "media_type": attachment.media_type,
    }


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _issue(
    kind: str,
    severity: str,
    message: str,
    *,
    object_id: str | None = None,
    path: str | None = None,
    repaired: bool = False,
    details: dict[str, Any] | None = None,
) -> ReconciliationIssue:
    return ReconciliationIssue(
        kind=kind,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        object_id=object_id,
        path=path,
        repaired=repaired,
        details=dict(details or {}),
    )
