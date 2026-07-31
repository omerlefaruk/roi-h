"""Distribution-content qualification."""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_PUBLIC_SKILLS = {"browser", "codex_chrome", "excel", "files", "pdf"}
_PUBLIC_AGENT_SKILLS = {"migrate-code-automation"}


def test_wheel_contains_only_roi_h_and_distribution_metadata(tmp_path: Path) -> None:
    subprocess.run(  # noqa: S603 - fixed local interpreter and build module
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("roi_h-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()

    assert "roi_h/py.typed" in names
    assert "roi_h/harness/application.py" in names
    assert "roi_h/_agent_skills/migrate-code-automation/SKILL.md" in names
    assert "roi_h/_agent_skills/migrate-code-automation/agents/openai.yaml" in names
    assert "roi_h/application.py" not in names
    assert "roi_h/domain.py" not in names
    assert not any(name.startswith("roi_h/packs/") for name in names)
    assert "roi_h/_skills/SKILL.md" not in names
    assert "roi_h/_skills/browser/scripts/navigate.py" in names
    assert "roi_h/_skills/files/scripts/hash.py" in names
    assert not any(
        name.startswith(
            (
                "roi_h/ata/",
                "roi_h/_skills/ata/",
                "roi_h/_skills/feedback/",
                "roi_h/_skills/http/",
                "roi_h/_skills/shell/",
            )
        )
        for name in names
    )
    assert all(name.startswith(("roi_h/", "roi_h-")) for name in names)
    assert not any("automationbench" in name.lower() or "/ab/" in name.lower() for name in names)
    assert not any(name.startswith("tests/") for name in names)
    packaged_skills = {
        name.split("/")[2]
        for name in names
        if name.startswith("roi_h/_skills/") and len(name.split("/")) > 3
    }
    packaged_agent_skills = {
        name.split("/")[2]
        for name in names
        if name.startswith("roi_h/_agent_skills/") and len(name.split("/")) > 3
    }
    assert packaged_skills == _PUBLIC_SKILLS
    assert packaged_agent_skills == _PUBLIC_AGENT_SKILLS
    assert any(".dist-info/licenses/LICENSE" in name for name in names)

    installed = tmp_path / "installed"
    installed.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)  # noqa: S202 - wheel was built locally in this test
    env = dict(os.environ)
    env["PYTHONPATH"] = str(installed)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from roi_h.agent_instructions import install_agent_instructions; "
                "from roi_h.harness.loader import default_skills_root, load_skills; "
                "root=default_skills_root(); "
                "catalog=load_skills(); "
                "assert root.name == '_skills'; "
                "assert catalog.resolve('browser', 'navigate'); "
                "home=Path.cwd() / 'agent-home'; "
                "install_agent_instructions(home); "
                "assert (home / '.codex/skills/migrate-code-automation/SKILL.md').is_file(); "
                "assert (home / '.agents/skills/migrate-code-automation/SKILL.md').is_file()"
            ),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr


def test_sdist_contains_only_generic_build_inputs(tmp_path: Path) -> None:
    subprocess.run(  # noqa: S603 - fixed local interpreter and build module
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    archive_path = next(tmp_path.glob("roi_h-*.tar.gz"))
    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()

    relative = [name.split("/", 1)[1] for name in names if "/" in name]
    top_level = {name.split("/", 1)[0] for name in relative}
    assert top_level <= {
        ".gitignore",
        "LICENSE",
        "PKG-INFO",
        "README.md",
        "pyproject.toml",
        "skills",
        "src",
    }
    assert not any(name.startswith("tests/") for name in relative)
    assert not any(
        marker in name.lower()
        for name in relative
        for marker in ("daily_summary", "trendyol", "/ata/", "automationbench")
    )
