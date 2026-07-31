"""Immutable modular automation source packages and environment channels."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roi_h.harness.atomicfs import atomic_write_json, package_digest, verify_package
from roi_h.harness.automation_source import (
    AutomationSourceManifest,
    load_source_manifest,
    make_tree_writable,
    snapshot_source,
    source_tree_digest,
)
from roi_h.harness.workspace import Workspace

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def publish_manifest(
    workspace: Workspace,
    *,
    name: str,
    version: str,
    source_root: str | Path,
    source_run_id: str,
    expected_source_digest: str,
    goal: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Publish the exact verified source snapshot from one development run."""
    _validate_name(name)
    _validate_version(version)
    manifest_source = load_source_manifest(source_root)
    if manifest_source.name != name:
        msg = f"automation source name mismatch: expected {name!r}, got {manifest_source.name!r}"
        raise ValueError(msg)
    observed_digest, _ = source_tree_digest(source_root)
    if observed_digest != expected_source_digest:
        msg = (
            "automation source digest does not match the verified run: "
            f"expected {expected_source_digest}, observed {observed_digest}"
        )
        raise ValueError(msg)

    name_dir = workspace.automations / name
    name_dir.mkdir(parents=True, exist_ok=True)
    package_root = name_dir / version
    existing: dict[str, Any] | None = None
    if package_root.exists():
        existing = _read_manifest(package_root)
        verify_package(package_root, existing)

    staging = Path(tempfile.mkdtemp(prefix=f".{version}.package-", dir=name_dir))
    try:
        snapshot = snapshot_source(source_root, staging / "source")
        unsigned = _package_manifest(
            workspace,
            name=name,
            version=version,
            source_run_id=source_run_id,
            source=snapshot.manifest,
            source_digest=snapshot.source_digest,
            source_files=snapshot.files,
            goal=goal,
            notes=notes,
            created_at=(
                str(existing["created_at"])
                if existing is not None
                else datetime.now(UTC).isoformat()
            ),
        )
        manifest = {**unsigned, "package_digest": package_digest(staging, unsigned)}
        atomic_write_json(staging / "manifest.json", manifest, mode=0o600)
        if existing is not None:
            if existing["package_digest"] != manifest["package_digest"]:
                msg = f"automation version is immutable: {name}@{version}"
                raise FileExistsError(msg)
        else:
            staging.replace(package_root)
        _write_channel(workspace, name, version, manifest)
        return {
            "ok": True,
            "name": name,
            "version": version,
            "source_run_id": source_run_id,
            "source_digest": snapshot.source_digest,
            "package_digest": manifest["package_digest"],
            "manifest": manifest,
        }
    finally:
        if staging.exists():
            make_tree_writable(staging)
            shutil.rmtree(staging)


def push_to_prod(
    *,
    root: str | Path | None,
    project: str,
    name: str,
    version: str | None = None,
) -> dict[str, Any]:
    """Promote one verified immutable package by changing the production channel."""
    dev = Workspace.open(root, project=project, env="dev")
    prod = Workspace.open(root, project=project, env="prod")
    _validate_name(name)
    resolved_version = version or _read_channel(dev, name)
    if resolved_version is None:
        msg = f"no development package is selected for automation {name!r}"
        raise FileNotFoundError(msg)
    package_root = dev.automations / name / resolved_version
    manifest = _read_manifest(package_root)
    digest = verify_package(package_root, manifest)
    verified = manifest.get("verification", {}).get("passed") is True
    if manifest.get("schema_version") != 2 or not verified:
        msg = f"automation package is not a verified schema-2 package: {name}@{resolved_version}"
        raise ValueError(msg)
    _write_channel(prod, name, resolved_version, manifest)
    return {
        "ok": True,
        "name": name,
        "version": resolved_version,
        "from_environment": "dev",
        "to_environment": "prod",
        "package_digest": digest,
        "source_digest": manifest["source_digest"],
    }


def load_automation(
    workspace: Workspace,
    name: str,
    *,
    version: str | None = None,
) -> dict[str, Any]:
    """Load and verify one immutable modular source package."""
    _validate_name(name)
    resolved_version = version or _read_channel(workspace, name)
    if resolved_version is None:
        msg = f"no automation {name!r} is selected in environment {workspace.env!r}"
        raise FileNotFoundError(msg)
    package_root = workspace.automations / name / resolved_version
    manifest = _read_manifest(package_root)
    if manifest.get("schema_version") != 2:
        msg = f"unsupported automation package schema: {manifest.get('schema_version')!r}"
        raise ValueError(msg)
    digest = verify_package(package_root, manifest)
    source_root = package_root / "source"
    source = load_source_manifest(source_root)
    source_digest, source_files = source_tree_digest(source_root)
    if source_digest != manifest.get("source_digest"):
        msg = f"automation source digest mismatch: {name}@{resolved_version}"
        raise ValueError(msg)
    return {
        "ok": True,
        "name": name,
        "version": resolved_version,
        "manifest": manifest,
        "package_digest": digest,
        "source_digest": source_digest,
        "source_files": source_files,
        "source_root": source_root,
        "source": source,
    }


def list_automations(workspace: Workspace) -> list[dict[str, Any]]:
    """List immutable package versions and the selected environment channel."""
    if not workspace.automations.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for name_dir in sorted(path for path in workspace.automations.iterdir() if path.is_dir()):
        versions = sorted(
            path.name
            for path in name_dir.iterdir()
            if path.is_dir() and (path / "manifest.json").is_file()
        )
        items.append(
            {
                "name": name_dir.name,
                "selected": _read_channel(workspace, name_dir.name),
                "versions": versions,
            }
        )
    return items


def _package_manifest(
    workspace: Workspace,
    *,
    name: str,
    version: str,
    source_run_id: str,
    source: AutomationSourceManifest,
    source_digest: str,
    source_files: list[str],
    goal: str,
    notes: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "name": name,
        "version": version,
        "project_id": workspace.project_id,
        "source_environment": workspace.env,
        "created_at": created_at,
        "source_run_id": source_run_id,
        "source_digest": source_digest,
        "source_files": source_files,
        "goal": goal,
        "notes": notes or source.notes,
        "max_parallel": source.max_parallel,
        "required_secrets": source.required_secrets,
        "network_hosts": source.network_hosts,
        "phase_plan": [phase.model_dump(mode="json", by_alias=True) for phase in source.phases],
        "verification": {"required": True, "passed": True},
    }


def _read_manifest(package_root: Path) -> dict[str, Any]:
    path = package_root / "manifest.json"
    if not path.is_file():
        msg = f"automation package manifest not found: {path}"
        raise FileNotFoundError(msg)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"automation package manifest must be an object: {path}"
        raise TypeError(msg)
    return raw


def _read_channel(workspace: Workspace, name: str) -> str | None:
    path = workspace.channels / f"{name}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return str(raw.get("version") or "") or None


def _write_channel(
    workspace: Workspace,
    name: str,
    version: str,
    manifest: dict[str, Any],
) -> None:
    workspace.channels.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        workspace.channels / f"{name}.json",
        {
            "schema_version": 2,
            "name": name,
            "version": version,
            "package_digest": manifest["package_digest"],
            "source_digest": manifest["source_digest"],
            "source_run_id": manifest["source_run_id"],
            "promoted_at": datetime.now(UTC).isoformat(),
        },
        mode=0o600,
    )


def _validate_name(name: str) -> None:
    if not _NAME.fullmatch(name):
        msg = f"invalid automation name: {name!r}"
        raise ValueError(msg)


def _validate_version(version: str) -> None:
    if not _VERSION.fullmatch(version):
        msg = f"invalid automation version: {version!r}"
        raise ValueError(msg)


__all__ = [
    "list_automations",
    "load_automation",
    "publish_manifest",
    "push_to_prod",
]
