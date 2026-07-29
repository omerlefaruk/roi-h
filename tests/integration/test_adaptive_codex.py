"""Codex CLI-backed ActiveGraph adaptive execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roi_h import RunSession
from roi_h.cli import main
from roi_h.harness.custom import define_project_tool
from roi_h.harness.loader import default_skills_root
from roi_h.harness.workspace import Workspace, create_project

_ECHO_TOOL = """\
from pydantic import BaseModel

TOOL_ID = "echo"
DESCRIPTION = "Echo text"
REQUIRES_APPROVAL = False

class Input(BaseModel):
    text: str

class Output(BaseModel):
    text: str

def run(args: Input) -> Output:
    return Output(text=args.text)
"""

_FAKE_CODEX = """\
#!/usr/bin/env python3
import json
import os
import pathlib
import sys

sys.stdin.read()
if os.environ.get("FAKE_CODEX_FAIL"):
    print("codex unavailable", file=sys.stderr)
    raise SystemExit(7)
if "--skip-git-repo-check" not in sys.argv:
    print("missing --skip-git-repo-check", file=sys.stderr)
    raise SystemExit(8)
output = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
state = pathlib.Path(os.environ["FAKE_CODEX_STATE"])
turn = int(state.read_text() or "0") if state.exists() else 0
state.write_text(str(turn + 1))
if turn == 0 or os.environ.get("FAKE_CODEX_ALWAYS_INVOKE"):
    payload = {
        "action": "invoke",
        "tool": "util.echo",
        "args_json": json.dumps({"text": "chosen by codex"}),
        "summary": "",
    }
else:
    payload = {
        "action": "finish",
        "tool": None,
        "args_json": "{}",
        "summary": "echo verified",
    }
raw = json.dumps(payload)
output.write_text(raw)
print(raw)
"""


def _workspace(tmp_path: Path, *, env: str = "dev") -> Workspace:
    home = tmp_path / ".roi-h"
    create_project(home, "adaptive", set_active=True)
    return Workspace.open(home, project="adaptive", env=env)


def _fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "codex"
    executable.write_text(_FAKE_CODEX, encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("ROI_H_CODEX_BIN", str(executable))
    monkeypatch.setenv("FAKE_CODEX_STATE", str(tmp_path / "codex-state"))


def test_adaptive_codex_reopens_and_uses_durable_invocation_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_codex(tmp_path, monkeypatch)
    workspace = _workspace(tmp_path)
    define_project_tool(
        skill="util",
        tool="echo",
        description="Echo text",
        project_root=workspace.project_skills,
        source=_ECHO_TOOL,
    )
    created = RunSession.create(
        workspace,
        run_id="adaptive-run",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    created.start_run("adapt safely")

    session = RunSession.reopen(
        workspace,
        run_id="adaptive-run",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    result = session.adapt(
        "Use util.echo once, verify its result, then finish.",
        tools=["util.echo"],
        max_turns=3,
    )

    assert result["ok"] is True
    assert result["summary"] == "echo verified"
    assert result["executed"][0]["output"] == {"text": "chosen by codex"}
    assert len(session.runtime.graph.objects(type="rpa.invocation")) == 1
    assert len(session.runtime.graph.objects(type="rpa.step")) == 1
    event_types = [event.type for event in session.runtime.graph.events]
    assert event_types.count("llm.requested") == 2
    assert event_types.count("llm.responded") == 2
    assert "tool.requested" in event_types
    assert "tool.responded" in event_types


def test_adaptive_codex_rejects_prod_and_destructive_tools(tmp_path: Path) -> None:
    dev = _workspace(tmp_path)
    session = RunSession.create(
        dev,
        run_id="adaptive-safety",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    session.start_run("stay safe")

    with pytest.raises(ValueError, match="forbids destructive"):
        session.adapt("run a command", tools=["shell.run"])

    prod = Workspace.open(dev.root, project=dev.project, env="prod")
    prod_session = RunSession.create(
        prod,
        run_id="adaptive-prod",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    prod_session.start_run("must remain deterministic")
    with pytest.raises(ValueError, match="restricted to dev"):
        prod_session.adapt("inspect", tools=["files.read"])


def test_adaptive_codex_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fake_codex(tmp_path, monkeypatch)
    workspace = _workspace(tmp_path)
    define_project_tool(
        skill="util",
        tool="echo",
        description="Echo text",
        project_root=workspace.project_skills,
        source=_ECHO_TOOL,
    )
    session = RunSession.create(
        workspace,
        run_id="adaptive-cli",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    session.start_run("adapt from CLI")

    exit_code = main(
        [
            "rpa",
            "adapt",
            "--home",
            str(workspace.root),
            "--project",
            workspace.project,
            "--env",
            "dev",
            "--skills",
            str(default_skills_root()),
            "--run-id",
            "adaptive-cli",
            "--auto-approve",
            "--goal",
            "Echo once and finish",
            "--tool",
            "util.echo",
            "--max-turns",
            "3",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["executed"][0]["tool"] == "echo"


def test_adaptive_codex_turn_bound_and_provider_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_codex(tmp_path, monkeypatch)
    workspace = _workspace(tmp_path)
    define_project_tool(
        skill="util",
        tool="echo",
        description="Echo text",
        project_root=workspace.project_skills,
        source=_ECHO_TOOL,
    )
    session = RunSession.create(
        workspace,
        run_id="adaptive-bounds",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    session.start_run("bounded")

    monkeypatch.setenv("FAKE_CODEX_ALWAYS_INVOKE", "1")
    bounded = session.adapt("keep echoing", tools=["util.echo"], max_turns=1)
    assert bounded["ok"] is False
    assert bounded["status"] == "max_turns"
    assert len(bounded["executed"]) == 1

    monkeypatch.delenv("FAKE_CODEX_ALWAYS_INVOKE")
    monkeypatch.setenv("FAKE_CODEX_FAIL", "1")
    failed = session.adapt("try again", tools=["util.echo"], max_turns=1)
    assert failed["ok"] is False
    assert failed["status"] == "failed"
    assert "codex unavailable" in failed["error"]
