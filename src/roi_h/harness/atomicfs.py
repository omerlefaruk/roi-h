"""Atomic filesystem primitives for workspace metadata and immutable packages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Replace a text file atomically on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            tmp.chmod(mode)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path: Path, value: object, *, mode: int | None = None) -> None:
    """Serialize and atomically replace a JSON file."""
    atomic_write_text(
        path,
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        mode=mode,
    )


def hash_file(path: Path) -> tuple[str, int]:
    """Hash one file and return its digest and byte count."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return f"sha256:{digest.hexdigest()}", size


def package_digest(root: Path, manifest_without_digest: dict[str, Any]) -> str:
    """Hash package content and canonical manifest metadata."""
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            manifest_without_digest,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name in {"manifest.json", "push.json"}:
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def verify_package(root: Path, manifest: dict[str, Any]) -> str:
    """Verify the immutable package digest and return it."""
    expected = str(manifest.get("package_digest") or "")
    if not expected:
        msg = f"automation package has no package_digest: {root}"
        raise ValueError(msg)
    unsigned = dict(manifest)
    unsigned.pop("package_digest", None)
    actual = package_digest(root, unsigned)
    if actual != expected:
        msg = f"automation package digest mismatch: expected {expected}, observed {actual}"
        raise ValueError(msg)
    return actual
