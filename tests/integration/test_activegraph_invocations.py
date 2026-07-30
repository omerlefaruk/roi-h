"""ActiveGraph is the authority for harness tool invocation lifecycles."""

from __future__ import annotations

from pathlib import Path

from roi_h import RunSession
from roi_h.harness.custom import define_project_tool
from roi_h.harness.loader import default_skills_root
from roi_h.harness.workspace import Workspace, create_project


def _workspace(tmp_path: Path, *, env: str = "dev") -> Workspace:
    home = tmp_path / ".roi-h"
    create_project(home, "runtime", set_active=True)
    return Workspace.open(home, project="runtime", env=env)


def test_invocation_is_scheduled_executed_and_materialized_by_activegraph(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    harness = RunSession.create(
        workspace,
        run_id="activegraph-default",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("record the complete tool lifecycle")

    result = harness.invoke("browser", "navigate", {"url": "https://example.com"})

    assert result.status == "ok"
    event_types = [event.type for event in harness.runtime.graph.events]
    assert "authority.decision" in event_types
    assert "tool.requested" in event_types
    assert "tool.responded" in event_types
    assert "behavior.completed" in event_types
    invocation = harness.runtime.graph.objects(type="rpa.invocation")[0]
    assert invocation.data["status"] == "succeeded"
    assert invocation.data["step_id"] == result.step_id
    assert invocation.data["duration_seconds"] >= 0


def test_write_tools_cannot_be_reinvoked_as_deterministic_replay(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    define_project_tool(
        skill="crm",
        tool="create_contact",
        description="write probe",
        project_root=workspace.project_skills,
        source=(
            "from pydantic import BaseModel\n"
            "TOOL_ID='create_contact'\n"
            "DESCRIPTION='write probe'\n"
            "TOOL_EFFECT='write'\n"
            "DETERMINISTIC=True\n"
            "class Input(BaseModel):\n    name: str\n"
            "class Output(BaseModel):\n    name: str\n"
            "def run(args: Input) -> Output:\n    return Output(name=args.name)\n"
        ),
    )
    harness = RunSession.create(
        workspace,
        run_id="write-policy",
        skills_root=default_skills_root(),
        auto_approve=True,
    )

    tool = harness.catalog.resolve("crm", "create_contact")

    assert tool.effect == "write"
    assert tool.deterministic is False
    assert tool.idempotency == "reconcile"
    assert harness.runtime.get_tool("crm.create_contact").deterministic is False


def test_skill_mutation_after_inspection_fails_before_import(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    define_project_tool(
        skill="safe",
        tool="echo",
        description="digest probe",
        project_root=workspace.project_skills,
        source=(
            "from pydantic import BaseModel\n"
            "TOOL_ID='echo'\nTOOL_EFFECT='read'\nIDEMPOTENCY='none'\n"
            "REQUIRES_APPROVAL=False\nALLOW_IN_PROD=False\n"
            "TIMEOUT_SECONDS=120.0\nSECRET_NAMES=()\nNETWORK_HOSTS=()\n"
            "FILESYSTEM_ROOTS=()\n"
            "class Input(BaseModel):\n    value: str\n"
            "class Output(BaseModel):\n    value: str\n"
            "def run(args: Input) -> Output:\n    return Output(value=args.value)\n"
        ),
    )
    harness = RunSession.create(
        workspace,
        run_id="skill-mutation",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("reject changed skill code")
    tool = harness.catalog.resolve("safe", "echo")
    marker = tmp_path / "mutated-import"
    tool.script_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('unsafe')\n"
        + tool.script_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = harness.invoke("safe", "echo", {"value": "test"})

    assert result.status == "error"
    assert result.failure is not None
    assert result.failure.details["stage"] == "integrity"
    assert not marker.exists()


def test_force_does_not_bypass_production_execution_policy(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, env="prod")
    harness = RunSession.create(
        workspace,
        run_id="prod-policy",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("do not run arbitrary shell")

    result = harness.invoke("shell", "run", {"command": "echo unsafe"}, force=True)

    assert result.status == "error"
    assert result.failure is not None
    assert "production policy denies shell.run" in result.failure.message
    assert harness.runtime.graph.objects(type="rpa.invocation") == []


def test_reopen_marks_interrupted_write_outcome_unknown(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    harness = RunSession.create(
        workspace,
        run_id="interrupted-write",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("recover without replay")
    harness.runtime.graph.add_object(
        "rpa.invocation",
        {
            "run_id": harness.runtime.run_id,
            "invocation_id": "inv_interrupted",
            "idempotency_key": "interrupted:1",
            "attempt": 1,
            "skill": "crm",
            "tool": "create_contact",
            "name": "crm.create_contact",
            "args": {"name": "Ada"},
            "status": "running",
            "effect": "write",
        },
    )

    recovered = RunSession.reopen(
        workspace,
        run_id="interrupted-write",
        skills_root=default_skills_root(),
        auto_approve=True,
    )

    status = recovered.status()
    assert status["outcome_unknown_invocations"] == 1
    invocation = status["invocations"][0]["data"]
    assert invocation["status"] == "outcome_unknown"
    assert "reconcile before retry" in invocation["error"]
    assert status["step_count"] == 0
