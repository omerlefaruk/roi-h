"""Plan-first project replacement through the installed-style agent CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4


def test_project_import_replacement_rejects_stale_plan_and_applies_fresh_plan(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    archive = tmp_path / "demo.roih"
    _call(
        tmp_path,
        "project.create",
        {
            "idempotency_key": "replace-create",
            "arguments": {"home": str(home), "name": "demo"},
        },
    )
    _call(
        tmp_path,
        "project.export",
        {
            "idempotency_key": "replace-export",
            "context": {"project": "demo", "environment": "dev"},
            "arguments": {
                "home": str(home),
                "project": "demo",
                "environment": "dev",
                "output": str(archive),
                "mode": "definition",
            },
        },
    )

    first_plan = _call(
        tmp_path,
        "project.import",
        {
            "idempotency_key": "replace-plan-one",
            "arguments": {
                "home": str(home),
                "source": str(archive),
                "name": "demo",
            },
        },
    )["result"]
    assert first_plan["apply_operation"] == "project.import"
    assert first_plan["effects"][0]["action"] == "replace_project"
    assert str(tmp_path) not in json.dumps(first_plan)

    project_manifest = home / "projects" / "demo" / "project.json"
    project_manifest.write_text(
        project_manifest.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    stale = _call(
        tmp_path,
        "project.import",
        {
            "idempotency_key": "replace-apply-stale",
            "arguments": {
                "home": str(home),
                "plan_id": first_plan["plan_id"],
            },
        },
        expected_exit=1,
    )
    assert stale["error"]["code"] == "plan.state_changed"
    assert stale["changed"] is False

    fresh_plan = _call(
        tmp_path,
        "project.import",
        {
            "idempotency_key": "replace-plan-two",
            "arguments": {
                "home": str(home),
                "source": str(archive),
                "name": "demo",
            },
        },
    )["result"]
    applied = _call(
        tmp_path,
        "project.import",
        {
            "idempotency_key": "replace-apply-fresh",
            "arguments": {
                "home": str(home),
                "plan_id": fresh_plan["plan_id"],
            },
        },
    )
    assert applied["result"]["project"] == "demo"
    assert applied["result"]["replaced"] is True
    assert applied["result"]["recoverable_previous_project"] is True


def _call(
    cwd: Path,
    operation: str,
    request: dict[str, Any],
    *,
    expected_exit: int = 0,
) -> dict[str, Any]:
    request_path = cwd / f"{operation.replace('.', '-')}-{uuid4().hex}.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "roi_h",
            "agent",
            "call",
            operation,
            "--input",
            str(request_path),
        ],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == expected_exit, completed.stdout + completed.stderr
    return json.loads(completed.stdout)
