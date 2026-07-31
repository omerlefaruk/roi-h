"""Durable task reconnect behavior."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _call(
    operation: str,
    arguments: dict[str, object],
    *,
    cwd: Path,
    key: str | None = None,
) -> subprocess.CompletedProcess[str]:
    request: dict[str, object] = {
        "schema_version": "1.0",
        "idempotency_key": key,
        "arguments": arguments,
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", "agent", "call", operation, "--input", "-"],
        cwd=cwd,
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
    )


def test_store_backup_task_can_be_resumed_by_event_id(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    create = _call(
        "project.create",
        {"home": home, "name": "demo"},
        cwd=tmp_path,
        key="task-project",
    )
    assert create.returncode == 0, create.stdout

    source = _call(
        "automation.source.put",
        {
            "home": home,
            "name": "task-store",
            "manifest": {
                "name": "task-store",
                "phases": [
                    {"id": "build", "module": "build"},
                    {
                        "id": "verify",
                        "module": "verify",
                        "role": "verify",
                        "needs": ["build"],
                    },
                ],
            },
            "files": {
                "build.py": "def run(context):\n    return {'summary': {'ok': True}}\n",
                "verify.py": "def run(context):\n    return {'summary': {'ok': True}}\n",
            },
        },
        cwd=tmp_path,
        key="task-source",
    )
    assert source.returncode == 0, source.stdout
    started = _call(
        "automation.dev.run",
        {"home": home, "name": "task-store", "run_id": "task-run"},
        cwd=tmp_path,
        key="task-run",
    )
    assert started.returncode == 0, started.stdout

    backup = _call(
        "store.backup",
        {"home": home, "output": str(tmp_path / "backup.roih")},
        cwd=tmp_path,
        key="backup-once",
    )
    assert backup.returncode == 0, backup.stdout
    task = json.loads(backup.stdout)["result"]["task"]
    assert task["state"] in {"queued", "working"}
    task_id = task["task_id"]

    waited = _call(
        "task.wait",
        {"home": home, "task_id": task_id, "timeout_seconds": 10},
        cwd=tmp_path,
    )
    assert json.loads(waited.stdout)["result"]["state"] == "succeeded"

    events = _call(
        "task.events",
        {"home": home, "task_id": task_id, "limit": 1},
        cwd=tmp_path,
    )
    assert events.returncode == 0, events.stdout
    first_page = json.loads(events.stdout)["result"]
    assert first_page["has_more"] is True
    after = first_page["items"][0]["event_id"]

    resumed = _call(
        "task.events",
        {"home": home, "task_id": task_id, "after": after, "limit": 10},
        cwd=tmp_path,
    )
    resumed_items = json.loads(resumed.stdout)["result"]["items"]
    assert resumed_items[-1]["type"] == "task.succeeded"

    waited = _call(
        "task.wait",
        {"home": home, "task_id": task_id, "timeout_seconds": 1},
        cwd=tmp_path,
    )
    assert json.loads(waited.stdout)["result"]["state"] == "succeeded"
