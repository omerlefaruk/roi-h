"""Project-local custom skills."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from roi_h.harness.loader import default_skills_root


def _roi_h(
    *args: str,
    cwd: Path,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        input=stdin,
    )


_FILTER_SCRIPT = """\
import os

from pydantic import BaseModel, Field

TOOL_ID = "filter_high"
DESCRIPTION = "Filter amounts above a threshold"
DETERMINISTIC = True
REQUIRES_APPROVAL = False

class Input(BaseModel):
    amounts: list[float]
    threshold: float = 1000

class Output(BaseModel):
    ok: bool = True
    kept: list[float] = Field(default_factory=list)

def run(args: Input) -> Output:
    os.write(1, b"x" * 2_000_000)
    return Output(ok=True, kept=[a for a in args.amounts if a > args.threshold])
"""


def test_custom_define_invoke_with_auto_approve(tmp_path: Path) -> None:
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
        "--run-id",
        "custom-job",
        "--goal",
        "Filter high amounts",
        "--auto-approve",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr

    custom = _roi_h(
        "rpa",
        "custom",
        "--home",
        home,
        "--env",
        "dev",
        "--skills",
        skills,
        "--skill",
        "finance",
        "--tool",
        "filter_high",
        "--description",
        "Filter amounts above threshold",
        "--script",
        "-",
        cwd=tmp_path,
        stdin=_FILTER_SCRIPT,
    )
    assert custom.returncode == 0, custom.stderr + custom.stdout

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
        "custom-job",
        "finance",
        "filter_high",
        "--args",
        '{"amounts":[10,2000,50,3000],"threshold":1000}',
        "--auto-approve",
        cwd=tmp_path,
    )
    assert invoke.returncode == 0, invoke.stderr
    step = json.loads(invoke.stdout)
    assert step["scope"] == "project"
    assert step["output"]["kept"] == [2000.0, 3000.0]
