"""Recoverable process leases for mutating a run."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, TYPE_CHECKING, Self

if TYPE_CHECKING:
    from roi_h.harness.workspace import Workspace

if sys.platform == "win32":
    import msvcrt

    def _acquire_lock(stream: IO[str]) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

    def _release_lock(stream: IO[str]) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _acquire_lock(stream: IO[str]) -> None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release_lock(stream: IO[str]) -> None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass
class RunLease:
    """An OS-backed exclusive lease released automatically on process exit."""

    path: Path
    run_id: str
    timeout_seconds: float = 0.0
    _stream: IO[str] | None = None

    def __enter__(self) -> Self:
        """Acquire the lease and record its holder."""
        if self.path.parent.is_symlink() or self.path.is_symlink():
            msg = f"lease path must not be a symlink: {self.path}"
            raise RuntimeError(msg)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        stream = os.fdopen(descriptor, "r+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                _acquire_lock(stream)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    stream.seek(0)
                    try:
                        holder = stream.read().strip() or "unknown holder"
                    except OSError:
                        holder = "unknown holder"
                    stream.close()
                    msg = f"run lease is busy: {self.path} ({holder})"
                    raise RuntimeError(msg) from None
                time.sleep(0.05)
        stream.seek(0)
        stream.truncate()
        stream.write(
            json.dumps(
                {
                    "run_id": self.run_id,
                    "pid": os.getpid(),
                    "acquired_at": datetime.now(UTC).isoformat(),
                },
                sort_keys=True,
            )
        )
        stream.flush()
        os.fsync(stream.fileno())
        self._stream = stream
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the lease."""
        del exc_type, exc, traceback
        stream = self._stream
        if stream is None:
            return
        _release_lock(stream)
        stream.close()
        self._stream = None


def project_policy_lease(project_root: Path, *, timeout_seconds: float = 0.0) -> RunLease:
    """Create the project-wide lease used by policy writers and cleanup."""
    lock_root = project_root / ".locks"
    path = lock_root / "project-policy.lock"
    if lock_root.is_symlink() or path.is_symlink():
        msg = "project policy lock must not be a symlink"
        raise RuntimeError(msg)
    return RunLease(
        path=path,
        run_id="project-policy",
        timeout_seconds=timeout_seconds,
    )


def run_lease(workspace: Workspace, run_id: str, *, timeout_seconds: float = 0.0) -> RunLease:
    """Create an exclusive lease for one durable run id."""
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in run_id)
    path = workspace.runtime / "locks" / f"run-{safe}.lock"
    return RunLease(path=path, run_id=run_id, timeout_seconds=timeout_seconds)
