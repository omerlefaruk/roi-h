"""Managed global instructions for AI agents that use ROI-H."""

from __future__ import annotations

import os
from pathlib import Path

from roi_h.harness.atomicfs import atomic_write_text

INSTRUCTIONS_VERSION = 2
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
- Treat the live operation manifest as the authority for schemas, effects, approvals,
  idempotency, plans, secrets, pagination, tasks, and time limits.
- Use a stable idempotency key for each write. Retry a lost write with the same operation,
  context, arguments, and key.
- Use `approval_mode: "full"` on `tool.invoke` only when the user explicitly gives
  full or unattended authority for that scope. Otherwise, show each pending effect and
  ask for approval.
- Full tool approval does not bypass production policy, secret handling, or destructive
  plans. Use the related `.plan` operation before a destructive `.apply` operation.
- Never put secret values in prompts, JSON, arguments, logs, plans, or files. Use the
  secure standard-input channel.
- For product tasks, do not bypass ROI-H with direct browser, shell, network, file, or
  database operations.
- Build repeatable work in `dev`, verify the run evidence, ship an immutable automation,
  dry-run it, and use `prod` only when the user requests a production run.
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


def install_agent_instructions(
    user_home: Path | None = None,
) -> tuple[tuple[Path, bool], ...]:
    """Install or update the managed block without changing other instructions."""
    paths = instruction_paths(user_home)
    originals: dict[Path, str | None] = {}
    updates: dict[Path, str] = {}
    for path in paths:
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        originals[path] = existing
        updates[path] = _merge_instructions(existing or "")

    written: list[Path] = []
    try:
        for path in paths:
            if updates[path] == originals[path]:
                continue
            atomic_write_text(path, updates[path])
            written.append(path)
    except OSError:
        for path in reversed(written):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_text(path, original)
        raise
    return tuple((path, path in written) for path in paths)


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
    "BEGIN_MARKER",
    "END_MARKER",
    "INSTRUCTIONS_VERSION",
    "MANAGED_INSTRUCTIONS",
    "install_agent_instructions",
    "instruction_paths",
]
