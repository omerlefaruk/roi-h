"""Editable modular automation source and immutable source snapshots."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.lease import RunLease

_AUTOMATION_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_IGNORED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_MAX_SOURCE_FILE_BYTES = 1_000_000
_MAX_SOURCE_TREE_BYTES = 10_000_000


class PhaseSpec(BaseModel):
    """One durable Python phase in an automation dependency graph."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    phase_id: str = Field(alias="id")
    module: str
    needs: list[str] = Field(default_factory=list)
    role: Literal["work", "verify"] = "work"
    parallel_safe: bool = False
    timeout_seconds: float = Field(default=300.0, gt=0, le=3600)

    @field_validator("phase_id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _AUTOMATION_NAME.fullmatch(value):
            msg = f"invalid phase id: {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("module")
    @classmethod
    def _valid_module(cls, value: str) -> str:
        if not _MODULE_NAME.fullmatch(value):
            msg = f"invalid phase module: {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("needs")
    @classmethod
    def _unique_needs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            msg = "phase dependencies must be unique"
            raise ValueError(msg)
        return value


class AutomationSourceManifest(BaseModel):
    """Closed manifest for one editable modular automation source tree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    name: str
    max_parallel: int = Field(default=1, ge=1, le=32)
    required_secrets: list[str] = Field(default_factory=list)
    network_hosts: list[str] = Field(default_factory=list)
    phases: list[PhaseSpec] = Field(max_length=32)
    notes: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _AUTOMATION_NAME.fullmatch(value):
            msg = f"invalid automation name: {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("required_secrets", "network_hosts")
    @classmethod
    def _unique_strings(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            msg = "manifest lists must contain non-empty strings"
            raise ValueError(msg)
        if len(value) != len(set(value)):
            msg = "manifest lists must not contain duplicates"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _valid_graph(self) -> AutomationSourceManifest:
        if not self.phases:
            msg = "automation must contain at least one phase"
            raise ValueError(msg)
        by_id = {phase.phase_id: phase for phase in self.phases}
        if len(by_id) != len(self.phases):
            msg = "automation phase ids must be unique"
            raise ValueError(msg)
        if not any(phase.role == "verify" for phase in self.phases):
            msg = "automation must contain a verification phase"
            raise ValueError(msg)
        if not any(phase.role == "work" for phase in self.phases):
            msg = "automation must contain a work phase separate from verification"
            raise ValueError(msg)
        modules = [phase.module for phase in self.phases]
        if len(modules) != len(set(modules)):
            msg = "each automation phase must use its own Python module"
            raise ValueError(msg)
        for phase in self.phases:
            missing = [item for item in phase.needs if item not in by_id]
            if missing:
                msg = f"phase {phase.phase_id!r} has unknown dependencies: {missing}"
                raise ValueError(msg)
            if phase.phase_id in phase.needs:
                msg = f"phase {phase.phase_id!r} cannot depend on itself"
                raise ValueError(msg)
            if phase.role == "verify" and not phase.needs:
                msg = f"verification phase {phase.phase_id!r} must depend on prior work"
                raise ValueError(msg)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(phase_id: str) -> None:
            if phase_id in visiting:
                msg = f"automation dependency cycle includes {phase_id!r}"
                raise ValueError(msg)
            if phase_id in visited:
                return
            visiting.add(phase_id)
            for dependency in by_id[phase_id].needs:
                visit(dependency)
            visiting.remove(phase_id)
            visited.add(phase_id)

        for phase_id in by_id:
            visit(phase_id)
        return self

    def ordered_phase_ids(self) -> list[str]:
        """Return a stable topological order for planning and projections."""
        by_id = {phase.phase_id: phase for phase in self.phases}
        pending = set(by_id)
        complete: set[str] = set()
        ordered: list[str] = []
        while pending:
            ready = sorted(
                phase_id for phase_id in pending if set(by_id[phase_id].needs).issubset(complete)
            )
            if not ready:  # pragma: no cover - model validation rejects cycles
                msg = "automation dependency graph cannot advance"
                raise RuntimeError(msg)
            ordered.extend(ready)
            complete.update(ready)
            pending.difference_update(ready)
        return ordered


class PhaseResult(BaseModel):
    """JSON result returned by one phase worker."""

    model_config = ConfigDict(extra="forbid")

    summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)

    @field_validator("artifacts")
    @classmethod
    def _portable_artifacts(cls, value: dict[str, str]) -> dict[str, str]:
        for name, raw_path in value.items():
            if not name.strip():
                msg = "artifact names must be non-empty"
                raise ValueError(msg)
            path = PurePosixPath(raw_path.replace("\\", "/"))
            if path.is_absolute() or not path.parts or ".." in path.parts:
                msg = f"artifact path must be relative to the phase output directory: {raw_path!r}"
                raise ValueError(msg)
        return value


class SourceSnapshot(BaseModel):
    """Identity of one immutable automation source snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_digest: str
    root: Path
    manifest: AutomationSourceManifest
    files: list[str]


def load_source_manifest(source_root: str | Path) -> AutomationSourceManifest:
    """Load and validate ``automation.json`` plus every declared phase module."""
    root = Path(source_root).expanduser().resolve()
    path = root / "automation.json"
    if not path.is_file():
        msg = f"automation source manifest not found: {path}"
        raise FileNotFoundError(msg)
    raw = json.loads(path.read_text(encoding="utf-8"))
    manifest = AutomationSourceManifest.model_validate(raw)
    for phase in manifest.phases:
        module_path = root.joinpath(*phase.module.split(".")).with_suffix(".py")
        if not module_path.is_file() or module_path.is_symlink():
            msg = f"phase module not found: {phase.module} ({module_path})"
            raise FileNotFoundError(msg)
        if not module_path.resolve().is_relative_to(root):
            msg = f"phase module escapes automation source: {phase.module}"
            raise ValueError(msg)
    return manifest


def source_tree_digest(source_root: str | Path) -> tuple[str, list[str]]:
    """Hash a portable source tree and reject unsafe or generated entries."""
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        msg = f"automation source root not found: {root}"
        raise FileNotFoundError(msg)
    digest = hashlib.sha256()
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            msg = f"automation source contains a symbolic link: {relative.as_posix()}"
            raise ValueError(msg)
        if path.is_dir():
            continue
        if path.suffix in {".pyc", ".pyo"}:
            msg = f"automation source contains Python bytecode: {relative.as_posix()}"
            raise ValueError(msg)
        if not path.is_file():
            msg = f"automation source contains a non-regular file: {relative.as_posix()}"
            raise ValueError(msg)
        name = relative.as_posix()
        data = path.read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        files.append(name)
    if "automation.json" not in files:
        msg = "automation source tree does not contain automation.json"
        raise ValueError(msg)
    return digest.hexdigest(), files


def snapshot_source(source_root: str | Path, destination: str | Path) -> SourceSnapshot:
    """Atomically copy and verify one automation source snapshot."""
    source = Path(source_root).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    manifest = load_source_manifest(source)
    before, _ = source_tree_digest(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.snapshot-", dir=target.parent))
    try:
        shutil.copytree(
            source,
            staging,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*_IGNORED_PARTS, "*.pyc", "*.pyo"),
        )
        after, files = source_tree_digest(staging)
        if after != before:
            msg = "automation source changed while ROI-H created its snapshot"
            raise RuntimeError(msg)
        load_source_manifest(staging)
        if target.exists():
            existing, existing_files = source_tree_digest(target)
            if existing != after:
                msg = f"automation source snapshot already exists with different content: {target}"
                raise FileExistsError(msg)
            _make_tree_read_only(target)
            return SourceSnapshot(
                source_digest=existing,
                root=target,
                manifest=manifest,
                files=existing_files,
            )
        staging.replace(target)
        _make_tree_read_only(target)
        return SourceSnapshot(
            source_digest=after,
            root=target,
            manifest=manifest,
            files=files,
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def put_source(
    sources_root: str | Path,
    name: str,
    manifest: dict[str, Any],
    files: dict[str, str],
) -> SourceSnapshot:
    """Atomically create or replace one project-owned automation source tree."""
    parsed = AutomationSourceManifest.model_validate(manifest)
    if parsed.name != name:
        msg = f"automation source name mismatch: expected {name!r}, got {parsed.name!r}"
        raise ValueError(msg)
    root = Path(sources_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with source_lease(root, name):
        _recover_source_update(root, name)
        return _put_source_locked(root, name, parsed, files)


def _put_source_locked(
    root: Path,
    name: str,
    parsed: AutomationSourceManifest,
    files: dict[str, str],
) -> SourceSnapshot:
    """Replace one source while its managed source lease is held."""
    target = root / name
    if not target.resolve().is_relative_to(root):
        msg = f"automation source escapes its managed root: {name!r}"
        raise ValueError(msg)
    staging = Path(tempfile.mkdtemp(prefix=f".{name}.source-", dir=root))
    previous = root / f".{name}.previous-{uuid.uuid4().hex}"
    journal = root / ".updates" / f"{name}.json"
    moved_previous = False
    try:
        total = 0
        for raw_path, content in files.items():
            relative = PurePosixPath(raw_path.replace("\\", "/"))
            if (
                relative.is_absolute()
                or not relative.parts
                or ".." in relative.parts
                or relative.as_posix() == "automation.json"
            ):
                msg = f"invalid automation source file path: {raw_path!r}"
                raise ValueError(msg)
            encoded = content.encode("utf-8")
            if len(encoded) > _MAX_SOURCE_FILE_BYTES:
                msg = f"automation source file is too large: {raw_path!r}"
                raise ValueError(msg)
            total += len(encoded)
            if total > _MAX_SOURCE_TREE_BYTES:
                msg = "automation source tree is too large"
                raise ValueError(msg)
            destination = staging.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        atomic_write_json(
            staging / "automation.json",
            parsed.model_dump(mode="json", by_alias=True),
            mode=0o600,
        )
        loaded = load_source_manifest(staging)
        digest, source_files = source_tree_digest(staging)
        atomic_write_json(
            journal,
            {
                "schema_version": 1,
                "target": target.name,
                "previous": previous.name,
                "staging": staging.name,
            },
            mode=0o600,
        )
        if target.exists():
            target.replace(previous)
            moved_previous = True
        try:
            staging.replace(target)
        except Exception:
            if moved_previous and previous.exists() and not target.exists():
                previous.replace(target)
            raise
        if previous.exists():
            shutil.rmtree(previous)
        journal.unlink(missing_ok=True)
        return SourceSnapshot(
            source_digest=digest,
            root=target,
            manifest=loaded,
            files=source_files,
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if previous.exists() and target.exists():
            shutil.rmtree(previous)


def show_source(source_root: str | Path) -> dict[str, Any]:
    """Return one validated source tree as portable text files."""
    root = Path(source_root).expanduser().resolve()
    with source_lease(root.parent, root.name):
        _recover_source_update(root.parent, root.name)
        return _show_source_locked(root)


def _show_source_locked(root: Path) -> dict[str, Any]:
    """Read one source while its managed source lease is held."""
    manifest = load_source_manifest(root)
    digest, paths = source_tree_digest(root)
    files = {
        relative: (root / relative).read_text(encoding="utf-8")
        for relative in paths
        if relative != "automation.json"
    }
    return {
        "name": manifest.name,
        "source_digest": digest,
        "manifest": manifest.model_dump(mode="json", by_alias=True),
        "files": files,
    }


def source_lease(
    sources_root: str | Path,
    name: str,
    *,
    timeout_seconds: float = 30.0,
) -> RunLease:
    """Create the shared managed-source lease for readers and writers."""
    if not _AUTOMATION_NAME.fullmatch(name):
        msg = f"invalid automation name: {name!r}"
        raise ValueError(msg)
    root = Path(sources_root).expanduser().resolve()
    return RunLease(
        path=root / ".locks" / f"source-{name}.lock",
        run_id=f"source-{name}",
        timeout_seconds=timeout_seconds,
    )


def _recover_source_update(root: Path, name: str) -> None:
    """Recover or finish one interrupted source pointer update."""
    journal = root / ".updates" / f"{name}.json"
    if not journal.is_file():
        return
    raw = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"automation source update journal is invalid: {name}"
        raise TypeError(msg)
    target = root / str(raw.get("target") or "")
    previous = root / str(raw.get("previous") or "")
    staging = root / str(raw.get("staging") or "")
    for path in (target, previous, staging):
        if path.parent != root:
            msg = f"automation source update journal escapes its root: {name}"
            raise ValueError(msg)
    if not target.exists() and previous.is_dir():
        previous.replace(target)
    elif target.is_dir() and previous.is_dir():
        shutil.rmtree(previous)
    if staging.is_dir() and staging != target:
        shutil.rmtree(staging)
    journal.unlink(missing_ok=True)


def _make_tree_read_only(root: Path) -> None:
    """Remove normal write access from a frozen source tree."""
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(stat.S_IREAD | (stat.S_IEXEC if path.is_dir() else 0))
    root.chmod(stat.S_IREAD | stat.S_IEXEC)


def make_tree_writable(root: Path) -> None:
    """Restore owner write access before a managed tree is removed."""
    if not root.exists():
        return
    root.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        path.chmod(stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if path.is_dir() else 0))


__all__ = [
    "AutomationSourceManifest",
    "PhaseResult",
    "PhaseSpec",
    "SourceSnapshot",
    "load_source_manifest",
    "make_tree_writable",
    "put_source",
    "show_source",
    "snapshot_source",
    "source_lease",
    "source_tree_digest",
]
