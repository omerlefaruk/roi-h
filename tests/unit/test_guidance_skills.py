"""Markdown-only guidance skill contract."""

from pathlib import Path

import pytest

from roi_h.harness.guidance_skills import load_guidance_skill, load_guidance_skills


def test_guidance_skill_accepts_markdown_references(tmp_path: Path) -> None:
    root = tmp_path / "portal"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: portal\ndescription: Operate the portal.\nversion: 1\n---\n\nUse phases.\n",
        encoding="utf-8",
    )
    (root / "references" / "selectors.md").write_text("# Selectors\n", encoding="utf-8")

    skill = load_guidance_skill(root, scope="project")

    assert skill.name == "portal"
    assert sorted(skill.documents) == ["SKILL.md", "references/selectors.md"]


def test_guidance_skill_rejects_executable_files(tmp_path: Path) -> None:
    root = tmp_path / "portal"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: portal\ndescription: Operate the portal.\n---\n",
        encoding="utf-8",
    )
    (root / "action.py").write_text("print('no')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="non-Markdown"):
        load_guidance_skill(root, scope="project")


def test_packaged_guidance_contains_only_expected_markdown_skills() -> None:
    skills = load_guidance_skills()

    assert set(skills) == {"browser", "excel", "files", "pdf"}
    assert all(set(skill.documents) == {"SKILL.md"} for skill in skills.values())


def test_guidance_skill_rejects_a_linked_skill_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "SKILL.md").write_text(
        "---\nname: linked\ndescription: Linked skill.\n---\n",
        encoding="utf-8",
    )
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links are not available: {exc}")

    with pytest.raises(ValueError, match="symbolic link"):
        load_guidance_skill(linked, scope="project")
