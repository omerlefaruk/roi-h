"""Project-local custom skills: define, advise promote, promote to global."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from roi_h.harness.loader import (
    SkillCatalog,
    default_skills_root,
    load_skills,
    validate_skill_token,
)
from roi_h.harness.workspace import resolve_home

_SCRIPT_CONTRACT = '''\
"""Project-local skill tool (generated/defined by the operator AI)."""

from __future__ import annotations

from pydantic import BaseModel, Field

TOOL_ID = "{tool_id}"
DESCRIPTION = {description!r}
DETERMINISTIC = True
REQUIRES_APPROVAL = False


class Input(BaseModel):
    """Replace fields with the real inputs this tool needs."""

    value: str = Field(default="", description="Example input — change me")


class Output(BaseModel):
    """Replace fields with the real outputs this tool returns."""

    ok: bool = True
    result: str = ""


def run(args: Input) -> Output:
    """Implement the tool body. Keep side effects explicit and logged via return value."""
    return Output(ok=True, result=args.value)
'''


def define_project_tool(
    *,
    skill: str,
    tool: str,
    project_root: str | Path,
    description: str = "",
    source: str | None = None,
    source_path: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create or replace a project-local skill tool script and SKILL.md stub."""
    skill_name = validate_skill_token(skill, kind="skill")
    tool_id = validate_skill_token(tool, kind="tool")
    root = Path(project_root).expanduser().resolve()
    skill_dir = root / skill_name
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    script_path = scripts_dir / f"{tool_id}.py"
    if script_path.exists() and not overwrite:
        msg = f"project tool already exists: {script_path} (pass overwrite=True to replace)"
        raise FileExistsError(msg)

    if source_path is not None:
        text = Path(source_path).expanduser().read_text(encoding="utf-8")
    elif source is not None:
        text = source
    else:
        text = _SCRIPT_CONTRACT.format(tool_id=tool_id, description=description or tool_id)

    script_path.write_text(text, encoding="utf-8")
    _ensure_skill_md(skill_dir, skill_name=skill_name, description=description)
    _ensure_tool_row_in_skill_md(
        skill_dir / "SKILL.md",
        tool_name=f"{skill_name}.{tool_id}",
        script_rel=f"scripts/{tool_id}.py",
        description=description or tool_id,
    )

    # Validate it loads as a real tool.
    catalog = load_skills(project_root=root, database=None)
    # Force discover even if only project tools: need global root present
    # load_skills always needs global; project_root alone is fine
    loaded = catalog.get(f"{skill_name}.{tool_id}")
    if loaded.scope != "project":
        # May resolve global if same name — still ok if script is ours
        pass

    return {
        "ok": True,
        "scope": "project",
        "skill": skill_name,
        "tool": tool_id,
        "name": f"{skill_name}.{tool_id}",
        "description": description or loaded.description,
        "script_path": str(script_path),
        "skill_md": str(skill_dir / "SKILL.md"),
        "project_root": str(root),
        "input_schema": loaded.input_model.model_json_schema(),
        "output_schema": loaded.output_model.model_json_schema(),
    }


def promote_advice(
    catalog: SkillCatalog,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recommend project-local tools that look worth promoting to global skills/."""
    usage: dict[str, dict[str, Any]] = {}
    for step in steps:
        data = step.get("data", step)
        name = str(data.get("name") or f"{data.get('skill')}.{data.get('tool')}")
        scope = data.get("scope") or _scope_from_catalog(catalog, name)
        if scope != "project":
            continue
        bucket = usage.setdefault(
            name,
            {
                "name": name,
                "skill": data.get("skill"),
                "tool": data.get("tool"),
                "scope": "project",
                "ok_count": 0,
                "error_count": 0,
                "description": _description(catalog, name),
                "script_path": _script_path(catalog, name),
            },
        )
        if data.get("status") == "ok":
            bucket["ok_count"] += 1
        else:
            bucket["error_count"] += 1

    # Include defined-but-unused project tools so the AI can still advise.
    for tool in catalog.project_tools():
        usage.setdefault(
            tool.name,
            {
                "name": tool.name,
                "skill": tool.skill,
                "tool": tool.tool_id,
                "scope": "project",
                "ok_count": 0,
                "error_count": 0,
                "description": tool.description,
                "script_path": str(tool.script_path),
            },
        )

    recommendations: list[dict[str, Any]] = []
    for item in sorted(usage.values(), key=lambda row: row["name"]):
        ok = int(item["ok_count"])
        err = int(item["error_count"])
        if ok >= 1 and err == 0:
            verdict = "recommend_promote"
            reason = (
                "Used successfully in this project with no recorded errors. "
                "Promote if the logic is reusable beyond this automation."
            )
        elif ok >= 1 and err > 0:
            verdict = "stabilize_first"
            reason = "Used with mixed results — fix errors before promoting to global."
        elif ok == 0 and err > 0:
            verdict = "do_not_promote"
            reason = "Only failed invocations recorded."
        else:
            verdict = "optional"
            reason = (
                "Defined for this project but not used successfully yet. "
                "Promote only if you expect other projects to need it."
            )
        recommendations.append(
            {
                **item,
                "verdict": verdict,
                "reason": reason,
                "promote_command": (
                    f"roi-h rpa promote --skill {item['skill']} --tool {item['tool']}"
                ),
            }
        )

    return {
        "ok": True,
        "project_root": str(catalog.project_root) if catalog.project_root else None,
        "global_root": str(catalog.global_root),
        "project_tool_count": len(catalog.project_tools()),
        "recommendations": recommendations,
        "guidance": (
            "Project-local tools stay under the project skills root. "
            "Promote to user-shared skills only when the human agrees the capability "
            "is reusable across future automations — not one-off job glue."
        ),
    }


def promote_to_global(
    *,
    skill: str,
    tool: str | None = None,
    project_root: str | Path,
    global_root: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Copy a project skill into the user-owned shared skills catalog."""
    skill_name = validate_skill_token(skill, kind="skill")
    tool_id = validate_skill_token(tool, kind="tool") if tool else None
    project = Path(project_root).expanduser().resolve()
    shared_skills = (
        Path(global_root).expanduser().resolve()
        if global_root is not None
        else (resolve_home() / "skills").resolve()
    )
    if shared_skills == default_skills_root().resolve():
        msg = "packaged core skills are immutable; promote into the user-shared skills root"
        raise ValueError(msg)
    src_skill = project / skill_name
    if not (src_skill / "SKILL.md").is_file():
        msg = f"project skill not found: {src_skill}"
        raise FileNotFoundError(msg)

    dest_skill = shared_skills / skill_name
    dest_skill.mkdir(parents=True, exist_ok=True)
    (dest_skill / "scripts").mkdir(exist_ok=True)

    copied: list[str] = []
    if tool_id is None:
        for script in sorted((src_skill / "scripts").glob("*.py")):
            dest = dest_skill / "scripts" / script.name
            if dest.exists() and not overwrite:
                msg = f"global script exists: {dest} (pass overwrite to replace)"
                raise FileExistsError(msg)
            shutil.copy2(script, dest)
            copied.append(str(dest))
        dest_md = dest_skill / "SKILL.md"
        if not dest_md.exists() or overwrite:
            shutil.copy2(src_skill / "SKILL.md", dest_md)
            copied.append(str(dest_md))
    else:
        src_script = src_skill / "scripts" / f"{tool_id}.py"
        if not src_script.is_file():
            msg = f"project tool script not found: {src_script}"
            raise FileNotFoundError(msg)
        dest = dest_skill / "scripts" / f"{tool_id}.py"
        if dest.exists() and not overwrite:
            msg = f"global script exists: {dest} (pass overwrite to replace)"
            raise FileExistsError(msg)
        shutil.copy2(src_script, dest)
        copied.append(str(dest))
        _ensure_skill_md(
            dest_skill,
            skill_name=skill_name,
            description=f"Promoted from project skill {skill_name}",
        )
        _ensure_tool_row_in_skill_md(
            dest_skill / "SKILL.md",
            tool_name=f"{skill_name}.{tool_id}",
            script_rel=f"scripts/{tool_id}.py",
            description=tool_id,
        )

    return {
        "ok": True,
        "skill": skill_name,
        "tool": tool_id,
        "global_root": str(shared_skills),
        "shared_root": str(shared_skills),
        "project_root": str(project),
        "copied": copied,
        "name": f"{skill_name}.{tool_id}" if tool_id else skill_name,
    }


def _ensure_skill_md(skill_dir: Path, *, skill_name: str, description: str) -> None:
    path = skill_dir / "SKILL.md"
    if path.exists():
        return
    body = f"""---
name: {skill_name}
description: {description or skill_name}
version: 0.1.0
scope: project
---

# {skill_name}

Project-local skill. Prefer global catalog tools when they fit.

## Tools

| Tool | Script | Notes |
|---|---|---|
"""
    path.write_text(body, encoding="utf-8")


def _ensure_tool_row_in_skill_md(
    path: Path,
    *,
    tool_name: str,
    script_rel: str,
    description: str,
) -> None:
    text = path.read_text(encoding="utf-8")
    if tool_name in text:
        return
    row = f"| `{tool_name}` | `{script_rel}` | {description} |\n"
    if "## Tools" in text:
        # Append after header row block if present; else at end of tools section.
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        inserted = False
        for index, line in enumerate(lines):
            out.append(line)
            if not inserted and line.startswith("|---"):
                # after separator of tools table
                out.append(row)
                inserted = True
            elif not inserted and line.startswith("## ") and index > 0 and "Tools" not in line:
                out.insert(-1, row)
                inserted = True
        if not inserted:
            out.append(row)
        path.write_text("".join(out), encoding="utf-8")
    else:
        path.write_text(text.rstrip() + "\n\n## Tools\n\n" + row, encoding="utf-8")


def _scope_from_catalog(catalog: SkillCatalog, name: str) -> str:
    try:
        return catalog.get(name).scope
    except KeyError:
        return "unknown"


def _description(catalog: SkillCatalog, name: str) -> str:
    try:
        return catalog.get(name).description
    except KeyError:
        return ""


def _script_path(catalog: SkillCatalog, name: str) -> str | None:
    try:
        return str(catalog.get(name).script_path)
    except KeyError:
        return None
