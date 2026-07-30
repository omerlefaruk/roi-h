"""Safe mutation contract for external AI callers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from roi_h.harness.custom import define_project_tool


def _call(
    operation: str,
    request: dict[str, object],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "roi_h",
            "agent",
            "call",
            operation,
            "--input",
            "-",
        ],
        cwd=cwd,
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
    )


def test_project_write_replay_conflict_and_stale_plan(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    request: dict[str, object] = {
        "schema_version": "1.0",
        "request_id": "req_create_1",
        "idempotency_key": "create-demo",
        "arguments": {"home": home, "name": "demo", "use": True},
    }
    first = _call("project.create", request, cwd=tmp_path)
    assert first.returncode == 0, first.stdout
    first_payload = json.loads(first.stdout)
    assert first_payload["changed"] is True

    replay_request = {**request, "request_id": "req_create_2"}
    replay = _call("project.create", replay_request, cwd=tmp_path)
    assert replay.returncode == 0, replay.stdout
    replay_payload = json.loads(replay.stdout)
    assert replay_payload["result"] == first_payload["result"]
    assert replay_payload["warnings"] == ["Returned the first result for this idempotency key."]

    conflict_request = {
        **request,
        "request_id": "req_create_3",
        "arguments": {"home": home, "name": "other", "use": True},
    }
    conflict = _call("project.create", conflict_request, cwd=tmp_path)
    assert conflict.returncode == 1
    conflict_payload = json.loads(conflict.stdout)
    assert conflict_payload["error"]["code"] == "request.idempotency_conflict"
    assert not (Path(home) / "projects" / "other").exists()

    plan = _call(
        "project.delete.plan",
        {
            "schema_version": "1.0",
            "arguments": {"home": home, "name": "demo"},
        },
        cwd=tmp_path,
    )
    assert plan.returncode == 0, plan.stdout
    plan_id = json.loads(plan.stdout)["result"]["plan_id"]
    (Path(home) / "projects" / "demo" / "reference" / "changed.txt").write_text(
        "changed",
        encoding="utf-8",
    )

    apply = _call(
        "project.delete.apply",
        {
            "schema_version": "1.0",
            "idempotency_key": "delete-demo",
            "arguments": {"home": home, "plan_id": plan_id},
        },
        cwd=tmp_path,
    )
    assert apply.returncode == 1
    apply_payload = json.loads(apply.stdout)
    assert apply_payload["error"]["code"] == "plan.state_changed"
    assert (Path(home) / "projects" / "demo").is_dir()


def test_agent_run_tool_and_approval_rejection_journey(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    created = _call(
        "project.create",
        {
            "schema_version": "1.0",
            "idempotency_key": "approval-project",
            "arguments": {"home": home, "name": "demo"},
        },
        cwd=tmp_path,
    )
    assert created.returncode == 0, created.stdout

    define_project_tool(
        skill="approval",
        tool="request",
        description="Request approval without an external effect.",
        project_root=Path(home) / "projects" / "demo" / "skills",
    )

    started = _call(
        "run.start",
        {
            "schema_version": "1.0",
            "idempotency_key": "approval-run",
            "context": {"project": "demo", "environment": "dev"},
            "arguments": {
                "home": home,
                "run_id": "agent-approval-run",
                "goal": "Request and reject approval",
            },
        },
        cwd=tmp_path,
    )
    assert started.returncode == 0, started.stdout

    invoked = _call(
        "tool.invoke",
        {
            "schema_version": "1.0",
            "idempotency_key": "request-not-approved",
            "context": {
                "project": "demo",
                "environment": "dev",
                "run_id": "agent-approval-run",
            },
            "arguments": {
                "home": home,
                "name": "approval.request",
                "arguments": {"value": "Do not run."},
            },
        },
        cwd=tmp_path,
    )
    assert invoked.returncode == 0, invoked.stdout
    step = json.loads(invoked.stdout)["result"]
    assert step["status"] == "pending_approval"
    approval_id = step["approval_id"]

    rejected = _call(
        "approval.reject",
        {
            "schema_version": "1.0",
            "idempotency_key": "reject-request",
            "context": {
                "project": "demo",
                "environment": "dev",
                "run_id": "agent-approval-run",
            },
            "arguments": {
                "home": home,
                "approval_id": approval_id,
                "by": "operator",
                "reason": "Not approved.",
            },
        },
        cwd=tmp_path,
    )
    assert rejected.returncode == 0, rejected.stdout
    assert json.loads(rejected.stdout)["result"]["status"] == "denied"

    events = _call(
        "run.events",
        {
            "schema_version": "1.0",
            "context": {
                "project": "demo",
                "environment": "dev",
                "run_id": "agent-approval-run",
            },
            "arguments": {"home": home, "limit": 200},
        },
        cwd=tmp_path,
    )
    event_types = {item["type"] for item in json.loads(events.stdout)["result"]["items"]}
    assert "approval.rejected" in event_types
    assert "tool.requested" not in event_types
