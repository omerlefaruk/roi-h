"""Operational lifecycle around the ActiveGraph SQLite store."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from roi_h.harness.atomicfs import atomic_write_json, hash_file
from roi_h.harness.lease import RunLease
from roi_h.harness.workspace import HOME_LAYOUT_VERSION, Workspace

CheckLevel = Literal["quick", "full"]


@dataclass(frozen=True)
class StoreStatus:
    """Observed store identity and effective SQLite configuration."""

    ok: bool
    identity: str
    path: str
    exists: bool
    bytes: int
    layout_version: int
    activegraph_schema_version: str | None
    journal_mode: str | None
    synchronous: int | None
    busy_timeout_ms: int | None
    wal_present: bool
    shm_present: bool
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StoreCheck:
    """Read-only health check result."""

    ok: bool
    level: CheckLevel
    status: StoreStatus
    checks: dict[str, Any]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.to_dict()
        return data


@dataclass(frozen=True)
class StoreBackup:
    """Published consistent SQLite backup and its manifest."""

    ok: bool
    source_identity: str
    path: str
    manifest_path: str
    bytes: int
    sha256: str
    created_at: str
    activegraph_schema_version: str | None
    layout_version: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RestoreResult:
    """Atomic restore outcome."""

    ok: bool
    changed: bool
    restored_from: str
    previous_backup: str | None
    store: StoreStatus

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["store"] = self.store.to_dict()
        return data


@dataclass(frozen=True)
class MigrationResult:
    """Explicit store migration result."""

    ok: bool
    changed: bool
    target: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompactionResult:
    """Plan-first compaction result; apply remains disabled until qualified."""

    ok: bool
    applied: bool
    enabled: bool
    policy: dict[str, Any] = field(default_factory=dict)
    message: str = "compaction is disabled until snapshot and fork-horizon qualification"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StoreLifecycle:
    """SQLite operational concerns without wrapping normal ActiveGraph calls."""

    def inspect(self, workspace: Workspace) -> StoreStatus:
        path = workspace.db
        identity = f"{workspace.project_id}/{workspace.env}/activegraph-sqlite"
        if not path.is_file():
            return StoreStatus(
                ok=False,
                identity=identity,
                path=str(path),
                exists=False,
                bytes=0,
                layout_version=workspace.layout_version,
                activegraph_schema_version=None,
                journal_mode=None,
                synchronous=None,
                busy_timeout_ms=None,
                wal_present=False,
                shm_present=False,
                warnings=("store file does not exist",),
            )
        warnings: list[str] = []
        try:
            with _connect_read_only(path) as connection:
                journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
                timeout = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
                schema = _activegraph_schema(connection)
        except sqlite3.Error as exc:
            msg = f"store.open_failed: {exc}"
            raise RuntimeError(msg) from exc
        if workspace.env == "prod" and synchronous < 2:
            warnings.append(
                "production requested full durability but ActiveGraph reports synchronous=NORMAL"
            )
        return StoreStatus(
            ok=True,
            identity=identity,
            path=str(path),
            exists=True,
            bytes=path.stat().st_size,
            layout_version=workspace.layout_version,
            activegraph_schema_version=schema,
            journal_mode=journal,
            synchronous=synchronous,
            busy_timeout_ms=timeout,
            wal_present=path.with_name(path.name + "-wal").exists(),
            shm_present=path.with_name(path.name + "-shm").exists(),
            warnings=tuple(warnings),
        )

    def check(self, workspace: Workspace, level: CheckLevel = "quick") -> StoreCheck:
        if level not in {"quick", "full"}:
            msg = f"invalid store check level: {level!r}"
            raise ValueError(msg)
        status = self.inspect(workspace)
        if not status.exists:
            return StoreCheck(
                ok=False,
                level=level,
                status=status,
                checks={"file": False},
                errors=("store.open_failed: store file does not exist",),
            )
        checks: dict[str, Any] = {
            "file": True,
            "layout_version": status.layout_version == HOME_LAYOUT_VERSION,
            "journal_mode": status.journal_mode,
            "synchronous": status.synchronous,
            "busy_timeout_ms": status.busy_timeout_ms,
        }
        errors: list[str] = []
        warnings = list(status.warnings)
        try:
            with _connect_read_only(workspace.db) as connection:
                quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                checks["quick_check"] = quick
                if quick != "ok":
                    errors.append(f"store.integrity_failed: {quick}")
                if level == "full":
                    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                    checks["integrity_check"] = integrity
                    if integrity != "ok":
                        errors.append(f"store.integrity_failed: {integrity}")
                    checks.update(_full_sql_checks(connection))
        except sqlite3.OperationalError as exc:
            code = "store.locked" if "locked" in str(exc).lower() else "store.open_failed"
            errors.append(f"{code}: {exc}")
        free = shutil.disk_usage(workspace.db.parent).free
        checks["free_bytes"] = free
        if free < max(status.bytes * 2, 100 * 1024 * 1024):
            warnings.append("available disk space is below the backup safety margin")
        migration_markers = list(workspace.environment_root.glob(".migration-*"))
        checks["migration_markers"] = [path.name for path in migration_markers]
        if migration_markers:
            errors.append("store.migration_failed: stale migration marker present")
        return StoreCheck(
            ok=not errors,
            level=level,
            status=status,
            checks=checks,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def backup(self, workspace: Workspace, destination: str | Path) -> StoreBackup:
        source_status = self.inspect(workspace)
        if not source_status.ok:
            msg = "store.backup_failed: source store is not healthy"
            raise RuntimeError(msg)
        target = Path(destination).expanduser().resolve()
        if target == workspace.db.resolve():
            msg = "store.backup_failed: destination is the live store"
            raise ValueError(msg)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        required = max(source_status.bytes * 2, 16 * 1024 * 1024)
        if shutil.disk_usage(target.parent).free < required:
            msg = "store.backup_failed: insufficient free space"
            raise OSError(msg)
        fd, raw_stage = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(fd)
        staging = Path(raw_stage)
        staging.unlink()
        lock = _maintenance_lock(workspace)
        try:
            with lock, sqlite3.connect(workspace.db) as source, sqlite3.connect(staging) as dest:
                source.execute("PRAGMA busy_timeout = 10000")
                source.backup(dest)
                integrity = str(dest.execute("PRAGMA integrity_check").fetchone()[0])
                if integrity != "ok":
                    msg = f"store.integrity_failed: backup: {integrity}"
                    raise RuntimeError(msg)  # noqa: TRY301
            digest, size = hash_file(staging)
            created_at = datetime.now(UTC).isoformat()
            manifest = {
                "schema_version": 1,
                "kind": "roi-h-activegraph-backup",
                "created_at": created_at,
                "source_identity": source_status.identity,
                "layout_version": workspace.layout_version,
                "activegraph_schema_version": source_status.activegraph_schema_version,
                "bytes": size,
                "sha256": digest,
            }
            staging.chmod(0o600)
            staging.replace(target)
            manifest_path = target.with_name(target.name + ".manifest.json")
            atomic_write_json(manifest_path, manifest, mode=0o600)
        except Exception as exc:
            if staging.exists():
                staging.unlink()
            if isinstance(exc, (OSError, RuntimeError, ValueError)):
                raise
            msg = f"store.backup_failed: {exc}"
            raise RuntimeError(msg) from exc
        return StoreBackup(
            ok=True,
            source_identity=source_status.identity,
            path=str(target),
            manifest_path=str(manifest_path),
            bytes=size,
            sha256=digest,
            created_at=created_at,
            activegraph_schema_version=source_status.activegraph_schema_version,
            layout_version=workspace.layout_version,
        )

    def restore(
        self,
        workspace: Workspace,
        backup: str | Path,
        mode: str = "replace",
    ) -> RestoreResult:
        if mode != "replace":
            msg = "store.restore_failed: only replace mode is supported"
            raise ValueError(msg)
        source = Path(backup).expanduser().resolve()
        _validate_backup(source, workspace)
        live = workspace.db
        live.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        previous: str | None = None
        with _maintenance_lock(workspace):
            if live.is_file():
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                previous_path = live.with_name(f"{live.name}.pre-restore-{stamp}")
                current_status = self.inspect(workspace)
                with (
                    sqlite3.connect(live) as current,
                    sqlite3.connect(previous_path) as previous_store,
                ):
                    current.backup(previous_store)
                previous_digest, previous_size = hash_file(previous_path)
                atomic_write_json(
                    previous_path.with_name(previous_path.name + ".manifest.json"),
                    {
                        "schema_version": 1,
                        "kind": "roi-h-activegraph-backup",
                        "created_at": datetime.now(UTC).isoformat(),
                        "source_identity": current_status.identity,
                        "layout_version": workspace.layout_version,
                        "activegraph_schema_version": (current_status.activegraph_schema_version),
                        "bytes": previous_size,
                        "sha256": previous_digest,
                    },
                    mode=0o600,
                )
                previous = str(previous_path)
            fd, raw_stage = tempfile.mkstemp(prefix=".restore-", dir=live.parent)
            os.close(fd)
            staging = Path(raw_stage)
            try:
                shutil.copyfile(source, staging)
                staging.chmod(0o600)
                with sqlite3.connect(staging) as connection:
                    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                    if integrity != "ok":
                        msg = f"store.restore_failed: {integrity}"
                        raise RuntimeError(msg)
                staging.replace(live)
            finally:
                if staging.exists():
                    staging.unlink()
        status = self.inspect(workspace)
        if not status.ok:
            msg = "store.restore_failed: restored store did not reopen"
            raise RuntimeError(msg)
        return RestoreResult(
            ok=True,
            changed=True,
            restored_from=str(source),
            previous_backup=previous,
            store=status,
        )

    def migrate(self, workspace: Workspace, target: str = "current") -> MigrationResult:
        status = self.inspect(workspace)
        if not status.ok:
            msg = "store.migration_failed: selected store cannot be opened"
            raise RuntimeError(msg)
        return MigrationResult(
            ok=True,
            changed=False,
            target=target,
            message="ActiveGraph owns schema migration; the selected schema is already openable",
        )

    def compact(
        self,
        workspace: Workspace,
        policy: dict[str, Any] | None = None,
        *,
        apply: bool = False,
    ) -> CompactionResult:
        del workspace
        if apply:
            msg = "store.migration_failed: compaction apply is disabled until qualification"
            raise RuntimeError(msg)
        return CompactionResult(ok=True, applied=False, enabled=False, policy=dict(policy or {}))


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _activegraph_schema(connection: sqlite3.Connection) -> str | None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if exists is None:
        return None
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return str(row[0]) if row else None


def _full_sql_checks(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    expected = {"events", "runs", "meta"}
    result: dict[str, Any] = {
        "tables": sorted(tables),
        "required_tables": expected.issubset(tables),
    }
    if "runs" in tables:
        result["runs"] = int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
    if "events" in tables:
        result["events"] = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        duplicate = connection.execute(
            "SELECT run_id, COUNT(*) FROM events GROUP BY seq HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        result["event_sequence_unique"] = duplicate is None
    return result


def _maintenance_lock(workspace: Workspace) -> RunLease:
    return RunLease(
        path=workspace.runtime / "locks" / "maintenance.lock",
        run_id="maintenance",
        timeout_seconds=10.0,
    )


def _validate_backup(source: Path, workspace: Workspace) -> None:
    if not source.is_file():
        msg = f"store.restore_failed: backup not found: {source}"
        raise FileNotFoundError(msg)
    manifest_path = source.with_name(source.name + ".manifest.json")
    if not manifest_path.is_file():
        msg = f"store.restore_failed: manifest missing: {manifest_path}"
        raise FileNotFoundError(msg)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("kind") != "roi-h-activegraph-backup":
        msg = "store.restore_failed: invalid backup manifest"
        raise ValueError(msg)
    digest, size = hash_file(source)
    if digest != raw.get("sha256") or size != raw.get("bytes"):
        msg = "store.restore_failed: backup digest mismatch"
        raise ValueError(msg)
    if int(raw.get("layout_version") or 0) > workspace.layout_version:
        msg = "store.restore_failed: backup layout is newer than this application"
        raise ValueError(msg)
    with sqlite3.connect(source) as connection:
        if str(connection.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
            msg = "store.restore_failed: backup integrity check failed"
            raise ValueError(msg)


__all__ = [
    "CompactionResult",
    "MigrationResult",
    "RestoreResult",
    "StoreBackup",
    "StoreCheck",
    "StoreLifecycle",
    "StoreStatus",
]
