"""Recoverable process leases for mutating a run."""

from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Self

from roi_h.harness.workspace import Workspace


@dataclass
class RunLease:
    """An OS-backed exclusive lease released automatically on process exit."""

    path: Path
    run_id: str
    timeout_seconds: float = 0.0
    _stream: IO[str] | None = None

    def __enter__(self) -> Self:
        """Acquire the lease and record its holder."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    stream.seek(0)
                    holder = stream.read().strip() or "unknown holder"
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
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()
        self._stream = None


def run_lease(workspace: Workspace, run_id: str, *, timeout_seconds: float = 0.0) -> RunLease:
    """Create an exclusive lease for one durable run id."""
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in run_id)
    path = workspace.runtime / "locks" / f"run-{safe}.lock"
    return RunLease(path=path, run_id=run_id, timeout_seconds=timeout_seconds)
