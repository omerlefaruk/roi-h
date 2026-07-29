"""Project-scoped secrets (never logged into recipes by default)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.workspace import Workspace

_SECRET_REF_RE = re.compile(r"\{\{secret\.([A-Za-z0-9_.-]+)\}\}")


def secrets_path(workspace: Workspace) -> Path:
    """Secrets live at project level (shared by dev/prod of that project)."""
    return workspace.project_root / "secrets.json"


def list_secrets(workspace: Workspace) -> dict[str, Any]:
    """List secret *names* only (values redacted)."""
    data = _read(workspace)
    return {
        "ok": True,
        "project": workspace.project,
        "path": str(secrets_path(workspace)),
        "names": sorted(data.keys()),
        "count": len(data),
    }


def set_secret(workspace: Workspace, name: str, value: str) -> dict[str, Any]:
    """Set or replace a secret value."""
    key = _validate_name(name)
    data = _read(workspace)
    data[key] = value
    _write(workspace, data)
    return {"ok": True, "name": key, "project": workspace.project, "action": "set"}


def get_secret(workspace: Workspace, name: str) -> str | None:
    """Return secret value or None (for tool injection, not CLI stdout)."""
    return _read(workspace).get(_validate_name(name))


def delete_secret(workspace: Workspace, name: str) -> dict[str, Any]:
    """Remove a secret."""
    key = _validate_name(name)
    data = _read(workspace)
    if key not in data:
        msg = f"secret not found: {key}"
        raise KeyError(msg)
    del data[key]
    _write(workspace, data)
    return {"ok": True, "name": key, "action": "deleted"}


def inject_secrets_into_environ(
    workspace: Workspace,
    *,
    prefix: str = "ROI_H_SECRET_",
) -> list[str]:
    """Export secrets into process env as ``ROI_H_SECRET_<NAME>`` (uppercase)."""
    data = _read(workspace)
    exported: list[str] = []
    for name, value in data.items():
        env_key = f"{prefix}{name.upper()}"
        os.environ[env_key] = str(value)
        exported.append(env_key)
    return exported


def resolve_secret_refs(value: object, workspace: Workspace) -> object:
    """Replace ``{{secret.NAME}}`` in strings (walk dicts/lists)."""
    if isinstance(value, dict):
        return {str(k): resolve_secret_refs(v, workspace) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_secret_refs(item, workspace) for item in value]
    if not isinstance(value, str):
        return value
    if "{{secret." not in value:
        return value
    out = value
    data = _read(workspace)
    for name, secret in data.items():
        out = out.replace(f"{{{{secret.{name}}}}}", secret)
    return out


def redact_secret_values(value: object, workspace: Workspace) -> object:
    """Replace persisted secret values with their ``{{secret.NAME}}`` refs."""
    if isinstance(value, dict):
        return {str(k): redact_secret_values(v, workspace) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secret_values(item, workspace) for item in value]
    if not isinstance(value, str):
        return value
    out = value
    for name, secret in _read(workspace).items():
        if secret:
            out = out.replace(secret, f"{{{{secret.{name}}}}}")
    return out


def secret_names_in_refs(value: object) -> set[str]:
    """Collect secret names referenced anywhere in a JSON-like payload."""
    if isinstance(value, dict):
        names: set[str] = set()
        for item in value.values():
            names.update(secret_names_in_refs(item))
        return names
    if isinstance(value, list):
        names = set()
        for item in value:
            names.update(secret_names_in_refs(item))
        return names
    if not isinstance(value, str):
        return set()
    return set(_SECRET_REF_RE.findall(value))


def _validate_name(name: str) -> str:
    key = name.strip()
    if not key or any(ch in key for ch in " \t\n/\\"):
        msg = f"invalid secret name: {name!r}"
        raise ValueError(msg)
    return key


def _read(workspace: Workspace) -> dict[str, str]:
    path = secrets_path(workspace)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _write(workspace: Workspace, data: dict[str, str]) -> None:
    path = secrets_path(workspace)
    atomic_write_json(path, data, mode=0o600)
