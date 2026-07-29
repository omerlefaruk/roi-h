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


def test_agent_can_find_and_explain_a_run_without_sql(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    create = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "roi_h",
            "rpa",
            "project",
            "create",
            "demo",
            "--home",
            home,
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert create.returncode == 0, create.stderr
    start = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "roi_h",
            "rpa",
            "start",
            "--home",
            home,
            "--run-id",
            "agent-read-run",
            "--goal",
            "Read this run",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert start.returncode == 0, start.stderr

    base_request = {
        "schema_version": "1.0",
        "context": {"project": "demo", "environment": "dev"},
        "arguments": {"home": home, "limit": 10},
    }
    listed = _agent(
        "call",
        "run.list",
        "--input",
        "-",
        cwd=tmp_path,
        stdin=json.dumps(base_request),
    )
    assert listed.returncode == 0, listed.stderr
    page = json.loads(listed.stdout)["result"]
    assert page["items"][0]["run_id"] == "agent-read-run"
    assert page["has_more"] is False
    assert page["snapshot"]

    for operation in ("run.show", "run.events", "run.trace"):
        request = dict(base_request)
        request["arguments"] = {"home": home, "run_id": "agent-read-run", "limit": 50}
        called = _agent(
            "call",
            operation,
            "--input",
            "-",
            cwd=tmp_path,
            stdin=json.dumps(request),
        )
        assert called.returncode == 0, called.stdout
        result = json.loads(called.stdout)["result"]
        assert result["run_id"] == "agent-read-run"
        assert str(tmp_path) not in called.stdout
