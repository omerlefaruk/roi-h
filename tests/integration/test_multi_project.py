"""Named projects under one home are isolated."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from roi_h.harness.loader import default_skills_root


def _roi_h(*args: str, cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        input=stdin,
    )


_ECHO = """\
from pydantic import BaseModel
TOOL_ID = "echo"
DESCRIPTION = "echo"
DETERMINISTIC = True
REQUIRES_APPROVAL = False

class Input(BaseModel):
    text: str = ""

class Output(BaseModel):
    text: str = ""

def run(args: Input) -> Output:
    return Output(text=args.text)
"""


def test_project_isolation_and_switch(tmp_path: Path) -> None:
    home = str(tmp_path / ".roi-h")
    skills = str(default_skills_root())

    a = _roi_h(
        "rpa",
        "project",
        "create",
        "acme",
        "--display-name",
        "Acme",
        "--home",
        home,
        cwd=tmp_path,
    )
    assert a.returncode == 0, a.stderr
    b = _roi_h("rpa", "project", "create", "beta", "--home", home, "--no-use", cwd=tmp_path)
    assert b.returncode == 0, b.stderr

    listed = _roi_h("rpa", "project", "list", "--home", home, cwd=tmp_path)
    assert listed.returncode == 0, listed.stderr
    payload = json.loads(listed.stdout)
    assert payload["count"] == 2
    assert payload["active"] == "acme"

    custom = _roi_h(
        "rpa",
        "custom",
        "--home",
        home,
        "--project",
        "acme",
        "--env",
        "dev",
        "--skills",
        skills,
        "--skill",
        "util",
        "--tool",
        "echo",
        "--script",
        "-",
        cwd=tmp_path,
        stdin=_ECHO,
    )
    assert custom.returncode == 0, custom.stderr

    tools_acme = _roi_h(
        "rpa",
        "tools",
        "--home",
        home,
        "--project",
        "acme",
        "--skills",
        skills,
        cwd=tmp_path,
    )
    assert tools_acme.returncode == 0, tools_acme.stderr
    acme_names = {tool["name"] for tool in json.loads(tools_acme.stdout)["tools"]}
    assert "util.echo" in acme_names

    tools_beta = _roi_h(
        "rpa",
        "tools",
        "--home",
        home,
        "--project",
        "beta",
        "--skills",
        skills,
        cwd=tmp_path,
    )
    assert tools_beta.returncode == 0, tools_beta.stderr
    beta_names = {tool["name"] for tool in json.loads(tools_beta.stdout)["tools"]}
    assert "util.echo" not in beta_names
    assert "browser.navigate" in beta_names

    use = _roi_h("rpa", "project", "use", "beta", "--home", home, cwd=tmp_path)
    assert use.returncode == 0, use.stderr
    assert json.loads(use.stdout)["project"] == "beta"

    start = _roi_h(
        "rpa",
        "start",
        "--home",
        home,
        "--skills",
        skills,
        "--run-id",
        "beta-run",
        "--goal",
        "on beta",
        "--auto-approve",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr
    assert json.loads(start.stdout)["project"] == "beta"
