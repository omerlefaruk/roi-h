"""Runnable proof for the throwaway Python automation contract prototype."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from roi_h.harness.application import RunSession
from roi_h.harness.atomicfs import atomic_write_json, package_digest
from roi_h.harness.loader import default_skills_root
from roi_h.harness.python_automation_prototype import (
    ApprovalRequired,
    PrototypeCrash,
    load_python_automation,
    operation_manifest_entry,
    runtime_manifest,
    verify_python_package,
)
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.workspace import Workspace, create_project

_AUTOMATION = """\
from pydantic import BaseModel, Field
from roi_h.harness.python_automation_prototype import (
    ARTIFACT_PUT,
    ArtifactPutInput,
    AutomationContext,
    Operation,
)

class IncrementInput(BaseModel):
    path: str

class IncrementOutput(BaseModel):
    ok: bool = True
    path: str
    count: int

class ReadInput(BaseModel):
    path: str
    max_chars: int = Field(default=200_000, ge=1)

class ReadOutput(BaseModel):
    ok: bool = True
    path: str
    text: str
    bytes: int = 0

class HashInput(BaseModel):
    path: str

class HashOutput(BaseModel):
    ok: bool = True
    path: str
    sha256: str
    bytes: int

INCREMENT = Operation("counter.increment", IncrementInput, IncrementOutput)
READ = Operation("files.read", ReadInput, ReadOutput)
UNDECLARED = Operation("files.hash", HashInput, HashOutput)


def run(ctx: AutomationContext) -> None:
    def write():
        result = ctx.call(
            INCREMENT,
            IncrementInput(path="run://output/count.txt"),
            call_id="increment-count",
        )
        artifact = ctx.call(
            ARTIFACT_PUT,
            ArtifactPutInput(source=result.path, name="count.txt"),
            call_id="attach-count",
        )
        return {"artifact": artifact.uri, "count": result.count}

    def verify():
        result = ctx.call(
            READ,
            ReadInput(path="run://output/count.txt"),
            call_id="read-count",
        )
        if result.text != "1":
            raise ValueError(f"write replayed: {result.text}")
        return {"count": int(result.text)}

    def deny():
        try:
            ctx.call(
                ARTIFACT_PUT,
                ArtifactPutInput(
                    source="project://reference/private.txt",
                    name="leak.txt",
                ),
                call_id="denied-artifact",
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("artifact source escaped run output")
        try:
            ctx.call(
                UNDECLARED,
                HashInput(path="run://output/count.txt"),
                call_id="undeclared-hash",
            )
        except PermissionError:
            return {"denied": True}
        raise AssertionError("undeclared operation was allowed")

    ctx.phase("write", write)
    ctx.phase("verify", verify)
    ctx.phase("deny", deny)
"""

_COUNTER = """\
from pathlib import Path
from pydantic import BaseModel
TOOL_ID = "increment"
DESCRIPTION = "Increment a durable counter."
TOOL_EFFECT = "write"
IDEMPOTENCY = "reconcile"
ALLOW_IN_PROD = True
REQUIRES_APPROVAL = False
FILESYSTEM_ROOTS = ("run:output:read-write",)

class Input(BaseModel):
    path: str

class Output(BaseModel):
    ok: bool = True
    path: str
    count: int


def run(args: Input) -> Output:
    path = Path(args.path)
    count = int(path.read_text(encoding="utf-8")) + 1 if path.exists() else 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(count), encoding="utf-8")
    return Output(path=str(path.resolve()), count=count)
"""


def test_verified_python_entrypoint_restarts_without_replaying_write(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    package = tmp_path / "counter-automation" / "1.0.0"
    skill = package / "skills" / "counter"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# counter\n", encoding="utf-8")
    (skill / "scripts" / "increment.py").write_text(_COUNTER, encoding="utf-8")
    (package / "automation.py").write_text(_AUTOMATION, encoding="utf-8")

    dev = Workspace.open(home, project="demo", env="dev")
    publisher = RunSession.create(
        dev,
        run_id="describe-package",
        skills_root=default_skills_root(),
        project_skills=package / "skills",
        auto_approve=False,
    )
    manifest = {
        "schema_version": 2,
        "name": "counter-automation",
        "version": "1.0.0",
        "runtime_api": "roi-h.python-automation/1",
        "runtime": runtime_manifest(),
        "entrypoint": "automation.py:run",
        "phases": [
            {"name": "write", "require_artifacts": ["count.txt"]},
            {"name": "verify"},
            {"name": "deny"},
        ],
        "operations": [
            operation_manifest_entry(publisher, "counter.increment"),
            operation_manifest_entry(publisher, "files.read"),
            operation_manifest_entry(publisher, "artifact.put"),
        ],
    }
    manifest["package_digest"] = package_digest(package, manifest)
    atomic_write_json(package / "manifest.json", manifest)
    expected_digest = str(manifest["package_digest"])

    verify_python_package(package, expected_digest)
    prod = Workspace.open(home, project="demo", env="prod")
    first = RunSession.create(
        prod,
        run_id="production-run",
        skills_root=default_skills_root(),
        project_skills=package / "skills",
        auto_approve=False,
    )
    first.start_run(
        "increment once",
        phase_plan=manifest["phases"],
        automation_name="counter-automation",
        automation_version="1.0.0",
        package_digest=expected_digest,
    )
    automation = load_python_automation(package, expected_digest, first)

    with pytest.raises(ApprovalRequired) as pending:
        automation.run(first)
    with pytest.raises(ApprovalRequired) as same_pending:
        automation.run(first)
    assert same_pending.value.approval_id == pending.value.approval_id
    assert len(first.runtime.pending_approvals()) == 1
    approved = first.approve(pending.value.approval_id)
    assert approved.status == "ok"

    with pytest.raises(PrototypeCrash):
        automation.run(first, crash_after="increment-count")
    assert first.status()["current_phase"] == "write"

    reopened = RunSession.reopen(
        prod,
        run_id="production-run",
        skills_root=default_skills_root(),
        project_skills=package / "skills",
        auto_approve=False,
    )
    resumed = load_python_automation(package, expected_digest, reopened)
    with pytest.raises(PrototypeCrash):
        resumed.run(reopened, crash_after="attach-count")
    approved_steps = []
    while True:
        try:
            result = resumed.run(reopened)
            break
        except ApprovalRequired as pending_read:
            approved_steps.append(reopened.approve(pending_read.approval_id))

    assert approved_steps and all(step.status == "ok" for step in approved_steps)
    assert result["ok"] is True
    assert reopened.status()["run_status"] == "completed"
    counter = RunStorage(prod).paths("production-run").output / "count.txt"
    assert counter.read_text(encoding="utf-8") == "1"
    invocations = list(reopened.runtime.graph.objects(type="rpa.invocation"))
    assert [item.data["name"] for item in invocations].count("counter.increment") == 1
    assert all(item.data["name"] != "files.hash" for item in invocations)
    assert {item.data["status"] for item in reopened.runtime.graph.objects(type="rpa.phase")} == {
        "done"
    }
    assert len(list(reopened.runtime.graph.objects(type="rpa.artifact"))) == 1
    event_types = {event.type for event in reopened.runtime.graph.events}
    assert {"authority.decision", "tool.requested", "tool.responded"} <= event_types

    other = tmp_path / "counter-automation" / "1.0.1"
    shutil.copytree(package, other)
    (other / "automation.py").write_text(
        _AUTOMATION + "\n# valid other package\n", encoding="utf-8"
    )
    other_manifest = {key: value for key, value in manifest.items() if key != "package_digest"}
    other_manifest["version"] = "1.0.1"
    other_manifest["package_digest"] = package_digest(other, other_manifest)
    atomic_write_json(other / "manifest.json", other_manifest)
    with pytest.raises(ValueError, match="run package identity mismatch"):
        load_python_automation(other, str(other_manifest["package_digest"]), reopened)

    (package / "automation.py").write_text(_AUTOMATION + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_python_automation(package, expected_digest, reopened)
