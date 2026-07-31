"""Manifest-driven project export/import with secure staged activation."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from roi_h.harness.atomicfs import hash_file
from roi_h.harness.store_lifecycle import StoreLifecycle
from roi_h.harness.workspace import (
    HOME_LAYOUT_VERSION,
    Workspace,
    list_projects,
    resolve_home,
    set_active_project,
    validate_project_name,
)

ArchiveMode = Literal["definition", "full"]
_MAX_ENTRIES = 100_000
_MAX_UNCOMPRESSED = 100 * 1024 * 1024 * 1024
_MAX_RATIO = 1_000


@dataclass(frozen=True)
class ArchiveInspection:
    """Verified archive summary without extraction."""

    ok: bool
    path: str
    format_version: int
    mode: str
    project_id: str
    slug: str
    files: int
    bytes: int
    environments: tuple[str, ...]
    required_secrets: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExportResult:
    """Atomically published project archive."""

    ok: bool
    path: str
    mode: ArchiveMode
    project_id: str
    project: str
    files: int
    bytes: int
    sha256: str
    excluded: tuple[str, ...]
    missing_secrets: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImportResult:
    """Verified import activation or verify-only report."""

    ok: bool
    changed: bool
    verified_only: bool
    project_id: str
    project: str
    path: str | None
    mode: str
    files: int
    missing_secrets: tuple[str, ...]
    trust_review_required: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProjectArchive:
    """Hide store snapshots, archive security, staging, and activation."""

    def inspect(self, source: str | Path) -> ArchiveInspection:
        path = Path(source).expanduser().resolve()
        manifest, infos = _verify_archive(path)
        project = manifest["project"]
        return ArchiveInspection(
            ok=True,
            path=str(path),
            format_version=int(manifest["format_version"]),
            mode=str(manifest["mode"]),
            project_id=str(project["project_id"]),
            slug=str(project["slug"]),
            files=len(infos),
            bytes=sum(int(item["bytes"]) for item in manifest["files"]),
            environments=tuple(str(item) for item in manifest.get("environments") or []),
            required_secrets=tuple(
                str(item.get("name") if isinstance(item, dict) else item)
                for item in manifest.get("required_secrets") or []
            ),
        )

    def export(
        self,
        workspace: Workspace,
        destination: str | Path,
        *,
        mode: ArchiveMode = "full",
        environments: tuple[str, ...] = ("dev", "prod"),
    ) -> ExportResult:
        if mode not in {"definition", "full"}:
            msg = f"archive.invalid: unsupported export mode {mode!r}"
            raise ValueError(msg)
        target = Path(destination).expanduser().resolve()
        if target.exists():
            msg = f"archive.invalid: destination already exists: {target}"
            raise FileExistsError(msg)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        project = workspace.project_root
        staging_dir = Path(tempfile.mkdtemp(prefix=".roih-export-", dir=target.parent))
        stage_archive = staging_dir / target.name
        snapshot_dir = staging_dir / "snapshots"
        snapshot_dir.mkdir(mode=0o700)
        entries: list[tuple[str, Path, str]] = []
        try:
            entries.extend(_definition_entries(project, environments))
            if mode == "full":
                for env in environments:
                    selected = Workspace.open(
                        workspace.root,
                        project=workspace.project,
                        env=env,
                    )
                    if not selected.db.is_file():
                        continue
                    backup_path = snapshot_dir / f"{env}.sqlite"
                    StoreLifecycle().backup(selected, backup_path)
                    entries.append((f"stores/{env}/activegraph.sqlite", backup_path, "store"))
                    registered = _registered_run_files(backup_path)
                    entries.extend(_durable_run_entries(selected, registered))
            _assert_archive_entries(entries)
            file_manifest: list[dict[str, Any]] = []
            with zipfile.ZipFile(
                stage_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                strict_timestamps=False,
            ) as archive:
                for archive_path, source, kind in sorted(entries):
                    digest, size = hash_file(source)
                    info = zipfile.ZipInfo(archive_path)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o600 << 16
                    with source.open("rb") as reader, archive.open(info, "w") as writer:
                        shutil.copyfileobj(reader, writer, length=1024 * 1024)
                    file_manifest.append(
                        {
                            "path": archive_path,
                            "bytes": size,
                            "sha256": digest,
                            "kind": kind,
                        }
                    )
                project_manifest = json.loads(
                    (project / "project.json").read_text(encoding="utf-8")
                )
                secrets = json.loads((project / "secrets.meta.json").read_text(encoding="utf-8"))
                manifest = {
                    "format": "roi-h-project",
                    "format_version": 1,
                    "created_at": datetime.now(UTC).isoformat(),
                    "created_by": {
                        "roi_h": _roi_h_version(),
                        "activegraph": "1.10.0",
                        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
                    },
                    "project": {
                        "project_id": project_manifest["project_id"],
                        "slug": project_manifest["slug"],
                        "display_name": project_manifest["display_name"],
                    },
                    "mode": mode,
                    "environments": list(environments),
                    "files": file_manifest,
                    "excluded": [
                        "secret-values",
                        "workspace",
                        "runtime",
                        "diagnostics",
                        "cache",
                        "locks",
                    ],
                    "required_secrets": secrets.get("entries") or [],
                    "compatibility": {
                        "roi_h": ">=0.1,<0.3",
                        "activegraph_schema": "1",
                        "layout_schema": HOME_LAYOUT_VERSION,
                    },
                }
                info = zipfile.ZipInfo("manifest.json")
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(
                    info,
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                )
            _verify_archive(stage_archive)
            stage_archive.chmod(0o600)
            stage_archive.replace(target)
            digest, size = hash_file(target)
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
        return ExportResult(
            ok=True,
            path=str(target),
            mode=mode,
            project_id=workspace.project_id,
            project=workspace.project,
            files=len(entries),
            bytes=size,
            sha256=digest,
            excluded=(
                "secret-values",
                "workspace",
                "runtime",
                "diagnostics",
                "cache",
                "locks",
            ),
            missing_secrets=tuple(
                item["name"]
                for item in json.loads(
                    (project / "secrets.meta.json").read_text(encoding="utf-8")
                ).get("entries", [])
                if workspace.env in item.get("environments", [])
            ),
        )

    def import_archive(
        self,
        source: str | Path,
        target_home: str | Path,
        *,
        name: str | None = None,
        verify_only: bool = False,
        use: bool = False,
    ) -> ImportResult:
        archive_path = Path(source).expanduser().resolve()
        manifest, infos = _verify_archive(archive_path)
        project_info = manifest["project"]
        slug = name or str(project_info["slug"])
        validate_project_name(slug)
        home = resolve_home(target_home)
        existing = list_projects(home)
        if any(item["name"] == slug for item in existing):
            msg = f"archive.invalid: project slug already exists: {slug}"
            raise FileExistsError(msg)
        if any(item.get("project_id") == project_info["project_id"] for item in existing):
            msg = "archive.invalid: stable project identity already exists in this home"
            raise FileExistsError(msg)
        missing = tuple(
            str(item.get("name") if isinstance(item, dict) else item)
            for item in manifest.get("required_secrets") or []
        )
        if verify_only:
            return ImportResult(
                ok=True,
                changed=False,
                verified_only=True,
                project_id=str(project_info["project_id"]),
                project=slug,
                path=None,
                mode=str(manifest["mode"]),
                files=len(infos),
                missing_secrets=missing,
                trust_review_required=_contains_project_skills(manifest),
            )

        projects = home / "projects"
        projects.mkdir(parents=True, exist_ok=True, mode=0o700)
        final = projects / slug
        staging = projects / f".{slug}.import-{uuid.uuid4().hex}"
        try:
            staging.mkdir(mode=0o700)
            with zipfile.ZipFile(archive_path) as archive:
                for item in manifest["files"]:
                    archive_name = str(item["path"])
                    relative = _import_relative_path(archive_name)
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with archive.open(archive_name) as reader, destination.open("wb") as writer:
                        shutil.copyfileobj(reader, writer, length=1024 * 1024)
                    destination.chmod(0o600)
            project_path = staging / "project.json"
            project_manifest = json.loads(project_path.read_text(encoding="utf-8"))
            project_manifest["slug"] = slug
            project_path.write_text(
                json.dumps(project_manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _validate_staged_project(staging, manifest)
            staging.replace(final)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        if use:
            set_active_project(home, slug)
        return ImportResult(
            ok=True,
            changed=True,
            verified_only=False,
            project_id=str(project_info["project_id"]),
            project=slug,
            path=str(final),
            mode=str(manifest["mode"]),
            files=len(infos),
            missing_secrets=missing,
            trust_review_required=_contains_project_skills(manifest),
        )


def _definition_entries(
    project: Path,
    environments: tuple[str, ...],
) -> list[tuple[str, Path, str]]:
    entries: list[tuple[str, Path, str]] = []
    fixed = (
        ("project.json", "project-manifest"),
        ("secrets.meta.json", "secret-declarations"),
    )
    for relative, kind in fixed:
        path = project / relative
        if path.is_file():
            entries.append((f"project/{relative}", path, kind))
    for root_name, kind in (
        ("reference", "reference"),
        ("skills", "project-guidance"),
        ("sources", "automation-source"),
        ("packages", "automation-package"),
        ("channels", "channel"),
    ):
        root = project / root_name
        entries.extend(
            (f"project/{path.relative_to(project).as_posix()}", path, kind)
            for path in _regular_files(root)
        )
    for env in environments:
        path = project / "environments" / env / "environment.json"
        if path.is_file():
            entries.append(
                (f"project/environments/{env}/environment.json", path, "environment-manifest")
            )
    return entries


def _durable_run_entries(
    workspace: Workspace,
    registered: dict[str, set[str]],
) -> list[tuple[str, Path, str]]:
    entries: list[tuple[str, Path, str]] = []
    for run_id, artifact_ids in registered.items():
        root = workspace.runs / run_id
        manifest = root / "run-files.json"
        if manifest.is_file():
            entries.append(
                (
                    f"files/{workspace.env}/runs/{run_id}/run-files.json",
                    manifest,
                    "run-files-manifest",
                )
            )
        artifact_root = root / "artifacts"
        for artifact_id in artifact_ids:
            matches = list(artifact_root.glob(f"{artifact_id}--*"))
            if len(matches) != 1:
                msg = f"artifact.file_missing: {run_id}/{artifact_id}"
                raise RuntimeError(msg)
            path = matches[0]
            entries.append(
                (
                    f"files/{workspace.env}/runs/{run_id}/artifacts/{path.name}",
                    path,
                    "artifact",
                )
            )
        for path in _regular_files(root / "phases"):
            entries.append(
                (
                    (f"files/{workspace.env}/runs/{run_id}/{path.relative_to(root).as_posix()}"),
                    path,
                    "phase-handoff",
                )
            )
    return entries


def _registered_run_files(database: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT run_id, payload FROM events WHERE type = 'object.created' ORDER BY seq"
        )
        for run_id, payload in rows:
            try:
                raw = json.loads(str(payload))
            except json.JSONDecodeError:
                continue
            obj = raw.get("object") if isinstance(raw, dict) else None
            if not isinstance(obj, dict) or obj.get("type") != "rpa.artifact":
                continue
            data = obj.get("data")
            if not isinstance(data, dict) or not data.get("artifact_id"):
                continue
            result.setdefault(str(run_id), set()).add(str(data["artifact_id"]))
    return result


def _regular_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            msg = f"archive.path_unsafe: symlink is not exportable: {path}"
            raise TypeError(msg)
        if path.is_file():
            files.append(path)
    return files


def _assert_archive_entries(entries: list[tuple[str, Path, str]]) -> None:
    names: set[str] = set()
    folded: set[str] = set()
    for archive_path, source, _kind in entries:
        _validate_archive_name(archive_path)
        if not source.is_file() or source.is_symlink():
            msg = f"archive.path_unsafe: not a regular file: {source}"
            raise ValueError(msg)
        if archive_path in names or archive_path.casefold() in folded:
            msg = f"archive.path_unsafe: duplicate/case collision: {archive_path}"
            raise ValueError(msg)
        names.add(archive_path)
        folded.add(archive_path.casefold())


def _verify_archive(path: Path) -> tuple[dict[str, Any], list[zipfile.ZipInfo]]:
    if not path.is_file():
        msg = f"archive.invalid: not found: {path}"
        raise FileNotFoundError(msg)
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > _MAX_ENTRIES:
            msg = "archive.invalid: entry-count limit exceeded"
            raise ValueError(msg)
        names: set[str] = set()
        folded: set[str] = set()
        total = 0
        for info in infos:
            _validate_archive_name(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
                msg = f"archive.path_unsafe: special entry: {info.filename}"
                raise ValueError(msg)
            if info.filename in names or info.filename.casefold() in folded:
                msg = f"archive.path_unsafe: duplicate entry: {info.filename}"
                raise ValueError(msg)
            names.add(info.filename)
            folded.add(info.filename.casefold())
            total += info.file_size
            if total > _MAX_UNCOMPRESSED:
                msg = "archive.invalid: uncompressed-size limit exceeded"
                raise ValueError(msg)
            if info.compress_size and info.file_size / info.compress_size > _MAX_RATIO:
                msg = f"archive.invalid: compression ratio too high: {info.filename}"
                raise ValueError(msg)
        if "manifest.json" not in names:
            msg = "archive.invalid: manifest.json is missing"
            raise ValueError(msg)
        manifest = json.loads(archive.read("manifest.json"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != "roi-h-project"
            or manifest.get("format_version") != 1
        ):
            msg = "archive.incompatible: unsupported project archive"
            raise ValueError(msg)
        declared = manifest.get("files")
        if not isinstance(declared, list):
            msg = "archive.invalid: manifest files must be a list"
            raise TypeError(msg)
        declared_names = {str(item.get("path")) for item in declared if isinstance(item, dict)}
        if declared_names != names - {"manifest.json"}:
            msg = "archive.invalid: manifest/file entry mismatch"
            raise ValueError(msg)
        for item in declared:
            archive_name = str(item["path"])
            info = archive.getinfo(archive_name)
            digest = hashlib.sha256()
            size = 0
            with archive.open(info) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != item.get("bytes") or f"sha256:{digest.hexdigest()}" != item.get("sha256"):
                msg = f"archive.digest_mismatch: {archive_name}"
                raise ValueError(msg)
    return manifest, [item for item in infos if item.filename != "manifest.json"]


def _validate_archive_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith(("/", "\\"))
        or "\\" in name
        or ":" in path.parts[0]
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        msg = f"archive.path_unsafe: {name!r}"
        raise ValueError(msg)


def _import_relative_path(archive_name: str) -> Path:
    parts = PurePosixPath(archive_name).parts
    if parts[0] == "project":
        return Path(*parts[1:])
    if parts[0] == "stores" and len(parts) == 3:
        return Path("environments", parts[1], "store", parts[2])
    if parts[0] == "files" and len(parts) >= 3:
        return Path("environments", parts[1], *parts[2:])
    msg = f"archive.path_unsafe: unsupported archive root: {archive_name}"
    raise ValueError(msg)


def _validate_staged_project(staging: Path, manifest: dict[str, Any]) -> None:
    project = json.loads((staging / "project.json").read_text(encoding="utf-8"))
    if project.get("project_id") != manifest["project"]["project_id"]:
        msg = "archive.invalid: staged project identity mismatch"
        raise ValueError(msg)
    for env in manifest.get("environments") or []:
        env_manifest = staging / "environments" / str(env) / "environment.json"
        if not env_manifest.is_file():
            msg = f"archive.invalid: environment manifest missing: {env}"
            raise ValueError(msg)
        store = staging / "environments" / str(env) / "store" / "activegraph.sqlite"
        if store.is_file():
            with sqlite3.connect(store) as connection:
                if str(connection.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
                    msg = f"archive.invalid: imported store failed integrity: {env}"
                    raise ValueError(msg)


def _contains_project_skills(manifest: dict[str, Any]) -> bool:
    return any(
        str(item.get("kind")) == "project-guidance"
        for item in manifest.get("files") or []
        if isinstance(item, dict)
    )


def _roi_h_version() -> str:
    try:
        return version("roi-h")
    except PackageNotFoundError:
        return "0.1.0"


__all__ = [
    "ArchiveInspection",
    "ExportResult",
    "ImportResult",
    "ProjectArchive",
]
