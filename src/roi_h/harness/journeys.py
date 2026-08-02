"""Development, shipping, and production journeys for modular automations."""

from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from roi_h.harness.activegraph_runtime import ROIHRuntime
from roi_h.harness.automation import load_automation, publish_manifest, push_to_prod
from roi_h.harness.automation_runner import run_source
from roi_h.harness.automation_source import source_lease, source_tree_digest
from roi_h.harness.lease import run_lease
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.workspace import Workspace

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_run_id(run_id: str) -> None:
    """Validate a public durable run identity."""
    if not _RUN_ID_RE.fullmatch(run_id):
        msg = (
            "run id must be 1-128 characters, start with an alphanumeric character, "
            "and contain only letters, digits, '.', '_', or '-'"
        )
        raise ValueError(msg)


def new_run_id(goal: str) -> str:
    """Create a readable unique run identity."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", goal.strip().lower())[:40].strip("-") or "run"
    return f"{slug}-{uuid.uuid4().hex[:8]}"


def run_development_source(
    workspace: Workspace,
    *,
    name: str,
    run_id: str | None = None,
    goal: str = "",
    actor: str = "ai",
    inputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Freeze and run one editable project source in development."""
    if workspace.env != "dev":
        msg = "editable automation sources can run only in development"
        raise ValueError(msg)
    resolved_run_id = run_id or new_run_id(goal or name)
    validate_run_id(resolved_run_id)
    source_root = workspace.automation_sources / name
    with (
        _staged_inputs(workspace, inputs or {}) as staged_inputs,
        run_lease(workspace, resolved_run_id),
    ):
        RunStorage(workspace).reserve(resolved_run_id)
        _materialize_staged_inputs(workspace, resolved_run_id, staged_inputs)
        with source_lease(workspace.automation_sources, name):
            result = run_source(
                workspace,
                source_root,
                run_id=resolved_run_id,
                goal=goal,
                actor=actor,
                lease_held=True,
                run_reserved=True,
            )
    return _public_result(result)


def ship_automation(
    workspace: Workspace,
    *,
    name: str,
    version: str,
    from_run: str,
    goal: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Ship only the exact source snapshot from a completed verified dev run."""
    if workspace.env != "dev":
        msg = "automation shipping must use the development environment"
        raise ValueError(msg)
    validate_run_id(from_run)
    evidence = _verified_run_evidence(workspace, from_run, name)
    source_root = RunStorage(workspace).paths(from_run).work / "source"
    observed_digest, _ = source_tree_digest(source_root)
    if observed_digest != evidence["source_digest"]:
        msg = "the frozen run source does not match its ActiveGraph evidence"
        raise ValueError(msg)
    published = publish_manifest(
        workspace,
        name=name,
        version=version,
        source_root=source_root,
        source_run_id=from_run,
        expected_source_digest=observed_digest,
        goal=goal,
        notes=notes,
    )
    promoted = push_to_prod(
        root=workspace.root,
        project=workspace.project,
        name=name,
        version=version,
    )
    return {
        "ok": True,
        "shipped": True,
        "name": name,
        "version": version,
        "source_run_id": from_run,
        "source_digest": observed_digest,
        "package_digest": published["package_digest"],
        "publish": published,
        "promotion": promoted,
    }


def run_automation(
    workspace: Workspace,
    *,
    name: str,
    version: str | None = None,
    run_id: str | None = None,
    actor: str = "automation",
    inputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute one selected immutable package in production."""
    if workspace.env != "prod":
        msg = "immutable automation packages run only in production"
        raise ValueError(msg)
    package = load_automation(workspace, name, version=version)
    resolved_run_id = run_id or new_run_id(name)
    validate_run_id(resolved_run_id)
    manifest = package["manifest"]
    with (
        _staged_inputs(workspace, inputs or {}) as staged_inputs,
        run_lease(workspace, resolved_run_id),
    ):
        RunStorage(workspace).reserve(resolved_run_id)
        _materialize_staged_inputs(workspace, resolved_run_id, staged_inputs)
        result = run_source(
            workspace,
            package["source_root"],
            run_id=resolved_run_id,
            goal=str(manifest.get("goal") or name),
            actor=actor,
            automation_version=str(package["version"]),
            package_digest=str(package["package_digest"]),
            expected_source_digest=str(package["source_digest"]),
            lease_held=True,
            run_reserved=True,
        )
    public = _public_result(result)
    public["automation"] = {
        "name": name,
        "version": package["version"],
        "package_digest": package["package_digest"],
        "source_digest": package["source_digest"],
    }
    return public


def _verified_run_evidence(workspace: Workspace, run_id: str, name: str) -> dict[str, Any]:
    try:
        runtime = ROIHRuntime.load(str(workspace.db), run_id=run_id)
    except Exception as exc:
        msg = f"development run not found: {run_id}"
        raise FileNotFoundError(msg) from exc
    runs = list(runtime.graph.objects(type="rpa.run"))
    if len(runs) != 1:
        msg = f"development run has invalid run evidence: {run_id}"
        raise ValueError(msg)
    run = runs[0]
    if run.data.get("status") != "completed" or run.data.get("automation_name") != name:
        msg = f"development run did not complete automation {name!r}: {run_id}"
        raise ValueError(msg)
    verify_phases = [
        phase
        for phase in runtime.graph.objects(type="rpa.phase")
        if phase.data.get("role") == "verify"
    ]
    if not verify_phases or any(phase.data.get("status") != "done" for phase in verify_phases):
        msg = f"development run has no successful verification phase: {run_id}"
        raise ValueError(msg)
    completed = [event for event in runtime.graph.events if event.type == "run.completed"]
    if not completed or completed[-1].payload.get("verification_ok") is not True:
        msg = f"development run has no successful completion event: {run_id}"
        raise ValueError(msg)
    return {
        "source_digest": str(run.data.get("source_digest") or ""),
        "run_object_id": run.id,
    }


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in result.items() if key != "runtime"}
    for phase in public.get("phases", {}).values():
        for artifact in phase.get("artifacts", {}).values():
            artifact.pop("physical_path", None)
            artifact.pop("path", None)
    return public


@contextmanager
def _staged_inputs(workspace: Workspace, inputs: dict[str, str]) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=".inputs-", dir=workspace.runs) as directory:
        root = Path(directory)
        _copy_inputs(root, inputs)
        yield root


def _materialize_staged_inputs(workspace: Workspace, run_id: str, staged_inputs: Path) -> None:
    target = RunStorage(workspace).paths(run_id).input
    shutil.copytree(staged_inputs, target, dirs_exist_ok=True)


def _copy_inputs(
    root: Path,
    inputs: dict[str, str],
) -> None:
    for raw_name, raw_source in inputs.items():
        relative = PurePosixPath(raw_name.replace("\\", "/"))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            msg = f"invalid automation input name: {raw_name!r}"
            raise ValueError(msg)
        source = Path(raw_source).expanduser().resolve()
        if source.is_symlink() or not source.is_file():
            msg = f"automation input is not a regular file: {raw_source}"
            raise FileNotFoundError(msg)
        target = root.joinpath(*relative.parts).resolve()
        if not target.is_relative_to(root):
            msg = f"automation input escapes the run input directory: {raw_name!r}"
            raise ValueError(msg)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.input-{uuid.uuid4().hex}")
        try:
            shutil.copyfile(source, staging)
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)


__all__ = [
    "new_run_id",
    "run_automation",
    "run_development_source",
    "ship_automation",
    "validate_run_id",
]
