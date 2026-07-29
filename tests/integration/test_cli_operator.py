"""Operator CLI: the surface external AIs should use."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from roi_h.harness.loader import default_skills_root


def _roi_h(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def test_operator_cli_tools_start_invoke_status_round_trip(tmp_path: Path) -> None:
    skills = str(default_skills_root())
    home = str(tmp_path / ".roi-h")
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr

    tools = _roi_h("rpa", "tools", "--home", home, "--env", "dev", "--skills", skills, cwd=tmp_path)
    assert tools.returncode == 0, tools.stderr
    tools_payload = json.loads(tools.stdout)
    assert tools_payload["ok"] is True
    names = {item["name"] for item in tools_payload["tools"]}
    assert "browser.navigate" in names

    start = _roi_h(
        "rpa",
        "start",
        "--home",
        home,
        "--env",
        "dev",
        "--skills",
        skills,
        "--run-id",
        "cli-job-1",
        "--goal",
        "Open example and snapshot",
        "--auto-approve",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr
    start_payload = json.loads(start.stdout)
    assert start_payload["run_id"] == "cli-job-1"
    assert start_payload["env"] == "dev"

    invoke = _roi_h(
        "rpa",
        "invoke",
        "--home",
        home,
        "--env",
        "dev",
        "--skills",
        skills,
        "--run-id",
        "cli-job-1",
        "browser",
        "navigate",
        "--args",
        '{"url":"https://example.com/"}',
        "--auto-approve",
        cwd=tmp_path,
    )
    assert invoke.returncode == 0, invoke.stderr
    step = json.loads(invoke.stdout)
    assert step["status"] == "ok"

    bad = _roi_h(
        "rpa",
        "invoke",
        "--home",
        home,
        "--env",
        "dev",
        "--skills",
        skills,
        "--run-id",
        "cli-job-1",
        "browser",
        "navigate",
        "--args",
        '{"url":"not-a-url"}',
        "--auto-approve",
        cwd=tmp_path,
    )
    assert bad.returncode == 1
    bad_payload = json.loads(bad.stdout)
    assert bad_payload["status"] == "error"

    status = _roi_h(
        "rpa",
        "status",
        "--home",
        home,
        "--env",
        "dev",
        "--skills",
        skills,
        "--run-id",
        "cli-job-1",
        "--auto-approve",
        cwd=tmp_path,
    )
    assert status.returncode == 1
    summary = json.loads(status.stdout)
    assert summary["step_count"] == 2
    assert summary["error_steps"] == 1


def test_cli_start_auto_run_id(tmp_path: Path) -> None:
    skills = str(default_skills_root())
    home = str(tmp_path / ".roi-h")
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    start = _roi_h(
        "rpa",
        "start",
        "--home",
        home,
        "--env",
        "dev",
        "--skills",
        skills,
        "--goal",
        "Auto id please",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr
    payload = json.loads(start.stdout)
    assert payload["run_id"]
