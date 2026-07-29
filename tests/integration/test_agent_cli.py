"""Strict JSON CLI for external AI callers."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from roi_h.agent.cli import _emit
from roi_h.agent.contract import CommandResult


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


def test_agent_machine_output_is_utf8(monkeypatch) -> None:
    class Console:
        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def write(self, _value: str) -> None:
            message = "the text fallback must not be used"
            raise AssertionError(message)

    console = Console()
    monkeypatch.setattr(sys, "stdout", console)
    result = CommandResult(
        operation="test.utf8",
        request_id="req_utf8",
        ok=True,
        changed=False,
        result={"text": "\ufffd"},
    )

    _emit(result)

    payload = json.loads(console.buffer.getvalue().decode("utf-8"))
    assert payload["result"]["text"] == "\ufffd"


def test_agent_doctors_report_isolated_socket_and_tls_health(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    uninitialized_request = json.dumps(
        {
            "schema_version": "1.0",
            "arguments": {"home": home},
        }
    )
    uninitialized = _agent(
        "call",
        "system.doctor",
        "--input",
        "-",
        cwd=tmp_path,
        stdin=uninitialized_request,
    )
    assert uninitialized.returncode == 0, uninitialized.stdout
    uninitialized_result = json.loads(uninitialized.stdout)["result"]
    assert uninitialized_result["runtime"]["healthy"] is True
    assert uninitialized_result["checks"]["runtime_socket_bootstrap"] is True
    assert uninitialized_result["checks"]["runtime_tls_bootstrap"] is True

    create_request = json.dumps(
        {
            "schema_version": "1.0",
            "request_id": "req_doctor_project",
            "idempotency_key": "doctor-project-create",
            "arguments": {"home": home, "name": "demo"},
        }
    )
    created = _agent(
        "call",
        "project.create",
        "--input",
        "-",
        cwd=tmp_path,
        stdin=create_request,
    )
    assert created.returncode == 0, created.stdout

    request = json.dumps(
        {
            "schema_version": "1.0",
            "context": {"project": "demo", "environment": "dev"},
            "arguments": {"home": home},
        }
    )
    for operation in ("system.doctor", "environment.doctor", "project.doctor"):
        called = _agent(
            "call",
            operation,
            "--input",
            "-",
            cwd=tmp_path,
            stdin=request,
        )
        assert called.returncode == 0, called.stdout
        result = json.loads(called.stdout)["result"]
        assert result["runtime"]["healthy"] is True
        assert result["checks"]["runtime_socket_bootstrap"] is True
        assert result["checks"]["runtime_tls_bootstrap"] is True


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


def test_agent_secret_set_uses_a_separate_standard_input_channel(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    create_request = tmp_path / "create.json"
    create_request.write_text(
        json.dumps(
            {
                "idempotency_key": "create-secret-project",
                "arguments": {"home": home, "name": "demo"},
            }
        ),
        encoding="utf-8",
    )
    created = _agent(
        "call",
        "project.create",
        "--input",
        str(create_request),
        cwd=tmp_path,
    )
    assert created.returncode == 0, created.stdout

    request_path = tmp_path / "secret-request.json"
    request_path.write_text(
        json.dumps(
            {
                "idempotency_key": "set-token",
                "context": {"project": "demo", "environment": "dev"},
                "arguments": {"home": home, "name": "TOKEN"},
            }
        ),
        encoding="utf-8",
    )
    secret = f"value-{uuid4().hex}"
    called = _agent(
        "call",
        "secret.set",
        "--input",
        str(request_path),
        "--secret-stdin",
        cwd=tmp_path,
        stdin=secret,
    )
    assert called.returncode == 0, called.stdout
    assert secret not in called.stdout
    assert secret not in called.stderr
    assert json.loads(called.stdout)["result"]["name"] == "TOKEN"
