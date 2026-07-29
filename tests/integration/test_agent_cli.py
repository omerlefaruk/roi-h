"""Strict JSON CLI for external AI callers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _agent(
    *args: str,
    cwd: Path,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", "agent", *args],
        cwd=cwd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def test_agent_describe_and_call_use_one_json_contract(tmp_path: Path) -> None:
    describe = _agent("describe", "project.list", cwd=tmp_path)
    assert describe.returncode == 0
    described = json.loads(describe.stdout)
    assert described["ok"] is True
    assert described["operation"] == "system.describe"
    assert described["result"]["operations"][0]["operation_id"] == "project.list"
    assert describe.stderr == ""

    request = json.dumps(
        {
            "schema_version": "1.0",
            "request_id": "req_cli",
            "arguments": {"home": str(tmp_path / "home")},
        }
    )
    called = _agent("call", "project.list", "--input", "-", cwd=tmp_path, stdin=request)
    assert called.returncode == 0
    payload = json.loads(called.stdout)
    assert payload == {
        "schema_version": "1.0",
        "operation": "project.list",
        "request_id": "req_cli",
        "ok": True,
        "changed": False,
        "context": {
            "project": None,
            "environment": None,
            "run_id": None,
        },
        "result": {"items": [], "count": 0},
        "warnings": [],
        "next_actions": [],
        "error": None,
    }
    assert called.stderr == ""


def test_agent_invalid_input_is_structured_and_exits_two(tmp_path: Path) -> None:
    called = _agent(
        "call",
        "project.list",
        "--input",
        "-",
        cwd=tmp_path,
        stdin="{not-json",
    )
    assert called.returncode == 2
    payload = json.loads(called.stdout)
    assert payload["ok"] is False
    assert payload["changed"] is False
    assert payload["error"]["code"] == "request.invalid"
    assert payload["error"]["retryable"] is False
