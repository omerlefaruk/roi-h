"""Unit tests for skills discovery."""

import json
import os
import time
from pathlib import Path

import pytest

from roi_h.harness.loader import default_skills_root, load_skills

_SHARED_TOOL = """\
from pydantic import BaseModel

TOOL_ID = "hello"
DESCRIPTION = "Shared hello"
TOOL_EFFECT = "read"

class Input(BaseModel):
    value: str = ""

class Output(BaseModel):
    ok: bool = True

def run(args: Input) -> Output:
    return Output()
"""


def test_load_skills_discovers_browser_stub_tools() -> None:
    catalog = load_skills(default_skills_root())
    names = {tool.name for tool in catalog.list_tools()}
    # browser core + expanded tools + global skills
    for required in (
        "browser.navigate",
        "browser.snapshot",
        "browser.click",
        "browser.fill",
        "browser.download",
        "browser.screenshot",
        "files.read",
        "excel.read_rows",
        "http.get",
        "feedback.record",
        "shell.run",
    ):
        assert required in names, required
    navigate = catalog.resolve("browser", "navigate")
    assert navigate.deterministic is False
    assert str(navigate.script_path).endswith("skills/browser/scripts/navigate.py")
    shell = catalog.resolve("shell", "run")
    assert shell.requires_approval is True


def test_user_shared_skills_load_between_core_and_project(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared_skill = shared / "shared-example"
    (shared_skill / "scripts").mkdir(parents=True)
    (shared_skill / "SKILL.md").write_text("# shared-example\n", encoding="utf-8")
    (shared_skill / "scripts" / "hello.py").write_text(_SHARED_TOOL, encoding="utf-8")

    catalog = load_skills(default_skills_root(), shared_root=shared)
    tool = catalog.resolve("shared-example", "hello")

    assert tool.scope == "shared"
    assert catalog.shared_root == shared.resolve()


def test_project_skill_inspection_runs_in_an_isolated_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    skill = project / "probe"
    probe = tmp_path / "import.json"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# probe\n", encoding="utf-8")
    monkeypatch.setenv("ROI_H_PARENT_ONLY", "must-not-leak")
    source = (
        "import json, os\n"
        "from pathlib import Path\n"
        "probe_data = {'pid': os.getpid(), "
        "'inherited': os.environ.get('ROI_H_PARENT_ONLY')}\n"
        f"Path({str(probe)!r}).write_text(json.dumps(probe_data))\n"
        "os.write(1, b'import noise')\n"
        + _SHARED_TOOL.replace('DESCRIPTION = "Shared hello"\n', "")
    )
    script = skill / "scripts" / "hello.py"
    script.write_text(source, encoding="utf-8")

    catalog = load_skills(default_skills_root(), project_root=project)

    imported = json.loads(probe.read_text(encoding="utf-8"))
    assert imported == {"pid": imported["pid"], "inherited": None}
    assert imported["pid"] != os.getpid()
    tool = catalog.resolve("probe", "hello")
    assert tool.scope == "project"
    assert tool.description == "probe.hello"
    assert tool.input_model.model_json_schema()["properties"]["value"]["type"] == "string"

    script.write_text(
        "import os\nos.write(1, b'x' * 2_000_000)\n" + _SHARED_TOOL,
        encoding="utf-8",
    )
    assert load_skills(default_skills_root(), project_root=project).resolve(
        "probe", "hello"
    )

    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'])\n"
        "time.sleep(1)\n"
        + _SHARED_TOOL,
        encoding="utf-8",
    )
    monkeypatch.setattr("roi_h.harness.loader._CUSTOM_INSPECTION_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="custom skill inspection exceeded"):
        load_skills(default_skills_root(), project_root=project)
    assert time.monotonic() - started < 1
