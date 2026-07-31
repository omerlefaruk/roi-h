"""Fail when private operator material crosses into the tracked core repository."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

PUBLIC_SKILLS = frozenset({"browser", "codex_chrome", "excel", "files", "pdf"})
PUBLIC_AGENT_SKILLS = frozenset({"migrate-code-automation"})
_AGENT_SKILL_PART = 3
_HISTORICAL_PUBLIC_SKILL_FILES = frozenset(
    {
        "skills/feedback/SKILL.md",
        "skills/feedback/scripts/list.py",
        "skills/feedback/scripts/record.py",
        "skills/http/SKILL.md",
        "skills/http/scripts/download.py",
        "skills/http/scripts/get.py",
        "skills/http/scripts/post.py",
        "skills/shell/SKILL.md",
        "skills/shell/scripts/run.py",
    }
)

_FORBIDDEN_EXACT = frozenset(
    {
        ".design-qa-comparison.jpg",
        ".design-qa-observer.jpg",
        "CONTEXT.md",
        "challenge.xlsx",
        "design-qa.md",
        "docs/DAILY_SUMMARY_HANDOFF.md",
        "docs/extracted-daily-summary-automation.json",
        "tests/integration/test_rpa_challenge_codex.py",
        "tests/unit/test_ata_analysis.py",
        "tests/unit/test_daily_summary_close.py",
        "tests/unit/test_daily_summary_report_parse.py",
    }
)
_FORBIDDEN_PREFIXES = (
    ".roi-h/",
    "analysis/",
    "artifacts/",
    "automations/",
    "customer/",
    "custom-skills/",
    "docs/daily-summary-",
    "docs/extracted-scripts/",
    "docs/trendyol-satis-raporu-steps.",
    "private/",
    "projects/",
    "src/roi_h/ata/",
    "tests/fixtures/daily_summary/",
)
_FORBIDDEN_DIRECTORY_NAMES = frozenset(
    {
        "artifacts",
        "automations",
        "browser-profile",
        "chrome-automation-profile",
    }
)
_FORBIDDEN_FILE_NAMES = frozenset(
    {
        "Cookies",
        "Login Data",
        "browser-session.json",
        "secrets.json",
    }
)
_FORBIDDEN_SUFFIXES = (
    ".har",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".xlsx",
)


def publication_violations(paths: Iterable[str], *, history: bool = False) -> list[str]:
    """Return tracked paths that do not belong in the generic core repository."""
    violations: list[str] = []
    for raw_path in paths:
        path = raw_path.removeprefix("./")
        parts = Path(path).parts
        if path in _FORBIDDEN_EXACT or path.startswith(_FORBIDDEN_PREFIXES):
            violations.append(path)
            continue
        if _FORBIDDEN_DIRECTORY_NAMES.intersection(parts):
            violations.append(path)
            continue
        if Path(path).name in _FORBIDDEN_FILE_NAMES or path.endswith(_FORBIDDEN_SUFFIXES):
            violations.append(path)
            continue
        if path.startswith("src/roi_h/_agent_skills/"):
            skill = parts[_AGENT_SKILL_PART] if len(parts) > _AGENT_SKILL_PART else ""
            if skill and skill not in PUBLIC_AGENT_SKILLS:
                violations.append(path)
        elif path.startswith("skills/"):
            skill = parts[1] if len(parts) > 1 else ""
            retired_public_file = history and path in _HISTORICAL_PUBLIC_SKILL_FILES
            if (
                skill
                and skill not in PUBLIC_SKILLS
                and skill != "SKILL.md"
                and not retired_public_file
            ):
                violations.append(path)
    return sorted(set(violations))


def tracked_files(repository: Path) -> list[str]:
    """Return files present in the candidate Git tree, excluding pending deletions."""
    git = shutil.which("git")
    if git is None:
        msg = "git is required to validate the publication boundary"
        raise RuntimeError(msg)
    completed = subprocess.run(  # noqa: S603 - resolved trusted git executable
        [git, "-C", str(repository), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    tracked = completed.stdout.decode("utf-8").split("\0")
    return [path for path in tracked if path and (repository / path).exists()]


def historical_files(repository: Path) -> list[str]:
    """Return every path recorded by any local Git ref."""
    git = shutil.which("git")
    if git is None:
        msg = "git is required to validate the publication boundary"
        raise RuntimeError(msg)
    completed = subprocess.run(  # noqa: S603 - resolved trusted git executable
        [git, "-C", str(repository), "log", "--all", "--format=", "--name-only", "-z"],
        check=True,
        capture_output=True,
    )
    paths = completed.stdout.decode("utf-8").split("\0")
    return [path.strip("\n") for path in paths if path.strip("\n")]


def main() -> int:
    """Validate the current repository and print an actionable failure."""
    repository = Path(__file__).resolve().parents[1]
    arguments = sys.argv[1:]
    if arguments not in ([], ["--history"]):
        sys.stderr.write("Usage: check_publication_boundary.py [--history]\n")
        return 2
    check_history = arguments == ["--history"]
    candidates = historical_files(repository) if check_history else tracked_files(repository)
    violations = publication_violations(candidates, history=check_history)
    if not violations:
        scope = "history" if check_history else "current tree"
        sys.stdout.write(f"Publication boundary ({scope}): OK\n")
        return 0
    lines = [
        (
            "Private or customer material exists in Git history:"
            if check_history
            else "Private or customer material is tracked in the generic ROI-H core:"
        ),
        *(f"- {path}" for path in violations),
        (
            "Do not push these refs. Create a sanitized public history first."
            if check_history
            else "Move it under ~/.roi-h/projects/<name>/ or ~/.roi-h/private/."
        ),
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
