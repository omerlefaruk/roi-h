"""Operator CLI: the surface external AIs should use."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

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


def test_store_status_honors_db_override(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr

    db = tmp_path / "override.sqlite"
    with sqlite3.connect(db):
        pass
    status = _roi_h(
        "rpa",
        "store",
        "status",
        "--home",
        home,
        "--db",
        str(db),
        cwd=tmp_path,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["exists"] is True


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


def test_required_human_read_command_groups(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    start = _roi_h(
        "rpa",
        "start",
        "--home",
        home,
        "--run-id",
        "human-read-run",
        "--goal",
        "Inspect command groups",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr

    commands = (
        ("runs", "list", "--home", home),
        ("runs", "show", "human-read-run", "--home", home),
        ("events", "list", "--run-id", "human-read-run", "--home", home),
        ("trace", "show", "--run-id", "human-read-run", "--home", home),
        ("skill", "list", "--home", home),
        ("automation", "list", "--home", home),
    )
    for command in commands:
        result = _roi_h("rpa", *command, cwd=tmp_path)
        assert result.returncode == 0, (command, result.stdout, result.stderr)
        assert json.loads(result.stdout)


def test_secret_set_accepts_stdin_and_rejects_positional_values(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    secret = f"value-{uuid4().hex}"

    rejected = _roi_h(
        "rpa",
        "secret",
        "set",
        "TOKEN",
        secret,
        "--home",
        home,
        cwd=tmp_path,
    )
    assert rejected.returncode != 0
    assert secret not in rejected.stdout
    assert secret not in rejected.stderr

    accepted = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "roi_h",
            "rpa",
            "secret",
            "set",
            "TOKEN",
            "--value-stdin",
            "--home",
            home,
        ],
        cwd=tmp_path,
        input=secret,
        text=True,
        capture_output=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert secret not in accepted.stdout
    assert secret not in accepted.stderr
    assert json.loads(accepted.stdout)["name"] == "TOKEN"


def test_human_skill_promote_and_delete_plan_apply(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    define = _roi_h(
        "rpa",
        "skill",
        "define",
        "sample",
        "work",
        "--home",
        home,
        cwd=tmp_path,
    )
    assert define.returncode == 0, define.stderr

    promote = _roi_h("rpa", "skill", "promote", "sample", "--home", home, cwd=tmp_path)
    assert promote.returncode == 0, promote.stderr
    assert json.loads(promote.stdout)["name"] == "sample"

    planned = _roi_h(
        "rpa",
        "skill",
        "delete",
        "plan",
        "sample",
        "--home",
        home,
        cwd=tmp_path,
    )
    assert planned.returncode == 0, planned.stderr
    plan_id = json.loads(planned.stdout)["plan_id"]
    applied = _roi_h(
        "rpa",
        "skill",
        "delete",
        "apply",
        plan_id,
        "--home",
        home,
        cwd=tmp_path,
    )
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["recoverable"] is True


def test_human_approval_reject_and_support_bundle_commands(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    skills = str(default_skills_root())
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    start = _roi_h(
        "rpa",
        "start",
        "--home",
        home,
        "--skills",
        skills,
        "--run-id",
        "human-approval-run",
        "--goal",
        "Reject an approval",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr
    invoke = _roi_h(
        "rpa",
        "invoke",
        "--home",
        home,
        "--skills",
        skills,
        "--run-id",
        "human-approval-run",
        "browser",
        "navigate",
        "--args",
        '{"url":"https://example.com/"}',
        cwd=tmp_path,
    )
    assert invoke.returncode == 1, invoke.stderr
    approval_id = json.loads(invoke.stdout)["approval_id"]

    rejected = _roi_h(
        "rpa",
        "approval",
        "reject",
        approval_id,
        "--run-id",
        "human-approval-run",
        "--home",
        home,
        "--skills",
        skills,
        "--reason",
        "not required",
        cwd=tmp_path,
    )
    assert rejected.returncode == 0, rejected.stderr
    assert json.loads(rejected.stdout)["status"] == "denied"

    bundle = tmp_path / "support.zip"
    supported = _roi_h(
        "diagnostics",
        "bundle",
        "--home",
        home,
        "--output",
        str(bundle),
        cwd=tmp_path,
    )
    assert supported.returncode == 0, supported.stderr
    assert bundle.is_file()


def test_human_destructive_commands_use_plan_and_apply(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    start = _roi_h(
        "rpa",
        "start",
        "--home",
        home,
        "--run-id",
        "plan-run",
        "--goal",
        "Create a store",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr
    backup_path = tmp_path / "store.sqlite"
    backup = _roi_h(
        "rpa",
        "store",
        "backup",
        "--home",
        home,
        "--output",
        str(backup_path),
        cwd=tmp_path,
    )
    assert backup.returncode == 0, backup.stderr

    restore_plan = _roi_h(
        "rpa",
        "store",
        "restore",
        "plan",
        str(backup_path),
        "--home",
        home,
        cwd=tmp_path,
    )
    assert restore_plan.returncode == 0, restore_plan.stderr
    restore_plan_id = json.loads(restore_plan.stdout)["plan_id"]
    restore_apply = _roi_h(
        "rpa",
        "store",
        "restore",
        "apply",
        restore_plan_id,
        "--home",
        home,
        cwd=tmp_path,
    )
    assert restore_apply.returncode == 0, restore_apply.stderr

    delete_plan = _roi_h(
        "rpa",
        "project",
        "delete",
        "plan",
        "demo",
        "--home",
        home,
        cwd=tmp_path,
    )
    assert delete_plan.returncode == 0, delete_plan.stderr
    delete_plan_id = json.loads(delete_plan.stdout)["plan_id"]
    delete_apply = _roi_h(
        "rpa",
        "project",
        "delete",
        "apply",
        delete_plan_id,
        "--home",
        home,
        cwd=tmp_path,
    )
    assert delete_apply.returncode == 0, delete_apply.stderr


def test_compatibility_command_prints_deprecation_only_to_stderr(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr

    result = _roi_h("rpa", "automations", "--home", home, cwd=tmp_path)

    assert result.returncode == 0
    assert json.loads(result.stdout)["ok"] is True
    assert "deprecated" in result.stderr.lower()
    assert "deprecated" not in result.stdout.lower()
