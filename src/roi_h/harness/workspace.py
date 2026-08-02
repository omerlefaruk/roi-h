"""Versioned ROI-H data-home catalog and typed workspace paths."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.lease import project_policy_lease

EnvName = Literal["dev", "prod"]
_VALID_ENVS = frozenset({"dev", "prod"})
_PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_LOG_RETENTION_RE = re.compile(r"^[1-9][0-9]{0,8}d$")
_RESERVED_PROJECT_NAMES = frozenset({"projects", "config", "diagnostics", "cache"})
HOME_LAYOUT_VERSION = 4
PROJECT_SCHEMA_VERSION = 1
ENVIRONMENT_SCHEMA_VERSION = 1
_DEFAULT_PROJECT = "default"

_DEFAULT_RETENTION: dict[str, Any] = {
    "workspace_days_after_success": 7,
    "workspace_days_after_failure": 30,
    "runtime_days": 7,
    "diagnostic_days": 14,
    "log_retention": "7d",
    "artifact_policy": "keep",
    "event_policy": "keep",
}


@dataclass(frozen=True)
class Workspace:
    """Resolved version-4 paths for one project environment."""

    root: Path
    shared_skills: Path
    project: str
    project_id: str
    project_root: Path
    env: EnvName
    environment_root: Path
    db: Path
    project_skills: Path
    artifacts: Path
    runs: Path
    automation_sources: Path
    automations: Path
    channels: Path
    reference: Path
    runtime: Path
    config_path: Path
    environment_config_path: Path
    home_config_path: Path
    layout_version: int = HOME_LAYOUT_VERSION

    @classmethod
    def open(
        cls,
        root: str | Path | None = None,
        *,
        project: str | None = None,
        env: str | None = None,
        db: str | Path | None = None,
    ) -> Workspace:
        """Resolve and validate paths for a named project and environment."""
        base = resolve_home(root)
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        _ensure_home_roots(base)
        home_config_path = base / "config.json"
        _assert_supported_home(home_config_path)
        project_name = _resolve_project(project, home_config_path, base)
        project_root = _project_dir(base, project_name)
        _assert_managed_project_root(base, project_root)
        if not project_root.is_dir():
            msg = (
                f"project {project_name!r} not found under {base / 'projects'}. "
                "Create it with the project.create operation."
            )
            raise FileNotFoundError(msg)

        project_config_path = _project_manifest_path(project_root)
        _assert_safe_child(project_config_path, project_root)
        project_cfg = _read_config(project_config_path)
        if not project_cfg:
            if (project_root / "config.json").is_file():
                msg = (
                    "project.layout_migration_required: version-3 project detected; "
                    "create a new current project with project.create, then recreate "
                    "automation source with automation.source.put"
                )
                raise RuntimeError(msg)
            msg = f"project manifest missing: {project_config_path}"
            raise FileNotFoundError(msg)
        if project_cfg.get("schema_version") != PROJECT_SCHEMA_VERSION:
            msg = f"project manifest schema unsupported: {project_cfg.get('schema_version')!r}"
            raise RuntimeError(msg)
        project_id = str(project_cfg.get("project_id") or "")
        if not project_id.startswith("prj_"):
            msg = f"project manifest has invalid project_id: {project_id!r}"
            raise ValueError(msg)

        active_env = _resolve_env(env, home_config_path, project_name)
        _ensure_project_roots(project_root)
        environment_root = project_root / "environments" / active_env
        environment_config_path = environment_root / "environment.json"

        db_path = (
            Path(db).expanduser()
            if db is not None
            else (environment_root / "store" / "activegraph.sqlite")
        )
        if not db_path.is_absolute():
            db_path = (Path.cwd() / db_path).resolve()
        else:
            db_path = db_path.resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        runs = environment_root / "runs"
        return cls(
            root=base.resolve(),
            shared_skills=(base / "skills").resolve(),
            project=project_name,
            project_id=project_id,
            project_root=project_root.resolve(),
            env=active_env,
            environment_root=environment_root.resolve(),
            db=db_path,
            project_skills=(project_root / "skills").resolve(),
            # Compatibility name: callers pass this root to run-storage helpers.
            artifacts=runs.resolve(),
            runs=runs.resolve(),
            automation_sources=(project_root / "sources" / "automations").resolve(),
            automations=(project_root / "packages" / "automations").resolve(),
            channels=(project_root / "channels" / active_env).resolve(),
            reference=(project_root / "reference").resolve(),
            runtime=(environment_root / "runtime").resolve(),
            config_path=project_config_path.resolve(),
            environment_config_path=environment_config_path.resolve(),
            home_config_path=home_config_path.resolve(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete selected identity and typed path map."""
        return {
            "root": str(self.root),
            "home": str(self.root),
            "layout_version": self.layout_version,
            "shared_skills": str(self.shared_skills),
            "project": self.project,
            "project_id": self.project_id,
            "project_root": str(self.project_root),
            "env": self.env,
            "environment": self.env,
            "environment_root": str(self.environment_root),
            "store_identity": f"{self.project_id}/{self.env}/activegraph-sqlite",
            "db": str(self.db),
            "project_skills": str(self.project_skills),
            "runs": str(self.runs),
            "automation_sources": str(self.automation_sources),
            "artifacts": str(self.artifacts),
            "automations": str(self.automations),
            "channels": str(self.channels),
            "reference": str(self.reference),
            "runtime": str(self.runtime),
            "config_path": str(self.config_path),
            "environment_config_path": str(self.environment_config_path),
            "home_config_path": str(self.home_config_path),
        }


def create_project(
    root: str | Path | None,
    name: str,
    *,
    display_name: str = "",
    set_active: bool = True,
    env: str = "dev",
    log_retention: str = "7d",
) -> dict[str, Any]:
    """Atomically create a version-4 project definition and two environments."""
    validate_project_name(name)
    if env not in _VALID_ENVS:
        msg = f"env must be one of {sorted(_VALID_ENVS)}, got {env!r}"
        raise ValueError(msg)
    log_retention = validate_log_retention(log_retention)

    base = _prepare_home(root)
    project_root = _project_dir(base, name)
    if project_root.exists():
        msg = f"project already exists: {project_root}"
        raise FileExistsError(msg)

    staging = project_root.with_name(f".{name}.create-{uuid.uuid4().hex}")
    created_at = datetime.now(UTC).isoformat()
    project_id = f"prj_{uuid.uuid4().hex}"
    try:
        staging.mkdir(mode=0o700)
        for relative in (
            ".locks",
            "reference",
            "skills",
            "sources/automations",
            "packages/automations",
            "channels/dev",
            "channels/prod",
        ):
            (staging / relative).mkdir(parents=True, mode=0o700)
        for env_name in sorted(_VALID_ENVS):
            env_root = staging / "environments" / env_name
            _ensure_environment_roots(env_root, env_name, containment_root=staging)
        atomic_write_json(
            staging / "project.json",
            {
                "schema_version": PROJECT_SCHEMA_VERSION,
                "project_id": project_id,
                "slug": name,
                "display_name": display_name or name,
                "created_at": created_at,
                "required_secrets": [],
                "retention": {**_DEFAULT_RETENTION, "log_retention": log_retention},
            },
            mode=0o600,
        )
        atomic_write_json(
            staging / "secrets.meta.json",
            {
                "schema_version": 1,
                "provider": (
                    "macos-keychain"
                    if sys.platform == "darwin"
                    else (
                        "windows-credential-manager"
                        if sys.platform == "win32"
                        else "linux-secret-service"
                    )
                ),
                "entries": [],
            },
            mode=0o600,
        )
        staging.replace(project_root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    home_cfg = _read_config(base / "config.json")
    home_cfg = _upgrade_home_config(home_cfg)
    if set_active or not home_cfg.get("active_project"):
        home_cfg["active_project"] = name
    active_envs = dict(home_cfg.get("active_environments") or {})
    active_envs[name] = env
    home_cfg["active_environments"] = active_envs
    _write_config(base / "config.json", home_cfg)

    ws = Workspace.open(base, project=name, env=env)
    return {
        "ok": True,
        "created": True,
        "display_name": display_name or name,
        "created_at": created_at,
        **ws.to_dict(),
    }


def init_project(
    root: str | Path | None = None,
    project: str | None = None,
    *,
    env: str | None = None,
    log_retention: str | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Select and verify an existing managed project without creating one."""
    base = resolve_home(root)
    selected = project or _project_from_cwd(base, cwd)
    validate_project_name(selected)
    project_root = _project_dir(base, selected)
    _assert_managed_project_root(base, project_root)
    if not project_root.is_dir():
        msg = f"project not found: {selected!r}. Create it with: roi-h project create {selected}"
        raise FileNotFoundError(msg)
    _validate_project_manifest(project_root)
    _ensure_project_roots(project_root)
    if log_retention is not None:
        configure_project(base, selected, log_retention=log_retention)

    selected_env = _resolve_env(env, base / "config.json", selected)
    home_cfg = _upgrade_home_config(_read_config(base / "config.json"))
    home_cfg["active_project"] = selected
    active_envs = dict(home_cfg.get("active_environments") or {})
    active_envs[selected] = selected_env
    home_cfg["active_environments"] = active_envs
    _write_config(base / "config.json", home_cfg)
    ws = Workspace.open(base, project=selected, env=selected_env)
    return {
        "ok": True,
        "created": False,
        "initialized": True,
        "project": ws.project,
        "project_id": ws.project_id,
        "environment": ws.env,
        "layout_version": ws.layout_version,
        "structure": project_structure(ws)["entries"],
    }


def init_home(
    root: str | Path | None = None,
    *,
    project: str = _DEFAULT_PROJECT,
    display_name: str = "",
) -> dict[str, Any]:
    """Compatibility wrapper for existing-project initialization."""
    del display_name
    return init_project(root, project)


def configure_project(
    root: str | Path | None,
    project: str,
    *,
    log_retention: str,
) -> dict[str, Any]:
    """Atomically update supported project policy fields."""
    base = resolve_home(root)
    validate_project_name(project)
    project_root = _project_dir(base, project)
    _assert_managed_project_root(base, project_root)
    with project_policy_lease(project_root):
        config = _validate_project_manifest(project_root)
        retention = dict(config.get("retention") or {})
        retention["log_retention"] = validate_log_retention(log_retention)
        config["retention"] = retention
        atomic_write_json(_project_manifest_path(project_root), config, mode=0o600)
    return {
        "ok": True,
        "project": project,
        "log_retention": retention["log_retention"],
    }


def project_structure(workspace: Workspace) -> dict[str, Any]:
    """Return the stable developer tree without absolute storage paths."""
    env = workspace.env
    runs = f"environments/{env}/runs"
    return {
        "view": "logical",
        "project": workspace.project,
        "project_id": workspace.project_id,
        "environment": env,
        "entries": [
            {"path": "config/project.json", "storage": "project.json", "kind": "config"},
            {"path": "reference/", "storage": "reference/", "kind": "reference"},
            {"path": "skills/", "storage": "skills/", "kind": "project-skills"},
            {
                "path": "automations/",
                "storage": "sources/automations/",
                "kind": "automation-sources",
            },
            {
                "path": "packages/",
                "storage": "packages/automations/",
                "kind": "automation-packages",
            },
            {"path": "runs/", "storage": f"{runs}/", "kind": "runs"},
            {
                "path": "runs/<run-id>/run.json",
                "storage": f"{runs}/<run-id>/run-files.json",
                "kind": "run-manifest",
            },
            {
                "path": "runs/<run-id>/input/",
                "storage": f"{runs}/<run-id>/workspace/input/",
                "kind": "run-input",
            },
            {
                "path": "runs/<run-id>/output/",
                "storage": f"{runs}/<run-id>/workspace/output/",
                "kind": "run-output",
            },
            {
                "path": "runs/<run-id>/screenshots/",
                "storage": f"{runs}/<run-id>/artifacts/ (image artifacts)",
                "kind": "screenshots",
            },
            {
                "path": "runs/<run-id>/logs/",
                "storage": f"{runs}/<run-id>/diagnostics/",
                "kind": "run-logs",
            },
        ],
    }


def list_projects(root: str | Path | None = None) -> list[dict[str, Any]]:
    """Inspect projects without opening stores or creating project directories."""
    base = resolve_home(root)
    projects_dir = base / "projects"
    if not projects_dir.is_dir():
        return []
    home_cfg = _read_config(base / "config.json")
    active = home_cfg.get("active_project") or home_cfg.get("project")
    items: list[dict[str, Any]] = []
    candidates = (
        path for path in projects_dir.iterdir() if path.is_dir() and not path.name.startswith(".")
    )
    for path in sorted(candidates):
        manifest_path = _project_manifest_path(path)
        cfg = _read_config(manifest_path)
        legacy = not cfg and (path / "config.json").is_file()
        if legacy:
            cfg = _read_config(path / "config.json")
        name = str(cfg.get("slug") or cfg.get("name") or path.name)
        if not _PROJECT_NAME_RE.fullmatch(name):
            continue
        items.append(
            {
                "name": name,
                "slug": name,
                "project_id": cfg.get("project_id"),
                "display_name": str(cfg.get("display_name") or name),
                "path": str(path.resolve()),
                "active": name == active,
                "created_at": cfg.get("created_at"),
                "layout_version": 3 if legacy else HOME_LAYOUT_VERSION,
                "migration_required": legacy,
            }
        )
    return items


def set_active_project(root: str | Path | None, name: str) -> dict[str, Any]:
    """Persist and verify the active project through the shared init seam."""
    return init_project(root, name)


def get_active_project(root: str | Path | None = None) -> str | None:
    """Return the active project preference when its directory exists."""
    base = resolve_home(root)
    data = _read_config(base / "config.json")
    name = data.get("active_project") or data.get("project")
    if not name or not _project_dir(base, str(name)).is_dir():
        return None
    return str(name)


def set_active_env(
    root: str | Path | None,
    env: str,
    *,
    project: str | None = None,
) -> dict[str, Any]:
    """Persist the selected environment in the home-local preference map."""
    if env not in _VALID_ENVS:
        msg = f"env must be one of {sorted(_VALID_ENVS)}, got {env!r}"
        raise ValueError(msg)
    base = resolve_home(root)
    project_name = _resolve_project(project, base / "config.json", base)
    if not _project_dir(base, project_name).is_dir():
        msg = f"project not found: {project_name!r}"
        raise FileNotFoundError(msg)
    data = _upgrade_home_config(_read_config(base / "config.json"))
    active = dict(data.get("active_environments") or {})
    active[project_name] = env
    data["active_environments"] = active
    _write_config(base / "config.json", data)
    return Workspace.open(base, project=project_name, env=env).to_dict()


def delete_project(
    root: str | Path | None,
    name: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Move a project to recoverable data-home trash after explicit confirmation."""
    validate_project_name(name)
    if not force:
        msg = "delete_project requires force=True"
        raise ValueError(msg)
    base = resolve_home(root)
    project_root = _project_dir(base, name)
    if not project_root.is_dir():
        msg = f"project not found: {name!r}"
        raise FileNotFoundError(msg)
    trash = base / ".trash" / "projects"
    trash.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = trash / f"{name}-{stamp}"
    project_root.replace(target)
    home_cfg = _upgrade_home_config(_read_config(base / "config.json"))
    if home_cfg.get("active_project") == name:
        remaining = list_projects(base)
        if remaining:
            home_cfg["active_project"] = remaining[0]["name"]
        else:
            home_cfg.pop("active_project", None)
    active_envs = dict(home_cfg.get("active_environments") or {})
    active_envs.pop(name, None)
    home_cfg["active_environments"] = active_envs
    _write_config(base / "config.json", home_cfg)
    return {
        "ok": True,
        "deleted": name,
        "recoverable": True,
        "trash_path": str(target),
        "active_project": home_cfg.get("active_project"),
        "projects": list_projects(base),
    }


def rename_project(root: str | Path | None, name: str, new_name: str) -> dict[str, Any]:
    """Atomically rename a project slug while preserving stable project identity."""
    validate_project_name(name)
    validate_project_name(new_name)
    if name == new_name:
        return {"ok": True, "renamed": False, "name": name}
    base = resolve_home(root)
    src = _project_dir(base, name)
    dest = _project_dir(base, new_name)
    if not src.is_dir():
        msg = f"project not found: {name!r}"
        raise FileNotFoundError(msg)
    if dest.exists():
        msg = f"target project already exists: {new_name!r}"
        raise FileExistsError(msg)
    cfg = _read_config(src / "project.json")
    cfg["slug"] = new_name
    if cfg.get("display_name") in {None, "", name}:
        cfg["display_name"] = new_name
    atomic_write_json(src / "project.json", cfg, mode=0o600)
    src.replace(dest)
    home_cfg = _upgrade_home_config(_read_config(base / "config.json"))
    if home_cfg.get("active_project") == name:
        home_cfg["active_project"] = new_name
    active_envs = dict(home_cfg.get("active_environments") or {})
    if name in active_envs:
        active_envs[new_name] = active_envs.pop(name)
    home_cfg["active_environments"] = active_envs
    _write_config(base / "config.json", home_cfg)
    ws = Workspace.open(base, project=new_name)
    return {"ok": True, "renamed": True, "from": name, "to": new_name, **ws.to_dict()}


def validate_log_retention(value: str) -> str:
    """Validate a project log-retention duration."""
    normalized = value.strip().lower()
    if normalized != "forever" and _LOG_RETENTION_RE.fullmatch(normalized) is None:
        msg = "log retention must be forever or a positive day value of at most 9 digits"
        raise ValueError(msg)
    return normalized


def validate_project_name(name: str) -> None:
    """Validate a portable project slug."""
    if name in _RESERVED_PROJECT_NAMES:
        msg = f"project name is reserved: {name!r}"
        raise ValueError(msg)
    if not _PROJECT_NAME_RE.fullmatch(name):
        msg = (
            f"invalid project name {name!r}; "
            "use lowercase letter start, then [a-z0-9_-], max 64 chars"
        )
        raise ValueError(msg)


def resolve_home(root: str | Path | None = None) -> Path:
    """Resolve home: explicit path, ``ROI_H_HOME``, then ``~/.roi-h``."""
    if root is not None:
        return Path(root).expanduser().resolve()
    env = os.environ.get("ROI_H_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".roi-h").resolve()


def _project_dir(home: Path, name: str) -> Path:
    return home / "projects" / name


def _assert_managed_project_root(home: Path, project_root: Path) -> None:
    projects = home / "projects"
    if (
        projects.is_symlink()
        or project_root.is_symlink()
        or project_root.resolve().parent != projects.resolve()
    ):
        msg = "project path must be a direct, non-symlink child of <ROI_H_HOME>/projects"
        raise ValueError(msg)


def _assert_safe_child(path: Path, root: Path) -> None:
    current = path
    while current != root:
        if current.is_symlink():
            msg = f"path.escape_denied: managed project path is a symlink: {path.name}"
            raise RuntimeError(msg)
        current = current.parent
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        msg = f"path.escape_denied: managed project path escaped its project: {path.name}"
        raise RuntimeError(msg) from exc


def _project_from_cwd(home: Path, cwd: str | Path | None) -> str:
    current = Path(cwd or Path.cwd()).expanduser().resolve()
    projects = (home / "projects").resolve()
    if current.parent != projects:
        msg = "project name is required outside <ROI_H_HOME>/projects/<name>"
        raise ValueError(msg)
    return current.name


def _validate_project_manifest(project_root: Path) -> dict[str, Any]:
    path = _project_manifest_path(project_root)
    _assert_safe_child(path, project_root)
    config = _read_config(path)
    if not project_root.is_dir():
        msg = f"project not found: {project_root.name!r}"
        raise FileNotFoundError(msg)
    if not config:
        msg = f"project manifest missing or invalid: {path}"
        raise FileNotFoundError(msg)
    if config.get("schema_version") != PROJECT_SCHEMA_VERSION:
        msg = f"project manifest schema unsupported: {config.get('schema_version')!r}"
        raise RuntimeError(msg)
    if not str(config.get("project_id") or "").startswith("prj_"):
        msg = f"project manifest has invalid project_id: {config.get('project_id')!r}"
        raise ValueError(msg)
    return config


def _ensure_project_roots(project_root: Path) -> None:
    for relative in (
        ".locks",
        "reference",
        "skills",
        "sources/automations",
        "packages/automations",
        "channels/dev",
        "channels/prod",
    ):
        path = project_root / relative
        _assert_safe_child(path, project_root)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for env_name in sorted(_VALID_ENVS):
        _ensure_environment_roots(
            project_root / "environments" / env_name,
            env_name,
            containment_root=project_root,
        )


def _project_manifest_path(project_root: Path) -> Path:
    return project_root / "project.json"


def _resolve_project(explicit: str | None, home_config_path: Path, home: Path) -> str:
    if explicit is not None:
        validate_project_name(explicit)
        return explicit
    env_var = os.environ.get("ROI_H_PROJECT")
    if env_var:
        validate_project_name(env_var)
        return env_var
    data = _read_config(home_config_path)
    configured = data.get("active_project") or data.get("project")
    if configured:
        name = str(configured)
        validate_project_name(name)
        return name
    projects = list_projects(home)
    if len(projects) == 1:
        return str(projects[0]["name"])
    if not projects:
        msg = f"no projects under {home}. Create one with: roi-h project create NAME"
        raise FileNotFoundError(msg)
    msg = (
        f"no active project in {home_config_path}. "
        "Pass the project name, set ROI_H_PROJECT, or use project.use."
    )
    raise FileNotFoundError(msg)


def _resolve_env(explicit: str | None, home_config_path: Path, project: str) -> EnvName:
    if explicit is not None:
        if explicit not in _VALID_ENVS:
            msg = f"env must be one of {sorted(_VALID_ENVS)}, got {explicit!r}"
            raise ValueError(msg)
        return explicit  # type: ignore[return-value]
    env_var = os.environ.get("ROI_H_ENV")
    if env_var:
        if env_var not in _VALID_ENVS:
            msg = f"ROI_H_ENV must be one of {sorted(_VALID_ENVS)}, got {env_var!r}"
            raise ValueError(msg)
        return env_var  # type: ignore[return-value]
    data = _read_config(home_config_path)
    active = data.get("active_environments")
    if isinstance(active, dict) and active.get(project) in _VALID_ENVS:
        return str(active[project])  # type: ignore[return-value]
    return "dev"


def _prepare_home(root: str | Path | None) -> Path:
    """Create the data home and report access failures with a repair path."""
    base = resolve_home(root)
    try:
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        _ensure_home_roots(base)
        _assert_home_writable(base)
    except PermissionError as exc:
        target = exc.filename or base
        msg = (
            f"ROI-H cannot write to data home {base} (blocked at {target}). "
            "Use a writable --home path or ROI_H_HOME. Do not run ROI-H with sudo."
        )
        raise PermissionError(errno.EACCES, msg, str(base)) from exc
    return base


def _ensure_home_roots(base: Path) -> None:
    for relative in ("projects", "skills", "diagnostics", "cache"):
        path = base / relative
        _assert_safe_child(path, base)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _assert_home_writable(base: Path) -> None:
    _assert_writable_directory(base)
    for relative in ("projects", "skills", "diagnostics", "cache"):
        _assert_writable_directory(base / relative)


def _assert_writable_directory(path: Path) -> None:
    if not os.access(path, os.W_OK | os.X_OK):
        raise PermissionError(errno.EACCES, f"directory is not writable: {path}", str(path))


def _ensure_environment_roots(
    root: Path,
    env: str,
    *,
    containment_root: Path,
) -> None:
    _assert_safe_child(root, containment_root)
    for relative in (
        "store",
        "runs",
        "runtime/locks",
        "runtime/browser-profiles",
    ):
        path = root / relative
        _assert_safe_child(path, containment_root)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = root / "environment.json"
    _assert_safe_child(manifest, containment_root)
    if manifest.exists():
        config = _read_config(manifest)
        execution = config.get("execution")
        if isinstance(execution, dict) and (
            "allow_adaptive" in execution or "allow_ambient_project_skills" in execution
        ):
            execution.pop("allow_adaptive", None)
            execution.pop("allow_ambient_project_skills", None)
            execution["editable_automation_sources"] = env == "dev"
            atomic_write_json(manifest, config, mode=0o600)
        return
    atomic_write_json(
        manifest,
        {
            "schema_version": ENVIRONMENT_SCHEMA_VERSION,
            "name": env,
            "store": {
                "adapter": "activegraph-sqlite",
                "durability": "full" if env == "prod" else "normal",
                "busy_timeout_ms": 10_000,
            },
            "execution": {
                "editable_automation_sources": env == "dev",
            },
        },
        mode=0o600,
    )


def _assert_supported_home(path: Path) -> None:
    data = _read_config(path)
    version = data.get("schema_version", data.get("version"))
    if version is None:
        return
    try:
        numeric = int(version)
    except (TypeError, ValueError) as exc:
        msg = f"home.schema_unsupported: {version!r}"
        raise RuntimeError(msg) from exc
    if numeric > HOME_LAYOUT_VERSION:
        msg = f"home.schema_unsupported: layout {numeric} is newer than {HOME_LAYOUT_VERSION}"
        raise RuntimeError(msg)


def _upgrade_home_config(data: dict[str, Any]) -> dict[str, Any]:
    active_project = data.get("active_project") or data.get("project")
    active_environments = data.get("active_environments")
    if not isinstance(active_environments, dict):
        active_environments = {}
    return {
        "schema_version": HOME_LAYOUT_VERSION,
        **({"active_project": active_project} if active_project else {}),
        "active_environments": dict(active_environments),
        "release_channel": str(data.get("release_channel") or "stable"),
        "diagnostics": dict(
            data.get("diagnostics") or {"level": "warning", "max_bytes": 52_428_800, "max_files": 5}
        ),
    }


def _read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_config(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data, mode=0o600)


__all__ = [
    "ENVIRONMENT_SCHEMA_VERSION",
    "HOME_LAYOUT_VERSION",
    "PROJECT_SCHEMA_VERSION",
    "EnvName",
    "Workspace",
    "configure_project",
    "create_project",
    "delete_project",
    "get_active_project",
    "init_home",
    "init_project",
    "list_projects",
    "project_structure",
    "rename_project",
    "resolve_home",
    "set_active_env",
    "set_active_project",
    "validate_log_retention",
    "validate_project_name",
]
