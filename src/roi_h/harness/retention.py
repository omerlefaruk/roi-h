"""Plan-first cleanup for disposable run storage only."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roi_h.harness.atomicfs import atomic_write_json, hash_file
from roi_h.harness.lease import project_policy_lease, run_lease
from roi_h.harness.workspace import Workspace, validate_log_retention


@dataclass(frozen=True)
class RetentionPlan:
    """Persisted exact cleanup targets and source-state fingerprint."""

    plan_id: str
    project_id: str
    environment: str
    created_at: str
    fingerprint: str
    targets: tuple[dict[str, Any], ...]
    bytes: int
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetentionResult:
    """Applied cleanup identities and bytes."""

    ok: bool
    plan_id: str
    deleted: tuple[str, ...]
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetentionPlanner:
    """Discover disposable targets; callers never supply arbitrary delete paths."""

    def plan(self, workspace: Workspace, policy: dict[str, Any] | None = None) -> RetentionPlan:
        """Persist a plan for expired logs from closed runs only."""
        retention = _log_retention(workspace, policy)
        now = datetime.now(UTC)
        targets = [
            target
            for run in (workspace.runs.iterdir() if workspace.runs.is_dir() else ())
            if (target := _run_log_target(workspace, run, retention, now)) is not None
        ]
        plan_id = f"gc_{uuid.uuid4().hex}"
        created_at = datetime.now(UTC).isoformat()
        byte_count = sum(int(item["bytes"]) for item in targets)
        fingerprint = _plan_fingerprint(
            plan_id=plan_id,
            project_id=workspace.project_id,
            environment=workspace.env,
            created_at=created_at,
            targets=targets,
            byte_count=byte_count,
            blockers=[],
        )
        result = RetentionPlan(
            plan_id=plan_id,
            project_id=workspace.project_id,
            environment=workspace.env,
            created_at=created_at,
            fingerprint=fingerprint,
            targets=tuple(targets),
            bytes=byte_count,
        )
        atomic_write_json(_plan_path(workspace, plan_id), result.to_dict(), mode=0o600)
        return result

    def inspect(self, workspace: Workspace, plan_id: str) -> RetentionPlan:
        """Load a persisted plan."""
        path = _plan_path(workspace, plan_id)
        if not path.is_file():
            msg = f"retention plan not found: {plan_id}"
            raise FileNotFoundError(msg)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RetentionPlan(
            plan_id=str(raw["plan_id"]),
            project_id=str(raw["project_id"]),
            environment=str(raw["environment"]),
            created_at=str(raw["created_at"]),
            fingerprint=str(raw["fingerprint"]),
            targets=tuple(raw.get("targets") or []),
            bytes=int(raw.get("bytes") or 0),
            blockers=tuple(raw.get("blockers") or []),
        )

    def apply(self, workspace: Workspace, plan_id: str) -> RetentionResult:
        """Apply only when every exact target still matches the reviewed plan."""
        plan = self.inspect(workspace, plan_id)
        planned = list(plan.targets)
        if _plan_fingerprint_from_plan(plan, planned, plan.bytes) != plan.fingerprint:
            msg = "retention.plan_stale: retention plan was changed after creation"
            raise RuntimeError(msg)
        deleted: list[str] = []
        with ExitStack() as leases:
            leases.enter_context(project_policy_lease(workspace.project_root))
            current_retention = _log_retention(workspace, None)
            for run_id in sorted({str(item.get("run_id") or "") for item in planned}):
                leases.enter_context(run_lease(workspace, run_id))
            refreshed = [_refresh_target(workspace, item, current_retention) for item in planned]
            refreshed_bytes = sum(int(item["bytes"]) for item in refreshed)
            if _plan_fingerprint_from_plan(plan, refreshed, refreshed_bytes) != plan.fingerprint:
                msg = "retention.plan_stale: storage or policy changed after plan creation"
                raise RuntimeError(msg)
            for item in refreshed:
                path = workspace.project_root / str(item["relative_path"])
                _assert_contained(path, workspace.project_root)
                if path.is_dir():
                    shutil.rmtree(path)
                    path.mkdir(mode=0o700)
                elif path.is_file() and not path.is_symlink():
                    path.unlink()
                deleted.append(str(item["relative_path"]))
        _plan_path(workspace, plan_id).unlink()
        return RetentionResult(
            ok=True,
            plan_id=plan_id,
            deleted=tuple(deleted),
            bytes=plan.bytes,
        )


def _log_retention(workspace: Workspace, policy: dict[str, Any] | None) -> str:
    if policy and policy.get("log_retention") is not None:
        return validate_log_retention(str(policy["log_retention"]))
    raw = json.loads(workspace.config_path.read_text(encoding="utf-8"))
    retention = raw.get("retention") if isinstance(raw, dict) else None
    value = retention.get("log_retention", "7d") if isinstance(retention, dict) else "7d"
    return validate_log_retention(str(value))


def _run_log_target(
    workspace: Workspace,
    run: Path,
    retention: str,
    now: datetime,
) -> dict[str, Any] | None:
    if retention == "forever" or not run.is_dir() or run.name.startswith("."):
        return None
    manifest = run / "run-files.json"
    if not manifest.is_file():
        return None
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        closed_at = datetime.fromisoformat(str(raw["finalized_at"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if raw.get("state") != "terminal" or not raw.get("terminal_status"):
        return None
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=UTC)
    days = int(retention[:-1])
    closed_at = closed_at.astimezone(UTC)
    if closed_at > now or (now - closed_at).days < days:
        return None
    logs = run / "diagnostics"
    if logs.is_symlink() or not logs.is_dir():
        return None
    if not any(item.is_file() and not item.is_symlink() for item in logs.rglob("*")):
        return None
    return _target(
        workspace,
        logs,
        "expired-run-logs",
        run_id=run.name,
        closed_at=closed_at.isoformat(),
        terminal_status=str(raw["terminal_status"]),
        retention=retention,
    )


def _target(
    workspace: Workspace,
    path: Path,
    classification: str,
    **metadata: Any,
) -> dict[str, Any]:
    entries = sorted(path.rglob("*"))
    file_state = []
    for item in entries:
        stat = item.lstat()
        state: dict[str, Any] = {
            "path": item.relative_to(path).as_posix(),
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "mode": stat.st_mode,
        }
        if item.is_symlink():
            state.update(kind="symlink", target=str(item.readlink()))
        elif item.is_file():
            digest, size = hash_file(item)
            state.update(kind="file", bytes=size, sha256=digest)
        elif item.is_dir():
            state["kind"] = "directory"
        else:
            state["kind"] = "special"
        file_state.append(state)
    return {
        "relative_path": path.relative_to(workspace.project_root).as_posix(),
        "classification": classification,
        **metadata,
        "bytes": sum(int(str(item["bytes"])) for item in file_state if item.get("kind") == "file"),
        "files": file_state,
    }


def _refresh_target(
    workspace: Workspace,
    item: dict[str, Any],
    current_retention: str,
) -> dict[str, Any]:
    run = workspace.runs / str(item.get("run_id") or "")
    refreshed = _run_log_target(
        workspace,
        run,
        current_retention,
        datetime.now(UTC),
    )
    return refreshed or {**item, "bytes": -1, "files": []}


def _plan_fingerprint_from_plan(
    plan: RetentionPlan,
    targets: list[dict[str, Any]],
    byte_count: int,
) -> str:
    return _plan_fingerprint(
        plan_id=plan.plan_id,
        project_id=plan.project_id,
        environment=plan.environment,
        created_at=plan.created_at,
        targets=targets,
        byte_count=byte_count,
        blockers=list(plan.blockers),
    )


def _plan_fingerprint(
    *,
    plan_id: str,
    project_id: str,
    environment: str,
    created_at: str,
    targets: list[dict[str, Any]],
    byte_count: int,
    blockers: list[str],
) -> str:
    canonical = json.dumps(
        {
            "plan_id": plan_id,
            "project_id": project_id,
            "environment": environment,
            "created_at": created_at,
            "targets": targets,
            "bytes": byte_count,
            "blockers": blockers,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _plan_path(workspace: Workspace, plan_id: str) -> Path:
    if not plan_id.startswith("gc_") or not plan_id[3:].isalnum():
        msg = f"invalid retention plan id: {plan_id!r}"
        raise ValueError(msg)
    return workspace.runtime / "retention-plans" / f"{plan_id}.json"


def _assert_contained(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        msg = "retention.plan_stale: target escaped project root"
        raise RuntimeError(msg) from exc


__all__ = ["RetentionPlan", "RetentionPlanner", "RetentionResult"]
