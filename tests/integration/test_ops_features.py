"""Approvals, artifacts, and ActiveGraph-owned budgets."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from roi_h import RunSession
from roi_h.harness.custom import define_project_tool
from roi_h.harness.domain import BudgetSpec
from roi_h.harness.loader import default_skills_root
from roi_h.harness.workspace import Workspace, create_project


def _open_ws(tmp_path: Path, *, env: str = "dev") -> Workspace:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    return Workspace.open(home, project="demo", env=env)


def _roi_h(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def test_project_tool_requires_approval_in_dev(tmp_path: Path) -> None:
    skills = default_skills_root()
    ws = _open_ws(tmp_path)
    define_project_tool(
        skill="util",
        tool="echo",
        description="echo",
        project_root=ws.project_skills,
        source=(
            "from pydantic import BaseModel\n"
            "TOOL_ID='echo'\nDESCRIPTION='e'\n"
            "class Input(BaseModel):\n    text: str = ''\n"
            "class Output(BaseModel):\n    text: str = ''\n"
            "def run(args: Input) -> Output:\n    return Output(text=args.text)\n"
        ),
    )
    harness = RunSession.create(ws, run_id="appr1", skills_root=skills, auto_approve=False)
    harness.start_run("need approval")
    step = harness.invoke("util", "echo", {"text": "hi"})
    assert step.status == "pending_approval"
    assert step.approval_id
    granted = harness.approve(step.approval_id, approved_by="human")
    assert granted.status == "ok"
    assert granted.output["text"] == "hi"


def test_artifacts_and_budget(tmp_path: Path) -> None:
    skills = default_skills_root()
    ws = _open_ws(tmp_path)
    harness = RunSession.create(
        ws,
        run_id="ops1",
        skills_root=skills,
        budget=BudgetSpec(max_tool_calls=5),
        auto_approve=True,
    )
    harness.start_run("ops features")
    harness.invoke("browser", "navigate", {"url": "https://example.com/"})

    sample = tmp_path / "orders.csv"
    sample.write_text("a,b\n1,2\n", encoding="utf-8")
    meta = harness.put_artifact(sample, name="orders.csv")
    assert meta["name"] == "orders.csv"
    assert (ws.artifacts / "ops1" / "orders.csv").is_file()

    # budget enforcement
    tight = RunSession.create(
        ws,
        run_id="budget1",
        skills_root=skills,
        budget=BudgetSpec(max_tool_calls=1),
        auto_approve=True,
    )
    tight.start_run("budget")
    assert tight.invoke("browser", "navigate", {"url": "https://example.com/"}).status == "ok"
    try:
        tight.invoke("browser", "snapshot", {})
        raised = False
    except RuntimeError as exc:
        raised = "max_tool_calls" in str(exc)
    assert raised


def test_cli_env_and_start(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    skills = str(default_skills_root())
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    shown = _roi_h("rpa", "env", "set", "prod", "--home", home, cwd=tmp_path)
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["env"] == "prod"

    start = _roi_h(
        "rpa",
        "start",
        "--home",
        home,
        "--skills",
        skills,
        "--run-id",
        "trace-job",
        "--goal",
        "trace me",
        "--auto-approve",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr
    assert json.loads(start.stdout)["env"] == "prod"
