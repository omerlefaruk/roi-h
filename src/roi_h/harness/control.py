"""Cross-process run control signals."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.workspace import Workspace


def request_cancellation(
    workspace: Workspace,
    run_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """Atomically publish a cancellation signal visible to the active runner."""
    request = {
        "run_id": run_id,
        "reason": reason,
        "requested_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_json(cancellation_path(workspace, run_id), request)
    return request


def cancellation_request(workspace: Workspace, run_id: str) -> dict[str, Any] | None:
    """Read a cancellation signal, if one has been published."""
    path = cancellation_path(workspace, run_id)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def cancellation_path(workspace: Workspace, run_id: str) -> Path:
    """Return the control-file path for a run."""
    return workspace.runs / run_id / "runtime" / "cancel.json"
