"""Skills-based harness vertical slice."""

from pathlib import Path
from uuid import uuid4

from roi_h import RunSession
from roi_h.harness.custom import define_project_tool
from roi_h.harness.diagnostics import DiagnosticSink
from roi_h.harness.loader import default_skills_root
from roi_h.harness.secrets import set_secret
from roi_h.harness.workspace import Workspace, create_project


def _open_ws(tmp_path: Path, *, env: str = "dev") -> Workspace:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    return Workspace.open(home, project="demo", env=env)


def test_start_run_invoke_browser_stub_and_reopen(tmp_path: Path) -> None:
    ws = _open_ws(tmp_path)
    harness = RunSession.create(
        ws,
        run_id="rpa_smoke",
        skills_root=default_skills_root(),
        auto_approve=True,
    )

    tools = {item.name: item for item in harness.list_tools()}
    assert "browser.navigate" in tools

    run = harness.start_run("Open example and snapshot")
    assert run.data["status"] == "open"
    assert run.data["env"] == "dev"

    step = harness.invoke(
        "browser",
        "navigate",
        {"url": "https://example.com/"},
    )
    assert step.status == "ok"
    assert step.output["ok"] is True
    assert "example.com" in step.output["url"]

    snap = harness.invoke("browser", "snapshot", {"mode": "a11y"})
    assert snap.status == "ok"
    assert "e1" in snap.output["refs"]
    assert snap.invocation_id != step.invocation_id
    assert snap.idempotency_key != step.idempotency_key

    click = harness.invoke("browser", "click", {"ref": "e1"})
    assert click.status == "ok"

    reopened = RunSession.reopen(
        ws,
        run_id="rpa_smoke",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    runs = list(reopened.runtime.graph.objects(type="rpa.run"))
    steps = list(reopened.runtime.graph.objects(type="rpa.step"))
    assert len(runs) == 1
    assert runs[0].data["goal"] == "Open example and snapshot"
    assert len(steps) == 3

    continued = reopened.invoke(
        "browser",
        "navigate",
        {"url": "https://example.org/"},
    )
    assert continued.status == "ok"
    assert "example.org" in continued.output["url"]
    assert len(reopened.runtime.graph.objects(type="rpa.step")) == 4


def test_invoke_invalid_args_records_error_step(tmp_path: Path) -> None:
    ws = _open_ws(tmp_path)
    harness = RunSession.create(
        ws,
        run_id="rpa_err",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("bad navigate")
    step = harness.invoke("browser", "navigate", {"url": "not-a-url"})
    assert step.status == "error"
    assert step.error is not None
    assert step.failure is not None
    assert step.failure.kind == "validation"
    assert step.failure.retryable is False
    assert step.invocation_id.startswith("inv_")
    assert step.idempotency_key.endswith(step.invocation_id)


def test_project_secrets_are_resolved_for_tools_but_redacted_from_records(
    tmp_path: Path,
) -> None:
    ws = _open_ws(tmp_path)
    secret = uuid4().hex
    set_secret(ws, "TOKEN", secret)
    define_project_tool(
        skill="secret_probe",
        tool="echo",
        description="Return the supplied token",
        project_root=ws.project_skills,
        source=(
            "from pydantic import BaseModel\n"
            "TOOL_ID = 'echo'\n"
            "DESCRIPTION = 'echo secret probe'\n"
            "REQUIRES_APPROVAL = False\n"
            "class Input(BaseModel):\n"
            "    token: str\n"
            "class Output(BaseModel):\n"
            "    token: str\n"
            "def run(args: Input) -> Output:\n"
            "    return Output(token=args.token)\n"
        ),
    )
    harness = RunSession.create(
        ws,
        run_id="rpa_secret_redaction",
        skills_root=default_skills_root(),
        auto_approve=False,
    )
    harness.start_run("redact secrets")

    pending = harness.invoke("secret_probe", "echo", {"token": "{{secret.TOKEN}}"})
    assert pending.status == "pending_approval"
    assert pending.args == {"token": "{{secret.TOKEN}}"}
    approval = harness.list_approvals()[0]
    assert approval["data"]["args"] == {"token": "{{secret.TOKEN}}"}

    completed = harness.approve(pending.approval_id or "")
    assert completed.status == "ok"
    assert completed.args == {"token": "{{secret.TOKEN}}"}
    assert completed.output == {"token": "{{secret.TOKEN}}"}
    assert secret not in str(completed.model_dump(mode="json"))
    assert secret not in str([obj.data for obj in harness.runtime.graph.objects(type="rpa.step")])
    stored_step = harness.runtime.graph.get_object(completed.step_id)
    assert stored_step is not None
    assert stored_step.data["approval_id"] == completed.approval_id


def test_isolated_worker_receives_only_declared_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ws = _open_ws(tmp_path)
    set_secret(ws, "ALLOWED", "allowed-value")
    set_secret(ws, "HIDDEN", "hidden-value")
    monkeypatch.setenv("ROI_H_SECRET_HOST_ONLY", "must-not-leak")
    define_project_tool(
        skill="secret_probe",
        tool="inspect_env",
        description="Inspect scoped worker environment",
        project_root=ws.project_skills,
        source=(
            "import os\n"
            "from pydantic import BaseModel\n"
            "TOOL_ID = 'inspect_env'\n"
            "DESCRIPTION = 'inspect scoped secrets'\n"
            "REQUIRES_APPROVAL = False\n"
            "SECRET_NAMES = ('ALLOWED',)\n"
            "class Input(BaseModel):\n"
            "    pass\n"
            "class Output(BaseModel):\n"
            "    allowed: str | None\n"
            "    hidden: str | None\n"
            "    host_only: str | None\n"
            "def run(args: Input) -> Output:\n"
            "    return Output(\n"
            "        allowed=os.getenv('ROI_H_SECRET_ALLOWED'),\n"
            "        hidden=os.getenv('ROI_H_SECRET_HIDDEN'),\n"
            "        host_only=os.getenv('ROI_H_SECRET_HOST_ONLY'),\n"
            "    )\n"
        ),
    )
    harness = RunSession.create(
        ws,
        run_id="scoped-secrets",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("scope worker secrets")

    result = harness.invoke("secret_probe", "inspect_env", {})

    assert result.status == "ok"
    assert result.output == {
        "allowed": "{{secret.ALLOWED}}",
        "hidden": None,
        "host_only": None,
    }


def test_isolated_runtime_failure_creates_actionable_diagnostic(tmp_path: Path) -> None:
    ws = _open_ws(tmp_path)
    define_project_tool(
        skill="runtime_probe",
        tool="fail",
        description="Raise one isolated runtime fault",
        project_root=ws.project_skills,
        source=(
            "from pydantic import BaseModel\n"
            "TOOL_ID = 'fail'\n"
            "DESCRIPTION = 'raise a runtime fault'\n"
            "REQUIRES_APPROVAL = False\n"
            "class Input(BaseModel):\n"
            "    pass\n"
            "class Output(BaseModel):\n"
            "    ok: bool\n"
            "def run(args: Input) -> Output:\n"
            "    raise OSError('socket bootstrap failed')\n"
        ),
    )
    harness = RunSession.create(
        ws,
        run_id="runtime-diagnostic",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("record runtime failure")

    result = harness.invoke("runtime_probe", "fail", {})

    assert result.status == "error"
    assert result.failure is not None
    diagnostic_id = result.failure.details["diagnostic_id"]
    diagnostics = DiagnosticSink(ws.root).read()
    assert diagnostics[-1]["diagnostic_id"] == diagnostic_id
    assert diagnostics[-1]["code"] == "tool.runtime_failure"
    assert diagnostics[-1]["details"]["tool"] == "runtime_probe.fail"
