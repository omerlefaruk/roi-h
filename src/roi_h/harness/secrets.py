"""Environment-isolated secret declarations and provider adapters."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.workspace import Workspace

_SECRET_REF_RE = re.compile(r"\{\{secret\.([A-Za-z0-9_.-]+)\}\}")
_PROCESS_VALUES: dict[str, str] = {}


@dataclass(frozen=True)
class SecretMetadata:
    """Names-only secret metadata safe for operator output."""

    name: str
    project_id: str
    environment: str
    provider: str


class SecretStore(Protocol):
    """Provider seam that never returns all secret values in bulk."""

    provider: str

    def list_names(self, project_id: str, environment: str) -> list[SecretMetadata]: ...

    def get(self, project_id: str, environment: str, name: str) -> str | None: ...

    def set(
        self,
        project_id: str,
        environment: str,
        name: str,
        value: str,
    ) -> SecretMetadata: ...

    def delete(self, project_id: str, environment: str, name: str) -> bool: ...


class EnvironmentSecretStore:
    """Ephemeral/headless provider backed by scoped environment variables."""

    provider = "environment"

    def list_names(self, project_id: str, environment: str) -> list[SecretMetadata]:
        prefix = _environment_prefix(project_id, environment)
        names = {key.removeprefix(prefix) for key in os.environ if key.startswith(prefix)}
        names.update(
            key.rsplit("/", 1)[-1]
            for key in _PROCESS_VALUES
            if key.startswith(f"{project_id}/{environment}/")
        )
        return [
            SecretMetadata(name, project_id, environment, self.provider) for name in sorted(names)
        ]

    def get(self, project_id: str, environment: str, name: str) -> str | None:
        identity = _identity(project_id, environment, name)
        return _PROCESS_VALUES.get(identity) or os.environ.get(
            _environment_key(project_id, environment, name)
        )

    def set(
        self,
        project_id: str,
        environment: str,
        name: str,
        value: str,
    ) -> SecretMetadata:
        _PROCESS_VALUES[_identity(project_id, environment, name)] = value
        return SecretMetadata(name, project_id, environment, self.provider)

    def delete(self, project_id: str, environment: str, name: str) -> bool:
        identity = _identity(project_id, environment, name)
        existed = identity in _PROCESS_VALUES or (
            _environment_key(project_id, environment, name) in os.environ
        )
        _PROCESS_VALUES.pop(identity, None)
        os.environ.pop(_environment_key(project_id, environment, name), None)
        return existed


class MacOSKeychainSecretStore:
    """macOS Keychain adapter using one service and a scoped account identity."""

    provider = "macos-keychain"
    service = "dev.roi-h.secrets"

    def list_names(self, project_id: str, environment: str) -> list[SecretMetadata]:
        # Keychain enumeration is deliberately not used; declarations are authoritative.
        del project_id, environment
        return []

    def get(self, project_id: str, environment: str, name: str) -> str | None:
        completed = subprocess.run(  # noqa: S603
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                _identity(project_id, environment, name),
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode == 44:
            return None
        if completed.returncode != 0:
            msg = "secret.provider_failed: macOS Keychain read failed"
            raise RuntimeError(msg)
        return completed.stdout.rstrip("\n")

    def set(
        self,
        project_id: str,
        environment: str,
        name: str,
        value: str,
    ) -> SecretMetadata:
        # With ``-w`` as the last option, ``security`` reads the value from its prompt.
        # Standard input supplies that prompt without exposing the value in process argv.
        completed = subprocess.run(  # noqa: S603
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                _identity(project_id, environment, name),
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            input=value,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            msg = "secret.provider_failed: macOS Keychain write failed"
            raise RuntimeError(msg)
        return SecretMetadata(name, project_id, environment, self.provider)

    def delete(self, project_id: str, environment: str, name: str) -> bool:
        completed = subprocess.run(  # noqa: S603
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-a",
                _identity(project_id, environment, name),
                "-s",
                self.service,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode == 44:
            return False
        if completed.returncode != 0:
            msg = "secret.provider_failed: macOS Keychain delete failed"
            raise RuntimeError(msg)
        return True


def secrets_path(workspace: Workspace) -> Path:
    """Return names-only secret metadata; values never live here."""
    return workspace.project_root / "secrets.meta.json"


def list_secrets(workspace: Workspace) -> dict[str, Any]:
    """List declared names for only the selected environment."""
    metadata = _metadata(workspace)
    names = _declared_names(metadata, workspace.env)
    return {
        "ok": True,
        "project": workspace.project,
        "project_id": workspace.project_id,
        "environment": workspace.env,
        "provider": metadata["provider"],
        "names": names,
        "count": len(names),
    }


def set_secret(workspace: Workspace, name: str, value: str) -> dict[str, Any]:
    """Set one scoped value and persist only its declaration."""
    key = _validate_name(name)
    if not value:
        msg = "secret value must be non-empty"
        raise ValueError(msg)
    metadata = _metadata(workspace)
    store = _store(str(metadata["provider"]))
    store.set(workspace.project_id, workspace.env, key, value)
    if store.get(workspace.project_id, workspace.env, key) != value:
        msg = "secret.provider_failed: value verification failed"
        raise TypeError(msg)
    entries = list(metadata.get("entries") or [])
    existing = next((item for item in entries if item.get("name") == key), None)
    if existing is None:
        entries.append({"name": key, "environments": [workspace.env]})
    else:
        environments = set(existing.get("environments") or [])
        environments.add(workspace.env)
        existing["environments"] = sorted(environments)
    metadata["entries"] = sorted(entries, key=lambda item: str(item.get("name") or ""))
    atomic_write_json(secrets_path(workspace), metadata, mode=0o600)
    return {
        "ok": True,
        "name": key,
        "project": workspace.project,
        "environment": workspace.env,
        "provider": store.provider,
        "action": "set",
    }


def get_secret(workspace: Workspace, name: str) -> str | None:
    """Load one value through the selected provider."""
    key = _validate_name(name)
    metadata = _metadata(workspace)
    return _store(str(metadata["provider"])).get(
        workspace.project_id,
        workspace.env,
        key,
    )


def delete_secret(workspace: Workspace, name: str) -> dict[str, Any]:
    """Delete one environment value while retaining other environment declarations."""
    key = _validate_name(name)
    metadata = _metadata(workspace)
    store = _store(str(metadata["provider"]))
    if not store.delete(workspace.project_id, workspace.env, key):
        msg = f"secret not found: {key}"
        raise KeyError(msg)
    entries: list[dict[str, Any]] = []
    for entry in metadata.get("entries") or []:
        if entry.get("name") != key:
            entries.append(entry)
            continue
        environments = [env for env in entry.get("environments") or [] if env != workspace.env]
        if environments:
            entries.append({**entry, "environments": environments})
    metadata["entries"] = entries
    atomic_write_json(secrets_path(workspace), metadata, mode=0o600)
    return {
        "ok": True,
        "name": key,
        "environment": workspace.env,
        "action": "deleted",
    }


def inject_secrets_into_environ(
    workspace: Workspace,
    *,
    prefix: str = "ROI_H_SECRET_",
) -> list[str]:
    """Compatibility helper; normal workers receive only declared tool secrets."""
    exported: list[str] = []
    for name in list_secrets(workspace)["names"]:
        value = get_secret(workspace, str(name))
        if value is None:
            continue
        env_key = f"{prefix}{str(name).upper()}"
        os.environ[env_key] = value
        exported.append(env_key)
    return exported


def resolve_secret_refs(value: object, workspace: Workspace) -> object:
    """Resolve only names explicitly referenced in the supplied value."""
    if isinstance(value, dict):
        return {str(k): resolve_secret_refs(v, workspace) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_secret_refs(item, workspace) for item in value]
    if not isinstance(value, str) or "{{secret." not in value:
        return value
    out = value
    for name in sorted(_SECRET_REF_RE.findall(value)):
        secret = get_secret(workspace, name)
        if secret is None:
            msg = f"secret.missing: {name} in {workspace.env}"
            raise KeyError(msg)
        out = out.replace(f"{{{{secret.{name}}}}}", secret)
    return out


def redact_secret_values(value: object, workspace: Workspace) -> object:
    """Redact known declared values before persistence or diagnostics."""
    if isinstance(value, dict):
        return {str(k): redact_secret_values(v, workspace) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secret_values(item, workspace) for item in value]
    if not isinstance(value, str):
        return value
    out = value
    for name in list_secrets(workspace)["names"]:
        secret = get_secret(workspace, str(name))
        if secret:
            out = out.replace(secret, f"{{{{secret.{name}}}}}")
    return out


def secret_names_in_refs(value: object) -> set[str]:
    """Collect referenced names from a JSON-like payload."""
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


def _store(provider: str) -> SecretStore:
    # Tests and explicit headless sessions never mutate a user's login Keychain.
    if os.environ.get("PYTEST_CURRENT_TEST") or provider == "environment":
        return EnvironmentSecretStore()
    if provider == "macos-keychain":
        return MacOSKeychainSecretStore()
    msg = f"secret.provider_failed: unsupported provider {provider!r}"
    raise RuntimeError(msg)


def _metadata(workspace: Workspace) -> dict[str, Any]:
    path = secrets_path(workspace)
    if not path.is_file():
        return {"schema_version": 1, "provider": "environment", "entries": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        msg = "secret.provider_failed: invalid secrets.meta.json"
        raise RuntimeError(msg)
    entries = raw.get("entries")
    if not isinstance(entries, list):
        msg = "secret.provider_failed: invalid secret declarations"
        raise TypeError(msg)
    return raw


def _declared_names(metadata: dict[str, Any], environment: str) -> list[str]:
    return sorted(
        str(entry["name"])
        for entry in metadata.get("entries") or []
        if isinstance(entry, dict)
        and entry.get("name")
        and environment in (entry.get("environments") or [])
    )


def _validate_name(name: str) -> str:
    key = name.strip()
    if not key or any(ch in key for ch in " \t\n/\\"):
        msg = f"invalid secret name: {name!r}"
        raise ValueError(msg)
    return key


def _identity(project_id: str, environment: str, name: str) -> str:
    return f"{project_id}/{environment}/{name}"


def _environment_prefix(project_id: str, environment: str) -> str:
    safe_project = re.sub(r"[^A-Za-z0-9]", "_", project_id).upper()
    return f"ROI_H_SECRET_{safe_project}_{environment.upper()}_"


def _environment_key(project_id: str, environment: str, name: str) -> str:
    return (
        _environment_prefix(project_id, environment)
        + re.sub(
            r"[^A-Za-z0-9]",
            "_",
            name,
        ).upper()
    )


__all__ = [
    "EnvironmentSecretStore",
    "MacOSKeychainSecretStore",
    "SecretMetadata",
    "SecretStore",
    "delete_secret",
    "get_secret",
    "inject_secrets_into_environ",
    "list_secrets",
    "redact_secret_values",
    "resolve_secret_refs",
    "secret_names_in_refs",
    "secrets_path",
    "set_secret",
]
