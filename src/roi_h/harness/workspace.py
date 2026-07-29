"""Multi-project workspace layout under the user-owned ROI-H home."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from roi_h.harness.atomicfs import atomic_write_json

EnvName = Literal["dev", "prod"]
_VALID_ENVS = frozenset({"dev", "prod"})
_PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RESERVED_PROJECT_NAMES = frozenset({"projects", "config"})
_HOME_CONFIG_VERSION = 3
_DEFAULT_PROJECT = "default"


@dataclass(frozen=True)
class Workspace:
    """Resolved paths for one project environment."""

    root: Path
    shared_skills: Path
    project: str
    project_root: Path
    env: EnvName
    db: Path
    project_skills: Path
    artifacts: Path
    automations: Path
    config_path: Path
    home_config_path: Path

    @classmethod
    def open(
        cls,
        root: str | Path | None = None,
        *,
        project: str | None = None,
        env: str | None = None,
        db: str | Path | None = None,
    ) -> Workspace:
        """Resolve workspace paths for a named project + env."""
        base = _resolve_root(root)
        base.mkdir(parents=True, exist_ok=True)
        shared_skills = base / "skills"
        shared_skills.mkdir(parents=True, exist_ok=True)
        home_config_path = base / "config.json"
        project_name = _resolve_project(project, home_config_path, base)
        project_root = _project_dir(base, project_name)
        if not project_root.is_dir():
            msg = (
                f"project {project_name!r} not found under {base / 'projects'}. "
                "Create it with: roi-h rpa project create NAME"
            )
            raise FileNotFoundError(msg)

        project_config_path = project_root / "config.json"
        active_env = _resolve_env(env, project_config_path)
        env_dir = project_root / active_env
        env_dir.mkdir(parents=True, exist_ok=True)

        db_path = Path(db).expanduser() if db is not None else env_dir / "rpa.sqlite"
        if not db_path.is_absolute():
            db_path = (Path.cwd() / db_path).resolve()
        else:
            db_path = db_path.resolve()

        project_skills = env_dir / "skills"
        artifacts = env_dir / "artifacts"
        automations = env_dir / "automations"
        project_skills.mkdir(parents=True, exist_ok=True)
        artifacts.mkdir(parents=True, exist_ok=True)
        automations.mkdir(parents=True, exist_ok=True)

        return cls(
            root=base.resolve(),
            shared_skills=shared_skills.resolve(),
            project=project_name,
            project_root=project_root.resolve(),
            env=active_env,  # type: ignore[arg-type]
            db=db_path,
            project_skills=project_skills.resolve(),
            artifacts=artifacts.resolve(),
            automations=automations.resolve(),
            config_path=project_config_path.resolve(),
            home_config_path=home_config_path.resolve(),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly path map for CLI/status output."""
        return {
            "root": str(self.root),
            "shared_skills": str(self.shared_skills),
            "project": self.project,
            "project_root": str(self.project_root),
            "env": self.env,
            "db": str(self.db),
            "project_skills": str(self.project_skills),
            "artifacts": str(self.artifacts),
            "automations": str(self.automations),
            "config_path": str(self.config_path),
            "home_config_path": str(self.home_config_path),
        }


def create_project(
    root: str | Path | None,
    name: str,
    *,
    display_name: str = "",
    set_active: bool = True,
    env: str = "dev",
) -> dict[str, Any]:
    """Create a project tree under ``<home>/projects/<name>/``."""
    validate_project_name(name)
    if env not in _VALID_ENVS:
        msg = f"env must be one of {sorted(_VALID_ENVS)}, got {env!r}"
        raise ValueError(msg)

    base = _resolve_root(root)
    base.mkdir(parents=True, exist_ok=True)
    project_root = _project_dir(base, name)
    if project_root.exists():
        msg = f"project already exists: {project_root}"
        raise FileExistsError(msg)

    project_root.mkdir(parents=True)
    (project_root / "reference").mkdir()
    for env_name in sorted(_VALID_ENVS):
        env_dir = project_root / env_name
        (env_dir / "skills").mkdir(parents=True)
        (env_dir / "artifacts").mkdir(parents=True)
        (env_dir / "automations").mkdir(parents=True)

    created_at = datetime.now(UTC).isoformat()
    project_cfg: dict[str, Any] = {
        "name": name,
        "display_name": display_name or name,
        "env": env,
        "created_at": created_at,
    }
    _write_config(project_root / "config.json", project_cfg)

    home_cfg = _read_config(base / "config.json")
    home_cfg["version"] = _HOME_CONFIG_VERSION
    if set_active or "project" not in home_cfg:
        home_cfg["project"] = name
    _write_config(base / "config.json", home_cfg)

    ws = Workspace.open(base, project=name, env=env)
    return {
        "ok": True,
        "created": True,
        "display_name": project_cfg["display_name"],
        "created_at": created_at,
        **ws.to_dict(),
    }


def init_home(
    root: str | Path | None = None,
    *,
    project: str = _DEFAULT_PROJECT,
    display_name: str = "",
) -> dict[str, Any]:
    """Ensure home exists with at least one project (create default if empty)."""
    base = _resolve_root(root)
    base.mkdir(parents=True, exist_ok=True)
    existing = list_projects(base)
    if existing:
        active = _read_config(base / "config.json").get("project")
        if active and any(p["name"] == active for p in existing):
            ws = Workspace.open(base, project=str(active))
            return {"ok": True, "created": False, "projects": existing, **ws.to_dict()}
        first = existing[0]["name"]
        set_active_project(base, first)
        ws = Workspace.open(base, project=first)
        return {"ok": True, "created": False, "projects": existing, **ws.to_dict()}

    created = create_project(
        base,
        project,
        display_name=display_name or project,
        set_active=True,
    )
    return {**created, "projects": list_projects(base)}


def list_projects(root: str | Path | None = None) -> list[dict[str, Any]]:
    """List named projects under the home."""
    base = _resolve_root(root)
    projects_dir = base / "projects"
    if not projects_dir.is_dir():
        return []
    home_cfg = _read_config(base / "config.json")
    active = home_cfg.get("project")
    items: list[dict[str, Any]] = []
    for path in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        cfg = _read_config(path / "config.json")
        name = str(cfg.get("name") or path.name)
        items.append(
            {
                "name": name,
                "display_name": str(cfg.get("display_name") or name),
                "env": str(cfg.get("env") or "dev"),
                "path": str(path.resolve()),
                "active": name == active,
                "created_at": cfg.get("created_at"),
            }
        )
    return items


def set_active_project(root: str | Path | None, name: str) -> dict[str, Any]:
    """Persist the active project in home ``config.json``."""
    validate_project_name(name)
    base = _resolve_root(root)
    project_root = _project_dir(base, name)
    if not project_root.is_dir():
        msg = f"project not found: {name!r}"
        raise FileNotFoundError(msg)
    home_cfg = _read_config(base / "config.json")
    home_cfg["version"] = _HOME_CONFIG_VERSION
    home_cfg["project"] = name
    _write_config(base / "config.json", home_cfg)
    ws = Workspace.open(base, project=name)
    return {"ok": True, **ws.to_dict()}


def get_active_project(root: str | Path | None = None) -> str | None:
    """Return the sticky active project name, if set and present."""
    base = _resolve_root(root)
    name = _read_config(base / "config.json").get("project")
    if not name:
        return None
    if not _project_dir(base, str(name)).is_dir():
        return None
    return str(name)


def set_active_env(
    root: str | Path | None,
    env: str,
    *,
    project: str | None = None,
) -> dict[str, Any]:
    """Persist the active environment on the project config."""
    if env not in _VALID_ENVS:
        msg = f"env must be one of {sorted(_VALID_ENVS)}, got {env!r}"
        raise ValueError(msg)
    base = _resolve_root(root)
    home_config = base / "config.json"
    project_name = _resolve_project(project, home_config, base)
    project_root = _project_dir(base, project_name)
    if not project_root.is_dir():
        msg = f"project not found: {project_name!r}"
        raise FileNotFoundError(msg)
    cfg_path = project_root / "config.json"
    data = _read_config(cfg_path)
    data["name"] = data.get("name") or project_name
    data["env"] = env
    if "display_name" not in data:
        data["display_name"] = project_name
    _write_config(cfg_path, data)
    return Workspace.open(base, project=project_name, env=env).to_dict()


def delete_project(
    root: str | Path | None,
    name: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Delete a project tree. Requires ``force=True`` (destructive)."""
    validate_project_name(name)
    if not force:
        msg = "delete_project requires force=True (destructive)"
        raise ValueError(msg)
    base = _resolve_root(root)
    project_root = _project_dir(base, name)
    if not project_root.is_dir():
        msg = f"project not found: {name!r}"
        raise FileNotFoundError(msg)
    shutil.rmtree(project_root)
    home_cfg = _read_config(base / "config.json")
    if home_cfg.get("project") == name:
        remaining = list_projects(base)
        if remaining:
            home_cfg["project"] = remaining[0]["name"]
        else:
            home_cfg.pop("project", None)
        _write_config(base / "config.json", home_cfg)
    return {
        "ok": True,
        "deleted": name,
        "active_project": home_cfg.get("project"),
        "projects": list_projects(base),
    }


def rename_project(
    root: str | Path | None,
    name: str,
    new_name: str,
) -> dict[str, Any]:
    """Rename a project directory and update configs."""
    validate_project_name(name)
    validate_project_name(new_name)
    if name == new_name:
        return {"ok": True, "renamed": False, "name": name}
    base = _resolve_root(root)
    src = _project_dir(base, name)
    dest = _project_dir(base, new_name)
    if not src.is_dir():
        msg = f"project not found: {name!r}"
        raise FileNotFoundError(msg)
    if dest.exists():
        msg = f"target project already exists: {new_name!r}"
        raise FileExistsError(msg)
    src.rename(dest)
    cfg_path = dest / "config.json"
    cfg = _read_config(cfg_path)
    cfg["name"] = new_name
    if cfg.get("display_name") in {None, "", name}:
        cfg["display_name"] = new_name
    _write_config(cfg_path, cfg)
    home_cfg = _read_config(base / "config.json")
    if home_cfg.get("project") == name:
        home_cfg["project"] = new_name
        _write_config(base / "config.json", home_cfg)
    ws = Workspace.open(base, project=new_name)
    return {"ok": True, "renamed": True, "from": name, "to": new_name, **ws.to_dict()}


def validate_project_name(name: str) -> None:
    """Raise ``ValueError`` if ``name`` is not a valid project id."""
    if name in _RESERVED_PROJECT_NAMES:
        msg = f"project name is reserved: {name!r}"
        raise ValueError(msg)
    if not _PROJECT_NAME_RE.match(name):
        msg = (
            f"invalid project name {name!r}; "
            "use lowercase letter start, then [a-z0-9_-], max 64 chars"
        )
        raise ValueError(msg)


def _project_dir(home: Path, name: str) -> Path:
    return home / "projects" / name


def resolve_home(root: str | Path | None = None) -> Path:
    """Resolve home: explicit path, ``ROI_H_HOME``, then ``~/.roi-h``."""
    if root is not None:
        return Path(root).expanduser().resolve()
    env = os.environ.get("ROI_H_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".roi-h").resolve()


def _resolve_root(root: str | Path | None) -> Path:
    return resolve_home(root)


def _resolve_project(explicit: str | None, home_config_path: Path, home: Path) -> str:
    if explicit is not None:
        validate_project_name(explicit)
        return explicit
    env_var = os.environ.get("ROI_H_PROJECT")
    if env_var:
        validate_project_name(env_var)
        return env_var
    data = _read_config(home_config_path)
    configured = data.get("project")
    if configured:
        name = str(configured)
        validate_project_name(name)
        return name
    projects = list_projects(home)
    if len(projects) == 1:
        return str(projects[0]["name"])
    if not projects:
        msg = (
            f"no projects under {home}. "
            "Create one with: roi-h rpa project create NAME  (or project init)"
        )
        raise FileNotFoundError(msg)
    msg = (
        f"no active project in {home_config_path}. "
        "Pass --project, set ROI_H_PROJECT, or: roi-h rpa project use NAME"
    )
    raise FileNotFoundError(msg)


def _resolve_env(explicit: str | None, project_config_path: Path) -> str:
    if explicit is not None:
        if explicit not in _VALID_ENVS:
            msg = f"env must be one of {sorted(_VALID_ENVS)}, got {explicit!r}"
            raise ValueError(msg)
        return explicit
    env_var = os.environ.get("ROI_H_ENV")
    if env_var:
        if env_var not in _VALID_ENVS:
            msg = f"ROI_H_ENV must be one of {sorted(_VALID_ENVS)}, got {env_var!r}"
            raise ValueError(msg)
        return env_var
    data = _read_config(project_config_path)
    value = str(data.get("env", "dev"))
    if value not in _VALID_ENVS:
        return "dev"
    return value


def _read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_config(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data)
