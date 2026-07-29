"""Checkpointed phases, handoff packages, and seed-from-handoff."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from roi_h import RunSession
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


def test_phase_handoff_and_seed_api(tmp_path: Path) -> None:
    skills = default_skills_root()
    ws = _open_ws(tmp_path)
    harness = RunSession.create(
        ws,
        run_id="phase1",
        skills_root=skills,
        auto_approve=True,
        budget=BudgetSpec(max_tool_calls=20),
    )
    harness.start_run(
        "download then normalize",
        phase_plan=["browse_download", "normalize:parse csv"],
    )

    begun = harness.begin_phase(
        "browse_download",
        require_artifacts=["orders.csv"],
    )
    assert begun["status"] == "open"
    assert begun["index"] == 1

    step = harness.invoke("browser", "navigate", {"url": "https://example.com/"})
    assert step.status == "ok"
    assert step.phase == "browse_download"
    assert step.phase_id == begun["phase_id"]

    sample = tmp_path / "orders.csv"
    sample.write_text("a,b\n1,2\n", encoding="utf-8")
    art = harness.put_artifact(sample, name="orders.csv")
    assert art["phase"] == "browse_download"

    ended = harness.end_phase(summary={"portal": "example"})
    assert ended["status"] == "done"
    assert ended["handoff_path"]
    handoff_dir = Path(ended["handoff_path"])
    assert (handoff_dir / "manifest.json").is_file()
    assert (handoff_dir / "orders.csv").is_file()
    manifest = json.loads((handoff_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["phase"] == "browse_download"
    assert "orders.csv" in manifest["artifacts"]
    assert ended["end_event_id"]

    status = harness.status()
    assert status["current_phase"] is None
    assert status["phase_count"] == 1
    assert status["phases"][0]["ok_steps"] == 1

    # iterate: new run seeded from handoff
    child = RunSession.create(
        ws,
        run_id="phase2",
        skills_root=skills,
        auto_approve=True,
    )
    child.start_run("normalize only", phase_plan=["normalize"])
    seeded = child.seed_from_handoff(handoff_dir)
    assert seeded["count"] == 1
    assert (ws.artifacts / "phase2" / "orders.csv").is_file()

    child.begin_phase("normalize")
    nstep = child.invoke("browser", "snapshot", {})
    assert nstep.phase == "normalize"
    assert child.end_phase(summary={"rows": 1})["status"] == "done"
    assert len(child.list_phases()) == 2


def test_phase_require_artifact_gate(tmp_path: Path) -> None:
    ws = _open_ws(tmp_path)
    harness = RunSession.create(
        ws,
        run_id="gate1",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("gate")
    harness.begin_phase("ingest", require_artifacts=["must.csv"])
    try:
        harness.end_phase()
        raised = False
    except ValueError as exc:
        raised = "must.csv" in str(exc)
    assert raised


def test_phase_retry(tmp_path: Path) -> None:
    ws = _open_ws(tmp_path)
    harness = RunSession.create(
        ws,
        run_id="pbudget",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("retry phase")
    harness.begin_phase("tight")
    harness.fail_phase(error="operator retry")
    retried = harness.retry_phase("tight")
    assert retried["status"] == "open"
    assert retried["index"] == 2
    assert harness.invoke("browser", "snapshot", {}).status == "ok"
    harness.end_phase()


def test_cli_phases_and_from_handoff(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    skills = str(default_skills_root())
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    common = ["--home", home, "--env", "dev", "--skills", skills, "--auto-approve"]

    start = _roi_h(
        "rpa",
        "start",
        *common,
        "--run-id",
        "cli-phase",
        "--goal",
        "phased job",
        "--phase",
        "download",
        "--phase",
        "parse",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr
    body = json.loads(start.stdout)
    assert len(body["phase_plan"]) == 2

    begin = _roi_h(
        "rpa",
        "phase",
        "begin",
        *common,
        "--run-id",
        "cli-phase",
        "download",
        "--require-artifact",
        "page.txt",
        cwd=tmp_path,
    )
    assert begin.returncode == 0, begin.stderr

    inv = _roi_h(
        "rpa",
        "invoke",
        *common,
        "--run-id",
        "cli-phase",
        "browser",
        "navigate",
        "--args",
        '{"url":"https://example.com/"}',
        cwd=tmp_path,
    )
    assert inv.returncode == 0, inv.stderr
    assert json.loads(inv.stdout)["phase"] == "download"

    sample = tmp_path / "page.txt"
    sample.write_text("hello", encoding="utf-8")
    put = _roi_h(
        "rpa",
        "artifact",
        "put",
        *common,
        "--run-id",
        "cli-phase",
        "--file",
        str(sample),
        "--name",
        "page.txt",
        cwd=tmp_path,
    )
    assert put.returncode == 0, put.stderr

    end = _roi_h(
        "rpa",
        "phase",
        "end",
        *common,
        "--run-id",
        "cli-phase",
        "--summary",
        '{"ok":true}',
        cwd=tmp_path,
    )
    assert end.returncode == 0, end.stderr
    end_body = json.loads(end.stdout)
    handoff = end_body["phase"]["handoff_path"]
    assert handoff

    listed = _roi_h(
        "rpa",
        "phase",
        "list",
        *common,
        "--run-id",
        "cli-phase",
        cwd=tmp_path,
    )
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)["count"] == 1

    seed_start = _roi_h(
        "rpa",
        "start",
        *common,
        "--run-id",
        "cli-phase-2",
        "--goal",
        "from handoff",
        "--from-handoff",
        handoff,
        cwd=tmp_path,
    )
    assert seed_start.returncode == 0, seed_start.stderr
    seeded = json.loads(seed_start.stdout)["seeded"]
    assert seeded["count"] == 1

    status = _roi_h(
        "rpa",
        "status",
        *common,
        "--run-id",
        "cli-phase-2",
        cwd=tmp_path,
    )
    assert status.returncode == 0, status.stderr
    st = json.loads(status.stdout)
    assert st["phase_count"] == 1
    assert st["phases"][0]["status"] == "done"
