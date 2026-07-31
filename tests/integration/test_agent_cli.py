"""Strict JSON CLI for the guidance-skill modular automation interface."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _agent(*args: str, cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", "agent", *args],
        cwd=cwd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def test_agent_describes_new_operations_and_omits_removed_actions(tmp_path: Path) -> None:
    described = _agent("describe", cwd=tmp_path)

    assert described.returncode == 0
    operation_ids = {
        item["operation_id"] for item in json.loads(described.stdout)["result"]["operations"]
    }
    assert {
        "skill.show",
        "automation.source.put",
        "automation.dev.run",
        "automation.ship",
        "automation.run",
    }.issubset(operation_ids)
    assert not operation_ids.intersection(
        {"tool.invoke", "skill.define", "phase.begin", "run.start"}
    )


def test_agent_call_uses_one_strict_json_contract(tmp_path: Path) -> None:
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
    assert payload["operation"] == "project.list"
    assert payload["request_id"] == "req_cli"
    assert payload["result"]["items"] == []
    assert called.stderr == ""


def test_agent_invalid_input_is_structured(tmp_path: Path) -> None:
    called = _agent(
        "call",
        "automation.source.put",
        "--input",
        "-",
        cwd=tmp_path,
        stdin=json.dumps({"idempotency_key": "invalid", "arguments": {}}),
    )

    assert called.returncode == 2
    payload = json.loads(called.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "request.invalid"
