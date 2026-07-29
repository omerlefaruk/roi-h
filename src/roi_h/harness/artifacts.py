"""File artifacts attached to a durable run."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any


def artifacts_dir(root: Path, run_id: str) -> Path:
    """Return (and create) the artifact directory for a run."""
    path = root / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def put_artifact(
    root: Path,
    *,
    run_id: str,
    source: str | Path,
    name: str | None = None,
) -> dict[str, Any]:
    """Copy a file into the run artifact store and return metadata."""
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        msg = f"artifact source is not a file: {src}"
        raise FileNotFoundError(msg)
    dest_name = name or src.name
    if "/" in dest_name or "\\" in dest_name or dest_name in {".", ".."}:
        msg = f"invalid artifact name: {dest_name!r}"
        raise ValueError(msg)
    directory = artifacts_dir(root, run_id)
    dest = directory / dest_name
    shutil.copy2(src, dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    return {
        "ok": True,
        "run_id": run_id,
        "name": dest_name,
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "sha256": digest,
    }


def list_artifacts(root: Path, *, run_id: str) -> list[dict[str, Any]]:
    """List file metadata for artifacts stored under a run (top-level files only)."""
    directory = artifacts_dir(root, run_id)
    items: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return items


def get_artifact_path(root: Path, *, run_id: str, name: str) -> Path:
    """Resolve a single artifact path or raise if missing."""
    path = artifacts_dir(root, run_id) / name
    if not path.is_file():
        msg = f"artifact not found: {name!r} for run {run_id}"
        raise FileNotFoundError(msg)
    return path
