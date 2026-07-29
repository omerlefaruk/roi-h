"""Unit tests for skills discovery."""

from pathlib import Path

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
