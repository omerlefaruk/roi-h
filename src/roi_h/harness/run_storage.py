"""Run workspace lifecycle and atomic durable artifact attachment."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from roi_h.harness.atomicfs import atomic_write_json, hash_file
from roi_h.harness.lease import run_lease
from roi_h.harness.logical_paths import LogicalPath, PathResolver, PathScope, validate_run_id
from roi_h.harness.workspace import Workspace

_ARTIFACT_ID = re.compile(r"^art_[A-Za-z0-9_-]{8,128}$")
_ARTIFACT_FILE = re.compile(r"^(art_[A-Za-z0-9_-]{8,128})--(.+)$")
_STAGING_PREFIX = ".attach-"


@dataclass(frozen=True)
class RunPaths:
    """Typed physical roots owned by one run."""

    root: Path
    workspace: Path
    input: Path
    work: Path
    output: Path
    tmp: Path
    artifacts: Path
    phases: Path
    runtime: Path
    diagnostics: Path
    manifest: Path


@dataclass(frozen=True)
class ArtifactAttachment:
    """Portable artifact identity and immutable byte metadata."""

    artifact_id: str
    run_id: str
    name: str
    uri: str
    sha256: str
    bytes: int
    media_type: str
    source: str
    path: Path
    created_at: str

    def to_dict(self, *, include_physical: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": True,
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "name": self.name,
            "uri": self.uri,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "media_type": self.media_type,
            "source": self.source,
            "created_at": self.created_at,
        }
        if include_physical:
            data["physical_path"] = str(self.path)
        return data


class RunStorage:
    """Deep storage module for run preparation, artifacts, and finalization."""

    def __init__(self, workspace: Workspace) -> None:
        """Bind storage operations to one selected project environment."""
        self.workspace = workspace
        self.resolver = PathResolver()

    def paths(self, run_id: str) -> RunPaths:
        validate_run_id(run_id)
        root = self.workspace.runs / run_id
        workspace = root / "workspace"
        return RunPaths(
            root=root,
            workspace=workspace,
            input=workspace / "input",
            work=workspace / "work",
            output=workspace / "output",
            tmp=workspace / "tmp",
            artifacts=root / "artifacts",
            phases=root / "phases",
            runtime=root / "runtime",
            diagnostics=root / "diagnostics",
            manifest=root / "run-files.json",
        )

    def prepare(self, run_id: str) -> RunPaths:
        """Create a complete run tree through same-filesystem staging."""
        paths = self.paths(run_id)
        if paths.root.exists():
            self._validate_existing(paths)
            return paths
        staging = paths.root.with_name(f".{run_id}.prepare-{uuid.uuid4().hex}")
        try:
            for relative in (
                "workspace/input",
                "workspace/work",
                "workspace/output",
                "workspace/tmp",
                "artifacts",
                "phases",
                "runtime",
                "diagnostics",
            ):
                (staging / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write_json(
                staging / "run-files.json",
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "project_id": self.workspace.project_id,
                    "environment": self.workspace.env,
                    "created_at": datetime.now(UTC).isoformat(),
                    "state": "prepared",
                },
                mode=0o600,
            )
            staging.replace(paths.root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return paths

    def activate(self, run_id: str, *, lease_held: bool = False) -> RunPaths:
        """Mark one run workspace active under its mutation lease."""
        paths = self.prepare(run_id)
        if lease_held:
            self._mark_active(paths, run_id)
        else:
            with run_lease(self.workspace, run_id):
                self._mark_active(paths, run_id)
        return paths

    def _mark_active(self, paths: RunPaths, run_id: str) -> None:
        raw = json.loads(paths.manifest.read_text(encoding="utf-8"))
        manifest = raw if isinstance(raw, dict) else {}
        manifest.update(
            run_id=run_id,
            state="active",
            terminal_status=None,
            finalized_at=None,
            activated_at=datetime.now(UTC).isoformat(),
        )
        atomic_write_json(paths.manifest, manifest, mode=0o600)

    def attach(
        self,
        run_id: str,
        source: str | Path | LogicalPath,
        *,
        name: str | None = None,
        media_type: str | None = None,
    ) -> ArtifactAttachment:
        """Atomically attach one regular file and return its logical identity."""
        paths = self.prepare(run_id)
        source_path, logical_source = self._source(run_id, source)
        if source_path.is_symlink() or not source_path.is_file():
            msg = f"artifact source is not a regular file: {source_path}"
            raise FileNotFoundError(msg)
        dest_name = name or source_path.name
        _validate_artifact_name(dest_name)

        digest, byte_count = hash_file(source_path)
        same_name = next(
            (item for item in self.list(run_id) if item.name == dest_name),
            None,
        )
        if same_name is not None:
            if same_name.sha256 == digest:
                return same_name
            msg = f"artifact.identity_conflict: {dest_name!r} already has a different digest"
            raise FileExistsError(msg)

        artifact_id = f"art_{uuid.uuid4().hex}"
        final = paths.artifacts / f"{artifact_id}--{dest_name}"
        fd, raw_staging = tempfile.mkstemp(prefix=_STAGING_PREFIX, dir=paths.artifacts)
        staging = Path(raw_staging)
        copied_hash = hashlib.sha256()
        copied_bytes = 0
        try:
            with source_path.open("rb") as reader, os.fdopen(fd, "wb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                    copied_hash.update(chunk)
                    copied_bytes += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            copied_digest = f"sha256:{copied_hash.hexdigest()}"
            if copied_digest != digest or copied_bytes != byte_count:
                msg = "artifact source changed while it was being attached"
                raise OSError(msg)  # noqa: TRY301
            staging.chmod(0o600)
            staging.replace(final)
            _sync_directory(paths.artifacts)
        except Exception:
            if staging.exists():
                staging.unlink()
            raise
        return ArtifactAttachment(
            artifact_id=artifact_id,
            run_id=run_id,
            name=dest_name,
            uri=f"artifact://{artifact_id}",
            sha256=digest,
            bytes=byte_count,
            media_type=(
                media_type or mimetypes.guess_type(dest_name)[0] or "application/octet-stream"
            ),
            source=logical_source,
            path=final,
            created_at=datetime.now(UTC).isoformat(),
        )

    def list(self, run_id: str) -> list[ArtifactAttachment]:
        """List immutable artifact files without exposing scratch files."""
        paths = self.paths(run_id)
        if not paths.artifacts.is_dir():
            return []
        items: list[ArtifactAttachment] = []
        for path in sorted(paths.artifacts.iterdir()):
            match = _ARTIFACT_FILE.fullmatch(path.name)
            if (
                match is None
                or not path.is_file()
                or path.is_symlink()
                or path.name.startswith(_STAGING_PREFIX)
            ):
                continue
            digest, size = hash_file(path)
            artifact_id, name = match.groups()
            items.append(
                ArtifactAttachment(
                    artifact_id=artifact_id,
                    run_id=run_id,
                    name=name,
                    uri=f"artifact://{artifact_id}",
                    sha256=digest,
                    bytes=size,
                    media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    source="",
                    path=path.resolve(),
                    created_at=datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                )
            )
        return items

    def open_artifact(self, run_id: str, artifact_id: str) -> BinaryIO:
        """Open an artifact by identity after containment validation."""
        if not _ARTIFACT_ID.fullmatch(artifact_id):
            msg = f"invalid artifact id: {artifact_id!r}"
            raise ValueError(msg)
        root = self.paths(run_id).artifacts
        files = [
            item
            for item in root.glob(f"{artifact_id}--*")
            if item.is_file() and not item.is_symlink()
        ]
        if len(files) != 1:
            msg = f"artifact.file_missing: {artifact_id}"
            raise FileNotFoundError(msg)
        return files[0].open("rb")

    def artifact_path(
        self,
        run_id: str,
        *,
        artifact_id: str | None = None,
        name: str | None = None,
    ) -> Path:
        """Resolve an artifact by stable id or compatibility filename."""
        items = self.list(run_id)
        found = next(
            (
                item
                for item in items
                if (artifact_id is not None and item.artifact_id == artifact_id)
                or (name is not None and item.name == name)
            ),
            None,
        )
        if found is None:
            identity = artifact_id or name
            msg = f"artifact.file_missing: {identity!r} for run {run_id}"
            raise FileNotFoundError(msg)
        return found.path

    def finalize(self, run_id: str, *, status: str | None = None) -> dict[str, Any]:
        """Remove disposable tmp data and mark filesystem retention eligibility."""
        paths = self.paths(run_id)
        removed = 0
        if paths.tmp.is_dir():
            removed = sum(item.stat().st_size for item in paths.tmp.rglob("*") if item.is_file())
            shutil.rmtree(paths.tmp)
            paths.tmp.mkdir(mode=0o700)
        manifest: dict[str, Any] = {}
        if paths.manifest.is_file():
            raw = json.loads(paths.manifest.read_text(encoding="utf-8"))
            manifest = raw if isinstance(raw, dict) else {}
        manifest.update(
            {
                "schema_version": 1,
                "run_id": run_id,
                "project_id": self.workspace.project_id,
                "environment": self.workspace.env,
                "state": "terminal" if status else manifest.get("state", "prepared"),
                "terminal_status": status,
                "finalized_at": datetime.now(UTC).isoformat(),
            }
        )
        atomic_write_json(paths.manifest, manifest, mode=0o600)
        return {"ok": True, "run_id": run_id, "tmp_bytes_removed": removed}

    def reconcile(self, run_id: str, *, repair: bool = False) -> dict[str, Any]:
        """Remove only stale hidden attach files; report ambiguous directories."""
        paths = self.paths(run_id)
        staging = (
            list(paths.artifacts.glob(f"{_STAGING_PREFIX}*")) if paths.artifacts.exists() else []
        )
        removed: list[str] = []
        if repair:
            for path in staging:
                if path.is_file() and not path.is_symlink():
                    path.unlink()
                    removed.append(str(path.relative_to(paths.root)))
        malformed = (
            [
                str(path.relative_to(paths.root))
                for path in paths.artifacts.iterdir()
                if paths.artifacts.is_dir()
                and not path.name.startswith(_STAGING_PREFIX)
                and not _ARTIFACT_FILE.fullmatch(path.name)
            ]
            if paths.artifacts.is_dir()
            else []
        )
        return {
            "ok": not malformed,
            "run_id": run_id,
            "staging_files": len(staging),
            "removed": removed,
            "malformed": malformed,
        }

    def _source(
        self,
        run_id: str,
        source: str | Path | LogicalPath,
    ) -> tuple[Path, str]:
        if isinstance(source, LogicalPath) or (isinstance(source, str) and "://" in source):
            parsed = source if isinstance(source, LogicalPath) else LogicalPath.parse(source)
            if parsed.scheme not in {"project", "run", "automation"}:
                msg = "artifact source must be project, run, or automation content"
                raise ValueError(msg)
            path = self.resolver.resolve(
                parsed,
                PathScope(self.workspace, run_id=run_id),
                "read",
            ).physical
            return path, str(parsed)
        path = Path(source).expanduser().resolve()
        try:
            logical_source = str(
                self.resolver.normalize(path, PathScope(self.workspace, run_id=run_id))
            )
        except ValueError:
            # Explicit artifact ingress is an operator seam; no physical value is persisted.
            logical_source = f"run://input/{path.name}"
        return path, logical_source

    @staticmethod
    def _validate_existing(paths: RunPaths) -> None:
        expected = (
            paths.input,
            paths.work,
            paths.output,
            paths.tmp,
            paths.artifacts,
            paths.phases,
            paths.runtime,
            paths.diagnostics,
        )
        missing = [str(item) for item in expected if not item.is_dir()]
        if missing:
            msg = f"run storage is incomplete: {missing}"
            raise RuntimeError(msg)


def _validate_artifact_name(name: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or len(name.encode("utf-8")) > 255
    ):
        msg = f"invalid artifact name: {name!r}"
        raise ValueError(msg)


def _sync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


__all__ = ["ArtifactAttachment", "RunPaths", "RunStorage"]
