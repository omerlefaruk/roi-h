"""Reconcile durable graph records with run artifact and handoff files."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from activegraph import Object, Runtime

from roi_h.harness import phases as phase_store
from roi_h.harness.domain import ReconciliationIssue, ReconciliationReport
from roi_h.harness.graph_access import phase_objects
from roi_h.harness.records import ArtifactRecord
from roi_h.harness.workspace import Workspace


def reconcile_run(
    runtime: Runtime,
    workspace: Workspace,
    *,
    repair: bool = False,
) -> ReconciliationReport:
    """Compare graph state with files and optionally apply unambiguous repairs."""
    issues: list[ReconciliationIssue] = []
    artifact_root = workspace.artifacts / runtime.run_id
    disk_artifacts = _disk_artifacts(artifact_root)
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
        metadata = disk_artifacts.get(name)
        if metadata is None:
            issues.append(
                _issue(
                    "artifact_missing_file",
                    "error",
                    f"graph artifact {name!r} has no corresponding file",
                    object_id=obj.id,
                    path=str(artifact_root / name),
                )
            )
            continue
        expected = {
            "path": metadata["path"],
            "bytes": metadata["bytes"],
            "sha256": metadata["sha256"],
        }
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
                    path=metadata["path"],
                    repaired=repaired,
                    details={"graph": actual, "filesystem": expected},
                )
            )

    for name, metadata in sorted(disk_artifacts.items()):
        if name in by_name:
            continue
        repaired = False
        object_id: str | None = None
        if repair:
            record = ArtifactRecord(
                run_id=runtime.run_id,
                name=name,
                path=metadata["path"],
                bytes=metadata["bytes"],
                sha256=metadata["sha256"],
            )
            object_id = runtime.graph.add_object("rpa.artifact", record.to_graph()).id
            repaired = True
        issues.append(
            _issue(
                "orphan_artifact_file",
                "warning",
                f"artifact file {name!r} has no graph record",
                object_id=object_id,
                path=metadata["path"],
                repaired=repaired,
            )
        )

    phases = [obj for obj in phase_objects(runtime) if obj.data.get("run_id") == runtime.run_id]
    expected_packages: set[Path] = set()
    for phase in phases:
        expected = (
            artifact_root
            / "phases"
            / f"{int(phase.data.get('index') or 0):02d}-{phase.data.get('name') or ''}"
        ).resolve()
        expected_packages.add(expected)
        _reconcile_phase(
            runtime,
            workspace,
            phase,
            expected,
            issues,
            repair=repair,
        )

    phases_dir = artifact_root / "phases"
    if phases_dir.is_dir():
        for child in sorted(phases_dir.iterdir()):
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
    ok = not any(not issue.repaired for issue in issues)
    return ReconciliationReport(
        run_id=runtime.run_id,
        repair_requested=repair,
        ok=ok,
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
    needs_handoff = status == "done" or bool(artifact_names) or bool(data.get("handoff_path"))
    if not needs_handoff:
        return

    manifest_path = expected / "manifest.json"
    if not manifest_path.is_file():
        missing = [
            name
            for name in artifact_names
            if not (workspace.artifacts / runtime.run_id / name).is_file()
        ]
        repaired = False
        if repair and not missing and status in {"done", "failed", "skipped"}:
            handoff = phase_store.write_handoff(
                workspace.artifacts,
                run_id=runtime.run_id,
                index=int(data.get("index") or 0),
                name=str(data.get("name") or ""),
                phase_id=phase.id,
                status=status,
                artifact_paths=[
                    workspace.artifacts / runtime.run_id / name for name in artifact_names
                ],
                summary=dict(data.get("summary") or {}),
                require_artifacts=list(data.get("require_artifacts") or []),
                source_run_id=data.get("source_run_id"),
            )
            runtime.graph.patch_object(phase.id, {"handoff_path": handoff["handoff_path"]})
            repaired = True
        issues.append(
            _issue(
                "phase_handoff_missing",
                "error",
                f"phase {data.get('name')!r} has no handoff manifest",
                object_id=phase.id,
                path=str(manifest_path),
                repaired=repaired,
                details={"missing_source_artifacts": missing},
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
                details={
                    "manifest_phase_id": manifest.phase_id,
                    "manifest_run_id": manifest.run_id,
                    "manifest_phase": manifest.phase,
                    "manifest_index": manifest.index,
                },
            )
        )
        return

    root = workspace.artifacts / runtime.run_id
    missing_files = [name for name in manifest.artifacts if not (package / name).is_file()]
    content_drift: dict[str, Any] = {}
    if missing_files:
        content_drift["missing_package_artifacts"] = missing_files
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
    changed_copies = [
        name
        for name in artifact_names
        if (root / name).is_file()
        and (package / name).is_file()
        and _sha256(root / name) != _sha256(package / name)
    ]
    if changed_copies:
        content_drift["changed_artifacts"] = changed_copies
    missing_sources = [name for name in artifact_names if not (root / name).is_file()]
    if content_drift:
        repaired = False
        if repair and not missing_sources and status in {"done", "failed", "skipped"}:
            phase_store.write_handoff(
                workspace.artifacts,
                run_id=runtime.run_id,
                index=int(data.get("index") or 0),
                name=str(data.get("name") or ""),
                phase_id=phase.id,
                status=status,
                artifact_paths=[root / name for name in artifact_names],
                summary=dict(data.get("summary") or {}),
                require_artifacts=list(data.get("require_artifacts") or []),
                source_run_id=data.get("source_run_id"),
            )
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

    graph_path = str(data.get("handoff_path") or "")
    expected_path = str(package.resolve())
    if graph_path != expected_path:
        repaired = False
        if repair:
            runtime.graph.patch_object(phase.id, {"handoff_path": expected_path})
            repaired = True
        issues.append(
            _issue(
                "phase_handoff_path_mismatch",
                "error",
                f"phase {data.get('name')!r} graph handoff path is stale or missing",
                object_id=phase.id,
                path=expected_path,
                repaired=repaired,
                details={"graph_path": graph_path},
            )
        )


def _disk_artifacts(root: Path) -> dict[str, dict[str, Any]]:
    if not root.is_dir():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        result[path.name] = {
            "path": str(path.resolve()),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
