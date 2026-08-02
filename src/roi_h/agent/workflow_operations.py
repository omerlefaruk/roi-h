"""Remaining product workflow adapters for the public operation catalog."""

# ruff: noqa: D103

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from roi_h import __version__
from roi_h.agent.contract import DestructivePlan
from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.automation_source import put_source
from roi_h.harness.control import request_cancellation
from roi_h.harness.diagnostics import DiagnosticSink
from roi_h.harness.journeys import run_automation, run_development_source, ship_automation
from roi_h.harness.logical_paths import LogicalPath, PathResolver, PathScope
from roi_h.harness.project_archive import ProjectArchive
from roi_h.harness.records import evidenced_artifacts
from roi_h.harness.retention import RetentionPlanner
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.runtime_environment import (
    RuntimeBootstrapReport,
    inspect_isolated_runtime_bootstrap,
)
from roi_h.harness.secrets import delete_secret, set_secret
from roi_h.harness.store_lifecycle import StoreLifecycle
from roi_h.harness.workspace import (
    Workspace,
    rename_project,
    resolve_home,
    set_active_env,
    set_active_project,
)

if TYPE_CHECKING:
    from roi_h.agent.contract import CommandRequest


def system_doctor(request: CommandRequest) -> dict[str, Any]:
    """Check the selected home and project without changing state."""
    home = resolve_home(request.arguments.get("home"))
    runtime = inspect_isolated_runtime_bootstrap()
    runtime_results = {check.code: check.ok for check in runtime.checks}
    runtime_checks = {
        "runtime_socket_bootstrap": runtime_results.get("runtime.socket_bootstrap", False),
        "runtime_tls_bootstrap": runtime_results.get("runtime.tls_bootstrap", False),
    }
    result: dict[str, Any] = {
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "home_initialized": (home / "config.json").is_file(),
        "project": None,
        "checks": runtime_checks,
        "runtime": runtime.to_dict(),
        "errors": [name for name, passed in runtime_checks.items() if passed is False],
    }
    try:
        workspace = _workspace(request)
    except (FileNotFoundError, ValueError):
        result["errors"] = [*result["errors"], "No selected project is available."]
        return result
    checks = _project_checks(workspace, runtime)
    result.update(
        {
            "project": workspace.project,
            "environment": workspace.env,
            "checks": checks,
            "runtime": runtime.to_dict(),
            "errors": [name for name, passed in checks.items() if passed is False],
        }
    )
    return result


def project_use(request: CommandRequest) -> dict[str, Any]:
    return _safe(set_active_project(request.arguments.get("home"), _name(request)))


def project_doctor(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    runtime = inspect_isolated_runtime_bootstrap()
    checks = _project_checks(workspace, runtime)
    store = None
    if workspace.db.is_file():
        store = (
            StoreLifecycle()
            .check(
                workspace,
                "full" if request.arguments.get("full") is True else "quick",
            )
            .to_dict()
        )
    errors = [name for name, passed in checks.items() if passed is False]
    if store is not None and not store["ok"]:
        errors.append("store")
    return {
        "project": workspace.project,
        "environment": workspace.env,
        "checks": checks,
        "runtime": runtime.to_dict(),
        "store": _safe(store),
        "errors": errors,
    }


def environment_doctor(request: CommandRequest) -> dict[str, Any]:
    """Inspect the selected environment and its isolated runtime."""
    workspace = _workspace(request)
    runtime = inspect_isolated_runtime_bootstrap()
    checks = _project_checks(workspace, runtime)
    return {
        "project": workspace.project,
        "environment": workspace.env,
        "checks": checks,
        "runtime": runtime.to_dict(),
        "errors": [name for name, passed in checks.items() if passed is False],
    }


def project_export(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    result = (
        ProjectArchive()
        .export(
            workspace,
            _required_string(request, "output"),
            mode=cast("Any", request.arguments.get("mode") or "full"),
        )
        .to_dict()
    )
    result.pop("path", None)
    return _safe(result)


def project_import_verify(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        ProjectArchive()
        .import_archive(
            _required_string(request, "source"),
            resolve_home(request.arguments.get("home")),
            name=_optional_string(request, "name"),
            verify_only=True,
        )
        .to_dict()
    )


def project_import(request: CommandRequest) -> dict[str, Any]:
    home = resolve_home(request.arguments.get("home"))
    plan_id = _optional_string(request, "plan_id")
    if plan_id is not None:
        return _project_import_replace_apply(home, plan_id)
    source = Path(_required_string(request, "source")).expanduser().resolve()
    inspection = ProjectArchive().inspect(source)
    name = _optional_string(request, "name") or inspection.slug
    target = home / "projects" / name
    if target.is_dir():
        return _project_import_replace_plan(home, source, name, target)
    result = (
        ProjectArchive()
        .import_archive(
            source,
            home,
            name=name,
            use=request.arguments.get("use") is True,
        )
        .to_dict()
    )
    result.pop("path", None)
    return _safe(result)


def _project_import_replace_plan(
    home: Path,
    source: Path,
    name: str,
    target: Path,
) -> dict[str, Any]:
    plan = DestructivePlan(
        plan_id=f"plan_{uuid4().hex}",
        operation="project.import.replace",
        arguments={
            "name": name,
            "source": str(source),
            "source_digest": _path_digest(source),
        },
        effects=[
            {
                "action": "replace_project",
                "project": name,
                "previous_project": "move_to_recoverable_trash",
            }
        ],
        state_digest=_path_digest(target),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        apply_operation="project.import",
    )
    atomic_write_json(
        _home_plan_path(home, plan.plan_id),
        plan.model_dump(mode="json"),
        mode=0o600,
    )
    public = plan.model_dump(mode="json")
    public["arguments"] = {
        "name": name,
        "source_digest": plan.arguments["source_digest"],
    }
    return public


def _project_import_replace_apply(home: Path, plan_id: str) -> dict[str, Any]:
    path = _home_plan_path(home, plan_id)
    if not path.is_file():
        msg = f"plan not found: {plan_id}"
        raise FileNotFoundError(msg)
    plan = DestructivePlan.model_validate_json(path.read_text(encoding="utf-8"))
    if plan.apply_operation != "project.import":
        _state_changed()
    if plan.expires_at < datetime.now(UTC):
        msg = "plan.expired: the plan expired"
        raise RuntimeError(msg)
    name = str(plan.arguments["name"])
    source = Path(str(plan.arguments["source"]))
    target = home / "projects" / name
    if (
        not target.is_dir()
        or _path_digest(target) != plan.state_digest
        or _path_digest(source) != plan.arguments["source_digest"]
    ):
        _state_changed()
    trash = home / "trash" / "projects"
    trash.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous = trash / f"{name}-{uuid4().hex}"
    target.replace(previous)
    try:
        imported = (
            ProjectArchive()
            .import_archive(
                source,
                home,
                name=name,
                use=False,
            )
            .to_dict()
        )
    except Exception:
        if not target.exists():
            previous.replace(target)
        raise
    path.unlink(missing_ok=True)
    imported.pop("path", None)
    return {
        **_safe(imported),
        "replaced": True,
        "recoverable_previous_project": True,
    }


def project_rename(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        rename_project(
            request.arguments.get("home"),
            _name(request),
            _required_string(request, "new_name"),
        )
    )


def environment_set(request: CommandRequest) -> dict[str, Any]:
    environment = _required_string(request, "environment")
    return _safe(
        set_active_env(
            request.arguments.get("home"),
            environment,
            project=request.context.project or request.arguments.get("project"),
        )
    )


def run_cancel(request: CommandRequest) -> dict[str, Any]:
    return request_cancellation(
        _workspace(request),
        _run_id(request),
        reason=str(request.arguments.get("reason") or "operator requested cancellation"),
    )


def run_input_add(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    run_id = _run_id(request)
    paths = RunStorage(workspace).prepare(run_id)
    source_run_id = _optional_string(request, "from_run")
    source_path = _optional_string(request, "source_path")
    if source_run_id is not None and source_path is not None:
        source_logical = LogicalPath.parse(source_path)
        if source_logical.scheme not in {"run", "artifact"}:
            msg = "source_path must be a run or artifact logical path"
            raise ValueError(msg)
        source = (
            PathResolver()
            .resolve(
                source_logical,
                PathScope(workspace, run_id=source_run_id),
                "read",
            )
            .physical
        )
    else:
        source = Path(_required_string(request, "source")).expanduser().resolve()
    if source.is_symlink() or not source.is_file():
        msg = "run input source is not a regular file"
        raise FileNotFoundError(msg)
    logical = LogicalPath.parse(f"run://input/{_required_string(request, 'name')}")
    destination = (
        PathResolver()
        .resolve(
            logical,
            PathScope(workspace, run_id=run_id),
            "create",
        )
        .physical
    )
    if destination.exists():
        msg = f"run input already exists: {logical}"
        raise FileExistsError(msg)
    staging = paths.input / f".{destination.name}.input"
    try:
        shutil.copyfile(source, staging)
        staging.chmod(0o600)
        staging.replace(destination)
    finally:
        staging.unlink(missing_ok=True)
    result = {"run_id": run_id, "path": str(logical), "bytes": destination.stat().st_size}
    if source_run_id is not None:
        result.update(source_run_id=source_run_id, source_path=source_path)
    return result


def run_files(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    run_id = _run_id(request)
    paths = RunStorage(workspace).paths(run_id)
    files: list[dict[str, Any]] = []
    for root_name, root in (
        ("input", paths.input),
        ("work", paths.work),
        ("output", paths.output),
        ("tmp", paths.tmp),
    ):
        if root.is_dir():
            files.extend(
                {
                    "path": f"run://{root_name}/{path.relative_to(root).as_posix()}",
                    "bytes": path.stat().st_size,
                }
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
    artifacts = [_safe(item.to_dict()) for item in evidenced_artifacts(workspace, run_id)]
    return {"run_id": run_id, "files": files, "artifacts": artifacts}


def artifact_export(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    run_filter = request.context.run_id or request.arguments.get("run_id")
    artifact_id = _required_string(request, "artifact_id")
    run_ids = (
        [str(run_filter)]
        if run_filter is not None
        else [
            run_dir.name
            for run_dir in workspace.runs.iterdir()
            if run_dir.is_dir() and not run_dir.name.startswith(".")
        ]
    )
    matches: list[tuple[str, Any]] = []
    for run_id in run_ids:
        try:
            items = evidenced_artifacts(workspace, run_id)
        except FileNotFoundError:
            if run_filter is not None:
                raise
            continue
        matches.extend((run_id, item) for item in items if item.artifact_id == artifact_id)
    if len(matches) != 1:
        msg = f"artifact.file_missing: expected one match, got {len(matches)}"
        raise FileNotFoundError(msg)
    run_id, attachment = matches[0]
    target = Path(_required_string(request, "output")).expanduser().resolve()
    if target.exists():
        msg = "artifact export destination exists"
        raise FileExistsError(msg)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.export")
    try:
        shutil.copyfile(attachment.path, staging)
        staging.chmod(0o600)
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)
    return {
        "artifact_id": attachment.artifact_id,
        "uri": attachment.uri,
        "run_id": run_id,
        "bytes": attachment.bytes,
        "sha256": attachment.sha256,
    }


def automation_ship(request: CommandRequest) -> dict[str, Any]:
    result = ship_automation(
        _workspace(request),
        name=_name(request),
        version=_required_string(request, "version"),
        from_run=_required_string(request, "from_run"),
        goal=str(request.arguments.get("goal") or ""),
        notes=str(request.arguments.get("notes") or ""),
    )
    return _strip_physical_paths(result)


def automation_run(request: CommandRequest) -> dict[str, Any]:
    return _strip_physical_paths(
        run_automation(
            _workspace(request),
            name=_name(request),
            version=_optional_string(request, "version"),
            run_id=_optional_string(request, "run_id"),
            actor=str(request.arguments.get("actor") or "agent"),
            inputs=_string_mapping(request.arguments.get("inputs")),
        )
    )


def automation_source_put(request: CommandRequest) -> dict[str, Any]:
    manifest = request.arguments.get("manifest")
    files = request.arguments.get("files")
    if not isinstance(manifest, dict):
        msg = "manifest must be an object"
        raise TypeError(msg)
    if not isinstance(files, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in files.items()
    ):
        msg = "files must be an object of relative paths to text content"
        raise TypeError(msg)
    workspace = _workspace(request)
    if workspace.env != "dev":
        msg = "editable automation sources can change only in development"
        raise ValueError(msg)
    snapshot = put_source(
        workspace.automation_sources,
        _name(request),
        cast("dict[str, Any]", manifest),
        cast("dict[str, str]", files),
    )
    return {
        "ok": True,
        "name": snapshot.manifest.name,
        "source_digest": snapshot.source_digest,
        "files": snapshot.files,
        "phase_plan": [
            phase.model_dump(mode="json", by_alias=True) for phase in snapshot.manifest.phases
        ],
    }


def automation_dev_run(request: CommandRequest) -> dict[str, Any]:
    return run_development_source(
        _workspace(request),
        name=_name(request),
        run_id=_optional_string(request, "run_id"),
        goal=str(request.arguments.get("goal") or ""),
        actor=str(request.arguments.get("actor") or "ai"),
        inputs=_string_mapping(request.arguments.get("inputs")),
    )


def secret_set_operation(request: CommandRequest) -> dict[str, Any]:
    value = request.arguments.get("secret_value")
    if not isinstance(value, str):
        msg = "secret_value is required through the secure input channel"
        raise TypeError(msg)
    return _safe(set_secret(_workspace(request), _name(request), value))


def secret_delete_operation(request: CommandRequest) -> dict[str, Any]:
    return _safe(delete_secret(_workspace(request), _name(request)))


def retention_plan(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        RetentionPlanner()
        .plan(_workspace(request), _mapping(request.arguments.get("policy")))
        .to_dict()
    )


def retention_apply(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        RetentionPlanner()
        .apply(_workspace(request), _required_string(request, "plan_id"))
        .to_dict()
    )


def store_restore_plan(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    source = Path(_required_string(request, "source")).expanduser().resolve()
    if not source.is_file() or source.is_symlink():
        msg = "store restore source is not a regular file"
        raise FileNotFoundError(msg)
    plan = _new_plan(
        workspace,
        operation="store.restore",
        arguments={"source": str(source), "source_digest": _path_digest(source)},
        effects=[{"action": "replace_active_store", "environment": workspace.env}],
        state_digest=_path_digest(workspace.db),
        apply_operation="store.restore.apply",
    )
    return plan.model_dump(mode="json")


def store_restore_apply(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    plan = _checked_plan(request, workspace, "store.restore.apply")
    source = Path(str(plan.arguments["source"]))
    if (
        _path_digest(workspace.db) != plan.state_digest
        or _path_digest(source) != plan.arguments["source_digest"]
    ):
        _state_changed()
    result = StoreLifecycle().restore(workspace, source).to_dict()
    _plan_path(workspace, plan.plan_id).unlink(missing_ok=True)
    return _safe(result)


def support_bundle_create(request: CommandRequest) -> dict[str, Any]:
    home = resolve_home(request.arguments.get("home"))
    target = Path(_required_string(request, "output")).expanduser().resolve()
    if target.exists():
        msg = "support bundle destination exists"
        raise FileExistsError(msg)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    diagnostics = DiagnosticSink(home).read(
        limit=min(int(request.arguments.get("limit") or 200), 200)
    )
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "roi_h_version": __version__,
        "doctor": system_doctor(request),
        "diagnostics": diagnostics,
    }
    staging = target.with_name(f".{target.name}.support")
    try:
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "support.json",
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
            )
        staging.chmod(0o600)
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)
    return {
        "created": True,
        "bytes": target.stat().st_size,
        "files": ["support.json"],
    }


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


def _name(request: CommandRequest) -> str:
    return _required_string(request, "name")


def _required_string(request: CommandRequest, key: str) -> str:
    value = request.arguments.get(key)
    if not isinstance(value, str) or not value:
        msg = f"{key} is required"
        raise ValueError(msg)
    return value


def _optional_string(request: CommandRequest, key: str) -> str | None:
    value = request.arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{key} must be a string"
        raise TypeError(msg)
    return value


def _mapping(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        msg = "value must be an object"
        raise TypeError(msg)
    return cast("dict[str, Any]", value)


def _string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = "value must be an array of strings"
        raise TypeError(msg)
    return cast("list[str]", value)


def _string_mapping(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        msg = "value must be an object of string keys and values"
        raise TypeError(msg)
    return cast("dict[str, str]", value)


def _project_checks(
    workspace: Workspace,
    runtime: RuntimeBootstrapReport | None = None,
) -> dict[str, bool]:
    runtime_report = runtime or inspect_isolated_runtime_bootstrap()
    runtime_results = {check.code: check.ok for check in runtime_report.checks}
    return {
        "project_manifest": workspace.config_path.is_file(),
        "environment_manifest": workspace.environment_config_path.is_file(),
        "plaintext_secrets_absent": not (workspace.project_root / "secrets.json").exists(),
        "paths_contained": all(
            path.is_relative_to(workspace.project_root)
            for path in (
                workspace.project_skills,
                workspace.automation_sources,
                workspace.automations,
                workspace.channels,
                workspace.reference,
                workspace.environment_root,
            )
        ),
        "runtime_socket_bootstrap": runtime_results.get("runtime.socket_bootstrap", False),
        "runtime_tls_bootstrap": runtime_results.get("runtime.tls_bootstrap", False),
    }


def _new_plan(  # noqa: PLR0913 - plan fields stay explicit at the safety boundary.
    workspace: Workspace,
    *,
    operation: str,
    arguments: dict[str, Any],
    effects: list[dict[str, Any]],
    state_digest: str,
    apply_operation: str,
    blockers: list[dict[str, Any]] | None = None,
) -> DestructivePlan:
    plan = DestructivePlan(
        plan_id=f"plan_{uuid4().hex}",
        operation=operation,
        arguments=arguments,
        effects=effects,
        blockers=blockers or [],
        state_digest=state_digest,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        apply_operation=apply_operation,
    )
    atomic_write_json(
        _plan_path(workspace, plan.plan_id),
        plan.model_dump(mode="json"),
        mode=0o600,
    )
    return plan


def _checked_plan(
    request: CommandRequest,
    workspace: Workspace,
    operation: str,
) -> DestructivePlan:
    plan_id = _required_string(request, "plan_id")
    path = _plan_path(workspace, plan_id)
    if not path.is_file():
        msg = f"plan not found: {plan_id}"
        raise FileNotFoundError(msg)
    plan = DestructivePlan.model_validate_json(path.read_text(encoding="utf-8"))
    if plan.apply_operation != operation:
        _state_changed()
    if plan.expires_at < datetime.now(UTC):
        msg = "plan.expired: the plan expired"
        raise RuntimeError(msg)
    return plan


def _plan_path(workspace: Workspace, plan_id: str) -> Path:
    if not plan_id.startswith("plan_") or not plan_id.removeprefix("plan_").isalnum():
        msg = "invalid plan ID"
        raise ValueError(msg)
    return workspace.runtime / "agent-plans" / f"{plan_id}.json"


def _home_plan_path(home: Path, plan_id: str) -> Path:
    if not plan_id.startswith("plan_") or not plan_id.removeprefix("plan_").isalnum():
        msg = "invalid plan ID"
        raise ValueError(msg)
    return home / "runtime" / "agent-plans" / f"{plan_id}.json"


def _path_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        digest.update(b"missing")
    elif path.is_file() and not path.is_symlink():
        digest.update(b"file\0")
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    elif path.is_dir() and not path.is_symlink():
        for item in sorted(path.rglob("*")):
            relative = item.relative_to(path).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            if item.is_file() and not item.is_symlink():
                with item.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
    else:
        digest.update(b"unsafe")
    return f"sha256:{digest.hexdigest()}"


def _state_changed() -> None:
    msg = "plan.state_changed: state changed after this plan was created"
    raise RuntimeError(msg)


def _strip_physical_paths(value: object) -> dict[str, Any]:
    safe = _safe(value)
    if not isinstance(safe, dict):
        return {"value": safe}
    for key in tuple(safe):
        if key in {
            "path",
            "project_root",
            "global_root",
            "shared_root",
            "copied",
            "distill_path",
            "skills_dir",
        }:
            safe.pop(key, None)
    return safe


def _safe[T](value: T) -> T:
    if isinstance(value, Path):
        return cast("T", value.name)
    if isinstance(value, dict):
        return cast("T", {str(key): _safe(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return cast("T", [_safe(item) for item in value])
    return value


__all__ = [
    "artifact_export",
    "automation_dev_run",
    "automation_run",
    "automation_ship",
    "automation_source_put",
    "environment_set",
    "project_doctor",
    "project_export",
    "project_import",
    "project_import_verify",
    "project_rename",
    "project_use",
    "retention_apply",
    "retention_plan",
    "run_cancel",
    "run_files",
    "run_input_add",
    "secret_delete_operation",
    "secret_set_operation",
    "store_restore_apply",
    "store_restore_plan",
    "support_bundle_create",
    "system_doctor",
]
