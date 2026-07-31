"""Strict JSON CLI for external AI callers."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from roi_h.agent.cli import _emit
from roi_h.agent.contract import CommandContext, CommandRequest, CommandResult
from roi_h.agent.dispatcher import Dispatcher
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.workspace import Workspace, create_project


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
        "result": {
            "items": [],
            "count": 0,
            "next_cursor": None,
            "has_more": False,
            "snapshot": "projects:0",
        },
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
        if operation == "system.doctor":
            assert result["home_initialized"] is True
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


def test_agent_can_copy_a_source_run_file_into_the_current_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_project(home, "demo")
    workspace = Workspace.open(home, project="demo", env="dev")
    storage = RunStorage(workspace)
    source = storage.prepare("source-run").input / "source.log"
    source.write_text("diagnostic evidence", encoding="utf-8")
    target = storage.prepare("target-run").input / "diagnostic.log"

    copied = Dispatcher().execute(
        "run.input.add",
        CommandRequest(
            idempotency_key="copy-source-log",
            context=CommandContext(project="demo", environment="dev", run_id="target-run"),
            arguments={
                "home": str(home),
                "from_run": "source-run",
                "source_path": "run://input/source.log",
                "name": "diagnostic.log",
            },
        ),
    )

    assert copied.ok is True
    assert copied.result is not None
    assert copied.result["path"] == "run://input/diagnostic.log"
    assert copied.result["source_run_id"] == "source-run"
    assert copied.result["source_path"] == "run://input/source.log"
    assert target.read_text(encoding="utf-8") == "diagnostic evidence"

    production = Workspace.open(home, project="demo", env="prod")
    prod_log = RunStorage(production).prepare("prod-run").input / "secret.log"
    prod_log.write_text("production secret", encoding="utf-8")
    escaped = Dispatcher().execute(
        "run.input.add",
        CommandRequest(
            idempotency_key="reject-cross-environment-copy",
            context=CommandContext(project="demo", environment="dev", run_id="target-run"),
            arguments={
                "home": str(home),
                "from_run": "../../prod/runs/prod-run",
                "source_path": "run://input/secret.log",
                "name": "escaped.log",
            },
        ),
    )
    assert escaped.ok is False
    assert escaped.error is not None
    assert escaped.error.code == "path.invalid_logical_path"
    assert not target.with_name("escaped.log").exists()

    linked_root = storage.prepare("linked-root-run")
    (linked_root.output / "secret.log").write_text("wrong capability root", encoding="utf-8")
    linked_root.input.rmdir()
    try:
        linked_root.input.symlink_to(linked_root.output, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    wrong_root = Dispatcher().execute(
        "run.input.add",
        CommandRequest(
            idempotency_key="reject-cross-root-copy",
            context=CommandContext(project="demo", environment="dev", run_id="target-run"),
            arguments={
                "home": str(home),
                "from_run": "linked-root-run",
                "source_path": "run://input/secret.log",
                "name": "wrong-root.log",
            },
        ),
    )
    assert wrong_root.ok is False
    assert wrong_root.error is not None
    assert wrong_root.error.code == "path.escape_denied"

    linked_run = workspace.runs / "linked-prod-run"
    linked_run.symlink_to(prod_log.parents[2], target_is_directory=True)
    linked = Dispatcher().execute(
        "run.input.add",
        CommandRequest(
            idempotency_key="reject-symlinked-run-copy",
            context=CommandContext(project="demo", environment="dev", run_id="target-run"),
            arguments={
                "home": str(home),
                "from_run": "linked-prod-run",
                "source_path": "run://input/secret.log",
                "name": "linked.log",
            },
        ),
    )
    assert linked.ok is False
    assert linked.error is not None
    assert linked.error.code == "path.escape_denied"
    assert not target.with_name("linked.log").exists()

    shutil.rmtree(workspace.environment_root)
    workspace.environment_root.symlink_to(production.environment_root, target_is_directory=True)
    with pytest.raises(RuntimeError, match=r"path\.escape_denied"):
        Workspace.open(home, project="demo", env="dev")


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
