"""Plan-first cleanup for disposable run storage only."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.workspace import Workspace


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
        """Persist a conservative plan containing only tmp and stale staging."""
        del policy
        targets: list[dict[str, Any]] = []
        for run in workspace.runs.iterdir() if workspace.runs.is_dir() else []:
            if not run.is_dir() or run.name.startswith("."):
                continue
            tmp = run / "workspace" / "tmp"
            if tmp.is_dir() and any(tmp.iterdir()):
                targets.append(_target(workspace, tmp, "disposable-tmp"))
            artifact_root = run / "artifacts"
            if artifact_root.is_dir():
                targets.extend(
                    _target(workspace, path, "stale-artifact-staging")
                    for path in artifact_root.glob(".attach-*")
                    if path.is_file() and not path.is_symlink()
                )
        plan_id = f"gc_{uuid.uuid4().hex}"
        fingerprint = _fingerprint(targets)
        result = RetentionPlan(
            plan_id=plan_id,
            project_id=workspace.project_id,
            environment=workspace.env,
            created_at=datetime.now(UTC).isoformat(),
            fingerprint=fingerprint,
            targets=tuple(targets),
            bytes=sum(int(item["bytes"]) for item in targets),
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
        refreshed = [_refresh_target(workspace, item) for item in plan.targets]
        if _fingerprint(refreshed) != plan.fingerprint:
            msg = "retention.plan_stale: storage changed after this plan was created"
            raise RuntimeError(msg)
        deleted: list[str] = []
        for item in plan.targets:
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


def _target(workspace: Workspace, path: Path, classification: str) -> dict[str, Any]:
    files = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
    return {
        "relative_path": path.relative_to(workspace.project_root).as_posix(),
        "classification": classification,
        "bytes": sum(item.stat().st_size for item in files),
        "mtime_ns": max(
            (item.stat().st_mtime_ns for item in files),
            default=path.stat().st_mtime_ns,
        ),
    }


def _refresh_target(workspace: Workspace, item: dict[str, Any]) -> dict[str, Any]:
    path = workspace.project_root / str(item["relative_path"])
    if not path.exists():
        return {**item, "bytes": -1, "mtime_ns": -1}
    return _target(workspace, path, str(item["classification"]))


def _fingerprint(targets: list[dict[str, Any]]) -> str:
    canonical = json.dumps(targets, sort_keys=True, separators=(",", ":")).encode()
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
