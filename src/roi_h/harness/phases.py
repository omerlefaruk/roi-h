"""Phase handoff packages: stable restart boundaries for RPA runs."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.domain import HandoffManifest, validate_phase_name

_SAFE_DIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_STORED_ARTIFACT = re.compile(r"^art_[A-Za-z0-9_-]{8,128}--(.+)$")


def phases_root(artifacts_root: Path, run_id: str) -> Path:
    """Return the phases directory for a run (created on demand)."""
    path = artifacts_root / run_id / "phases"
    path.mkdir(parents=True, exist_ok=True)
    return path


def phase_package_dir(
    artifacts_root: Path,
    *,
    run_id: str,
    index: int,
    name: str,
) -> Path:
    """Directory for one phase handoff package."""
    validate_phase_name(name)
    dirname = f"{index:02d}-{name}"
    path = phases_root(artifacts_root, run_id) / dirname
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_handoff(
    artifacts_root: Path,
    *,
    run_id: str,
    index: int,
    name: str,
    phase_id: str,
    status: str,
    artifact_paths: list[Path],
    summary: dict[str, Any] | None = None,
    require_artifacts: list[str] | None = None,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    """Copy phase artifacts into a handoff package and write ``manifest.json``."""
    package = phase_package_dir(artifacts_root, run_id=run_id, index=index, name=name)
    stored_names: list[str] = []
    for src in artifact_paths:
        if not src.is_file():
            msg = f"phase artifact missing: {src}"
            raise FileNotFoundError(msg)
        stored_match = _STORED_ARTIFACT.fullmatch(src.name)
        dest_name = stored_match.group(1) if stored_match else src.name
        if "/" in dest_name or "\\" in dest_name or dest_name in {".", ".."}:
            msg = f"invalid artifact name for handoff: {dest_name!r}"
            raise ValueError(msg)
        dest = package / dest_name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        stored_names.append(dest_name)

    manifest = HandoffManifest(
        phase=name,
        phase_id=phase_id,
        run_id=run_id,
        index=index,
        status=status,  # type: ignore[arg-type]
        artifacts=stored_names,
        summary=dict(summary or {}),
        require_artifacts=list(require_artifacts or []),
        source_run_id=source_run_id,
    )
    path = package / "manifest.json"
    atomic_write_json(path, manifest.model_dump(mode="json"), mode=0o600)
    relative = package.relative_to(artifacts_root / run_id).as_posix()
    return {
        "ok": True,
        "handoff_path": str(package.resolve()),
        "handoff_uri": f"run-handoff://{run_id}/{relative}",
        "manifest_path": str(path.resolve()),
        "manifest": manifest.model_dump(mode="json"),
    }


def read_handoff(path: str | Path) -> tuple[HandoffManifest, Path]:
    """Load a handoff package from a phase dir or its manifest.json path."""
    target = Path(path).expanduser().resolve()
    if target.is_file() and target.name == "manifest.json":
        package = target.parent
        manifest_path = target
    elif target.is_dir():
        package = target
        manifest_path = target / "manifest.json"
    else:
        msg = f"handoff path not found: {target}"
        raise FileNotFoundError(msg)
    if not manifest_path.is_file():
        msg = f"handoff manifest missing: {manifest_path}"
        raise FileNotFoundError(msg)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = HandoffManifest.model_validate(raw)
    return manifest, package


def list_handoff_files(package: Path, manifest: HandoffManifest) -> list[Path]:
    """Resolve artifact files listed in a handoff manifest."""
    files: list[Path] = []
    for name in manifest.artifacts:
        path = package / name
        if not path.is_file():
            msg = f"handoff artifact missing: {name} in {package}"
            raise FileNotFoundError(msg)
        files.append(path)
    return files


def discover_handoffs(path: str | Path) -> list[tuple[HandoffManifest, Path]]:
    """Load one handoff or all handoffs under a phases/ directory."""
    target = Path(path).expanduser().resolve()
    if target.is_file() and target.name == "manifest.json":
        return [read_handoff(target)]
    if not target.is_dir():
        msg = f"handoff path not found: {target}"
        raise FileNotFoundError(msg)
    if (target / "manifest.json").is_file():
        return [read_handoff(target)]
    # phases/ parent or run artifacts root
    phases_dir = target / "phases" if (target / "phases").is_dir() else target
    candidates = [
        child
        for child in sorted(phases_dir.iterdir())
        if child.is_dir() and (child / "manifest.json").is_file()
    ]
    if not candidates:
        msg = f"no handoff packages found under {target}"
        raise FileNotFoundError(msg)
    return [read_handoff(child) for child in candidates]


def copy_artifacts_to_run(
    artifacts_root: Path,
    *,
    run_id: str,
    files: list[Path],
    overwrite: bool = True,
) -> list[dict[str, Any]]:
    """Copy handoff files into the run artifact root (flat names)."""
    dest_dir = artifacts_root / run_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    for src in files:
        dest = dest_dir / src.name
        if dest.exists() and not overwrite:
            msg = f"artifact already exists: {dest}"
            raise FileExistsError(msg)
        shutil.copy2(src, dest)
        copied.append({"name": src.name, "path": str(dest.resolve()), "bytes": dest.stat().st_size})
    return copied


def slug_dir_name(name: str) -> str:
    """Filesystem-safe fragment for phase directories."""
    validate_phase_name(name)
    if not _SAFE_DIR.match(name):
        msg = f"unsafe phase directory name: {name!r}"
        raise ValueError(msg)
    return name
