"""Reusable custom skills stay in user-owned storage."""

from pathlib import Path

import pytest

from roi_h.harness.custom import promote_to_global
from roi_h.harness.loader import default_skills_root

_TOOL = """\
from pydantic import BaseModel

TOOL_ID = "read"
DESCRIPTION = "Read a value"
TOOL_EFFECT = "read"

class Input(BaseModel):
    value: str = ""

class Output(BaseModel):
    ok: bool = True

def run(args: Input) -> Output:
    return Output()
"""


def _project_skill(root: Path) -> Path:
    skill = root / "example"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# example\n", encoding="utf-8")
    (skill / "scripts" / "read.py").write_text(_TOOL, encoding="utf-8")
    return skill


def test_promote_copies_to_user_shared_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _project_skill(project)
    shared = tmp_path / "home" / "skills"

    result = promote_to_global(
        skill="example",
        project_root=project,
        global_root=shared,
    )

    assert result["shared_root"] == str(shared.resolve())
    assert (shared / "example" / "scripts" / "read.py").is_file()


def test_promote_rejects_packaged_core_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _project_skill(project)

    with pytest.raises(ValueError, match="core skills are immutable"):
        promote_to_global(
            skill="example",
            project_root=project,
            global_root=default_skills_root(),
        )
