"""Managed global instructions for AI agents that use ROI-H."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

from roi_h.harness.atomicfs import atomic_write_text

INSTRUCTIONS_VERSION = 4
AGENT_SKILLS = ("migrate-code-automation",)
AGENT_SKILL_MARKER = "<!-- ROI-H managed agent skill -->"
_AGENT_SKILL_FILES = ("SKILL.md", "agents/openai.yaml")
BEGIN_MARKER = "<!-- ROI-H instructions: begin -->"
END_MARKER = "<!-- ROI-H instructions: end -->"
MANAGED_INSTRUCTIONS = f"""\
{BEGIN_MARKER}
## ROI-H CLI

When a task uses ROI-H:

- Use the installed `roi-h` command as the ROI-H interface.
- Run `roi-h agent context` first.
- Run `roi-h agent describe` and `roi-h agent describe <operation>` before calls.
- Call operations with `roi-h agent call <operation> --input <file|->`.
- Treat the live operation manifest as the authority for schemas, effects,
  idempotency, plans, secrets, pagination, tasks, and time limits.
- Use a stable idempotency key for each write. Retry a lost write with the same operation,
  context, arguments, and key.
- Use the related `.plan` operation before a destructive `.apply` operation.
- Never put secret values in prompts, JSON, arguments, logs, plans, or files. Use the
  secure standard-input channel.
- For product tasks, do not bypass ROI-H with direct browser, shell, network, file, or
  database operations. For code migration only, you can inspect user-supplied source files
  read-only. Do not execute or change the legacy automation.
- Read `skill.list` and `skill.show` for Markdown guidance. Skills do not execute code.
- Create repeatable work with `automation.source.put`. Use small Python phase modules,
  dependency edges, artifacts, and a final verification phase. Do not create one large
  script.
- Run editable source with `automation.dev.run`. ROI-H freezes the source before it runs
  and records phase, input, artifact, and completion evidence in ActiveGraph.
- Ship only from a successful development run with `automation.ship`. Run the immutable
  package with `automation.run` in `prod` only when the user requests a production run.
- Report the project, environment, run or task ID, automation version, artifacts,
  approvals, warnings, and required user action.
{END_MARKER}"""


def instruction_paths(user_home: Path | None = None) -> tuple[Path, Path]:
    """Return the supported user-level instruction files."""
    if user_home is None:
        resolved_home = Path.home()
        configured_codex_home = os.environ.get("CODEX_HOME")
        codex_home = (
            Path(configured_codex_home).expanduser()
            if configured_codex_home
            else resolved_home / ".codex"
        )
    else:
        resolved_home = user_home.expanduser()
        codex_home = resolved_home / ".codex"
    return codex_home / "AGENTS.md", resolved_home / ".agents" / "AGENTS.md"


def agent_skill_paths(user_home: Path | None = None) -> tuple[Path, ...]:
    """Return the managed agent skill directories."""
    instruction_files = instruction_paths(user_home)
    return tuple(
        instruction_file.parent / "skills" / name
        for instruction_file in instruction_files
        for name in AGENT_SKILLS
    )


def install_agent_instructions(
    user_home: Path | None = None,
) -> tuple[tuple[Path, bool], ...]:
    """Install or update managed instructions and agent skills."""
    instruction_files = instruction_paths(user_home)
    updates: dict[Path, str] = {}
    for path in instruction_files:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        updates[path] = _merge_instructions(existing)

    updates.update(_agent_skill_updates(user_home))

    originals = {
        path: path.read_text(encoding="utf-8") if path.exists() else None for path in updates
    }
    written: list[Path] = []
    try:
        for path, text in updates.items():
            if text == originals[path]:
                continue
            atomic_write_text(path, text)
            written.append(path)
    except OSError:
        for path in reversed(written):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_text(path, original)
        raise
    return tuple((path, path in written) for path in updates)


def _agent_skill_updates(user_home: Path | None) -> dict[Path, str]:
    source_root = resources.files("roi_h").joinpath("_agent_skills")
    updates: dict[Path, str] = {}
    for skill_path in agent_skill_paths(user_home):
        source_skill = source_root.joinpath(skill_path.name)
        source_files = {
            relative: source_skill.joinpath(*relative.split("/")).read_text(encoding="utf-8")
            for relative in _AGENT_SKILL_FILES
        }
        existing_files = [skill_path / relative for relative in _AGENT_SKILL_FILES]
        if any(path.exists() for path in existing_files):
            manifest = skill_path / "SKILL.md"
            existing_manifest = manifest.read_text(encoding="utf-8") if manifest.is_file() else ""
            if (
                existing_manifest != source_files["SKILL.md"]
                and AGENT_SKILL_MARKER not in existing_manifest
            ):
                msg = f"An unmanaged agent skill already exists: {skill_path}"
                raise FileExistsError(msg)
        updates.update((skill_path / relative, text) for relative, text in source_files.items())
    return updates


def _merge_instructions(existing: str) -> str:
    begin_count = existing.count(BEGIN_MARKER)
    end_count = existing.count(END_MARKER)
    if begin_count == end_count == 0:
        separator = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        return f"{existing}{separator}{MANAGED_INSTRUCTIONS}\n"
    if begin_count != 1 or end_count != 1:
        msg = "The instruction file has invalid ROI-H managed markers."
        raise ValueError(msg)
    begin = existing.index(BEGIN_MARKER)
    end = existing.index(END_MARKER, begin) + len(END_MARKER)
    return f"{existing[:begin]}{MANAGED_INSTRUCTIONS}{existing[end:]}"


__all__ = [
    "AGENT_SKILLS",
    "AGENT_SKILL_MARKER",
    "BEGIN_MARKER",
    "END_MARKER",
    "INSTRUCTIONS_VERSION",
    "MANAGED_INSTRUCTIONS",
    "agent_skill_paths",
    "install_agent_instructions",
    "instruction_paths",
]
