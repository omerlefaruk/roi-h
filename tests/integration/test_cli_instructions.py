"""Public CLI tests for global AI-agent instructions."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def test_instructions_install_preserves_existing_text_and_is_idempotent(
    tmp_path: Path,
) -> None:
    user_home = tmp_path / "customer"
    codex_file = user_home / ".codex" / "AGENTS.md"
    agents_file = user_home / ".agents" / "AGENTS.md"
    codex_file.parent.mkdir(parents=True)
    codex_file.write_text("# Customer rules\n\nKeep this text.\n", encoding="utf-8")

    first = _run(
        "instructions",
        "--install",
        "--user-home",
        str(user_home),
        "--output",
        "json",
        cwd=tmp_path,
    )
    second = _run(
        "instructions",
        "--install",
        "--user-home",
        str(user_home),
        "--output",
        "json",
        cwd=tmp_path,
    )

    assert first.returncode == 0, first.stdout
    assert second.returncode == 0, second.stdout
    assert first.stderr == second.stderr == ""
    assert json.loads(first.stdout)["changed"] is True
    assert json.loads(second.stdout)["changed"] is False
    for path in (codex_file, agents_file):
        text = path.read_text(encoding="utf-8")
        assert text.count("<!-- ROI-H instructions: begin -->") == 1
        assert text.count("<!-- ROI-H instructions: end -->") == 1
        assert "`roi-h agent context`" in text
        assert "`roi-h agent describe`" in text
        assert '`approval_mode: "full"`' in text
        assert "inspect user-supplied source files" in text
    for root in (user_home / ".codex", user_home / ".agents"):
        skill = root / "skills" / "migrate-code-automation"
        assert "name: migrate-code-automation" in (skill / "SKILL.md").read_text(encoding="utf-8")
        assert "$migrate-code-automation" in (skill / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
    assert len(json.loads(first.stdout)["files"]) == 6
    assert codex_file.read_text(encoding="utf-8").startswith(
        "# Customer rules\n\nKeep this text.\n"
    )


def test_instructions_install_preserves_an_unmanaged_skill(tmp_path: Path) -> None:
    user_home = tmp_path / "customer"
    skill_file = user_home / ".codex" / "skills" / "migrate-code-automation" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Customer skill\n", encoding="utf-8")

    completed = _run(
        "instructions",
        "--install",
        "--user-home",
        str(user_home),
        "--output",
        "json",
        cwd=tmp_path,
    )

    assert completed.returncode == 1
    assert "unmanaged agent skill already exists" in completed.stdout
    assert skill_file.read_text(encoding="utf-8") == "# Customer skill\n"
    assert not (user_home / ".codex" / "AGENTS.md").exists()
    assert not (user_home / ".agents").exists()


def test_instructions_command_prints_the_managed_block(tmp_path: Path) -> None:
    completed = _run("instructions", cwd=tmp_path)

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.startswith("<!-- ROI-H instructions: begin -->\n")
    assert completed.stdout.endswith("<!-- ROI-H instructions: end -->\n")
