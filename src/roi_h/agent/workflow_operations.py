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
from roi_h.harness.custom import define_project_tool, promote_to_global
from roi_h.harness.diagnostics import DiagnosticSink
from roi_h.harness.journeys import run_automation, ship_automation
from roi_h.harness.logical_paths import LogicalPath, PathResolver, PathScope
from roi_h.harness.project_archive import ProjectArchive
from roi_h.harness.retention import RetentionPlanner
from roi_h.harness.run_storage import RunStorage
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
    from roi_h.harness.application import RunSession


def system_doctor(request: CommandRequest) -> dict[str, Any]:
    """Check the selected home and project without changing state."""
    home = resolve_home(request.arguments.get("home"))
    result: dict[str, Any] = {
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "home_initialized": (home / "home.json").is_file(),
        "project": None,
        "checks": {},
        "errors": [],
    }
    try:
        workspace = _workspace(request)
    except (FileNotFoundError, ValueError):
        result["errors"] = ["No selected project is available."]
        return result
    checks = _project_checks(workspace)
    result.update(
        {
            "project": workspace.project,
            "environment": workspace.env,
            "checks": checks,
            "errors": [name for name, passed in checks.items() if passed is False],
        }
    )
    return result


def project_use(request: CommandRequest) -> dict[str, Any]:
    return _safe(set_active_project(request.arguments.get("home"), _name(request)))


def project_doctor(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    checks = _project_checks(workspace)
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
        "store": _safe(store),
        "errors": errors,
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
    return _session(request).cancel(
        reason=str(request.arguments.get("reason") or "operator requested cancellation")
    )


def run_reconcile(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        _session(request)
        .reconcile(repair=request.arguments.get("repair") is True)
        .model_dump(mode="json")
    )


def run_input_add(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    run_id = _run_id(request)
    paths = RunStorage(workspace).prepare(run_id)
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
    return {"run_id": run_id, "path": str(logical), "bytes": destination.stat().st_size}


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
    artifacts = [_safe(item.to_dict()) for item in RunStorage(workspace).list(run_id)]
    return {"run_id": run_id, "files": files, "artifacts": artifacts}


def phase_list(request: CommandRequest) -> dict[str, Any]:
    items = _session(request).list_phases()
    return {"run_id": _run_id(request), "items": _safe(items), "count": len(items)}


def phase_begin(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        _session(request).begin_phase(
            _name(request),
            description=str(request.arguments.get("description") or ""),
            require_artifacts=_string_list(request.arguments.get("require_artifacts")),
        )
    )


def phase_end(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        _session(request).end_phase(
            summary=_mapping(request.arguments.get("summary")),
            require_artifacts=_string_list(request.arguments.get("require_artifacts")),
        )
    )


def phase_fail(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        _session(request).fail_phase(
            error=_required_string(request, "error"),
            summary=_mapping(request.arguments.get("summary")),
        )
    )


def phase_skip(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        _session(request).skip_phase(
            _name(request),
            reason=str(request.arguments.get("reason") or ""),
        )
    )


def phase_retry(request: CommandRequest) -> dict[str, Any]:
    return _safe(
        _session(request).retry_phase(
            _name(request),
            description=str(request.arguments.get("description") or ""),
            require_artifacts=_string_list(request.arguments.get("require_artifacts")),
        )
    )


def artifact_put(request: CommandRequest) -> dict[str, Any]:
    result = _session(request).put_artifact(
        _required_string(request, "source"),
        name=_optional_string(request, "name"),
    )
    result.pop("path", None)
    return _safe(result)


def artifact_export(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    storage = RunStorage(workspace)
    run_filter = request.context.run_id or request.arguments.get("run_id")
    artifact_id = _required_string(request, "artifact_id")
    matches = [
        (run_dir.name, item)
        for run_dir in workspace.runs.iterdir()
        if run_dir.is_dir() and not run_dir.name.startswith(".")
        for item in storage.list(run_dir.name)
        if item.artifact_id == artifact_id and (run_filter is None or run_dir.name == run_filter)
    ]
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


def skill_define(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    return _safe(
        define_project_tool(
            skill=_required_string(request, "skill"),
            tool=_required_string(request, "tool"),
            description=str(request.arguments.get("description") or ""),
            project_root=workspace.project_skills,
            source=_optional_string(request, "source"),
            source_path=_optional_string(request, "source_path"),
            overwrite=request.arguments.get("overwrite") is True,
        )
    )


def skill_promote(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    result = promote_to_global(
        skill=_name(request),
        tool=_optional_string(request, "tool"),
        project_root=workspace.project_skills,
        global_root=workspace.shared_skills,
        overwrite=request.arguments.get("overwrite") is True,
    )
    return _strip_physical_paths(result)


def skill_delete_plan(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    name = _name(request)
    target = workspace.shared_skills / name
    if not target.is_dir() or not (target / "SKILL.md").is_file():
        msg = f"skill not found: {name}"
        raise FileNotFoundError(msg)
    plan = _new_plan(
        workspace,
        operation="skill.delete",
        arguments={"name": name},
        effects=[{"action": "move_to_recoverable_trash", "skill": name}],
        state_digest=_path_digest(target),
        apply_operation="skill.delete.apply",
    )
    return plan.model_dump(mode="json")


def skill_delete_apply(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    plan = _checked_plan(request, workspace, "skill.delete.apply")
    name = str(plan.arguments["name"])
    target = workspace.shared_skills / name
    if not target.is_dir() or _path_digest(target) != plan.state_digest:
        _state_changed()
    trash = workspace.root / "trash" / "skills"
    trash.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = trash / f"{name}-{uuid4().hex}"
    target.replace(destination)
    _plan_path(workspace, plan.plan_id).unlink(missing_ok=True)
    return {"skill": name, "recoverable": True}


def automation_ship(request: CommandRequest) -> dict[str, Any]:
    result = ship_automation(
        _workspace(request),
        name=_name(request),
        version=_required_string(request, "version"),
        from_run=_required_string(request, "from_run"),
        goal=str(request.arguments.get("goal") or ""),
        notes=str(request.arguments.get("notes") or ""),
        skills=_string_list(request.arguments.get("skills_list")),
        distill=request.arguments.get("distill") is not False,
    )
    return _strip_physical_paths(result)


def automation_run(request: CommandRequest) -> dict[str, Any]:
    return _strip_physical_paths(
        run_automation(
            _workspace(request),
            name=_name(request),
            version=_optional_string(request, "version"),
            run_id=_optional_string(request, "run_id"),
            skills_root=_optional_string(request, "skills"),
            dry_run=request.arguments.get("dry_run") is True,
            from_handoff=_optional_string(request, "from_handoff"),
            auto_approve=request.arguments.get("auto_approve"),
            force=request.arguments.get("force") is not False,
            actor=str(request.arguments.get("actor") or "agent"),
            set_args=_string_list(request.arguments.get("set_args")),
        )
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


def store_migrate_plan(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    plan = _new_plan(
        workspace,
        operation="store.migrate",
        arguments={"target": str(request.arguments.get("target") or "current")},
        effects=[{"action": "migrate_store_schema", "environment": workspace.env}],
        state_digest=_path_digest(workspace.db),
        apply_operation="store.migrate.apply",
    )
    return plan.model_dump(mode="json")


def store_migrate_apply(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    plan = _checked_plan(request, workspace, "store.migrate.apply")
    if _path_digest(workspace.db) != plan.state_digest:
        _state_changed()
    result = (
        StoreLifecycle()
        .migrate(
            workspace,
            target=str(plan.arguments["target"]),
        )
        .to_dict()
    )
    _plan_path(workspace, plan.plan_id).unlink(missing_ok=True)
    return _safe(result)


def store_compact_plan(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    policy = _mapping(request.arguments.get("policy")) or {}
    preview = StoreLifecycle().compact(workspace, policy, apply=False).to_dict()
    plan = _new_plan(
        workspace,
        operation="store.compact",
        arguments={"policy": policy},
        effects=[{"action": "compact_store", "preview": _safe(preview)}],
        blockers=[] if preview.get("enabled") else [{"reason": preview.get("message")}],
        state_digest=_path_digest(workspace.db),
        apply_operation="store.compact.apply",
    )
    return plan.model_dump(mode="json")


def store_compact_apply(request: CommandRequest) -> dict[str, Any]:
    workspace = _workspace(request)
    plan = _checked_plan(request, workspace, "store.compact.apply")
    if plan.blockers:
        msg = "operation.failed: store compaction plan has blockers"
        raise RuntimeError(msg)
    if _path_digest(workspace.db) != plan.state_digest:
        _state_changed()
    result = (
        StoreLifecycle()
        .compact(
            workspace,
            cast("dict[str, Any]", plan.arguments["policy"]),
            apply=True,
        )
        .to_dict()
    )
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
    )


def _session(request: CommandRequest) -> RunSession:
    from roi_h.harness.application import RunSession  # noqa: PLC0415

    return RunSession.reopen(
        _workspace(request),
        run_id=_run_id(request),
        skills_root=request.arguments.get("skills"),
        auto_approve=False,
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


def _project_checks(workspace: Workspace) -> dict[str, bool]:
    return {
        "project_manifest": workspace.config_path.is_file(),
        "environment_manifest": workspace.environment_config_path.is_file(),
        "plaintext_secrets_absent": not (workspace.project_root / "secrets.json").exists(),
        "paths_contained": all(
            path.is_relative_to(workspace.project_root)
            for path in (
                workspace.project_skills,
                workspace.automations,
                workspace.channels,
                workspace.reference,
                workspace.environment_root,
            )
        ),
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
            "recipe_path",
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
    "artifact_put",
    "automation_run",
    "automation_ship",
    "environment_set",
    "phase_begin",
    "phase_end",
    "phase_fail",
    "phase_list",
    "phase_retry",
    "phase_skip",
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
    "run_reconcile",
    "secret_delete_operation",
    "secret_set_operation",
    "skill_define",
    "skill_delete_apply",
    "skill_delete_plan",
    "skill_promote",
    "store_compact_apply",
    "store_compact_plan",
    "store_migrate_apply",
    "store_migrate_plan",
    "store_restore_apply",
    "store_restore_plan",
    "support_bundle_create",
    "system_doctor",
]
