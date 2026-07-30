"""Versioned automation packages, recipes, and dev → prod push."""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roi_h.harness.atomicfs import (
    atomic_write_json,
    package_digest,
    verify_package,
)
from roi_h.harness.domain import PhasePlanEntry, Recipe, parse_phase_plan
from roi_h.harness.loader import default_skills_root, load_skills
from roi_h.harness.logical_paths import detect_physical_paths
from roi_h.harness.recipe import (
    load_recipe,
    recipe_from_dict,
    recipe_to_dict,
    validate_recipe,
    write_recipe,
)
from roi_h.harness.workspace import Workspace


def publish_manifest(
    workspace: Workspace,
    *,
    name: str,
    version: str,
    goal: str = "",
    skills: list[str] | None = None,
    budgets: dict[str, Any] | None = None,
    notes: str = "",
    phases: list[str] | list[dict[str, Any]] | None = None,
    recipe: Recipe | dict[str, Any] | None = None,
    recipe_path: str | Path | None = None,
    distill_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish one immutable, content-addressed automation package."""
    _validate_name(name)
    _validate_version(version)
    name_dir = workspace.automations / name
    name_dir.mkdir(parents=True, exist_ok=True)
    auto_dir = name_dir / version
    existing_manifest: dict[str, Any] | None = None
    if auto_dir.exists():
        manifest_path = auto_dir / "manifest.json"
        if not manifest_path.is_file():
            msg = f"automation version directory is incomplete: {auto_dir}"
            raise FileExistsError(msg)
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        verify_package(auto_dir, existing_manifest)
    staging = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=name_dir))
    try:
        skills_snapshot = staging / "skills"
        skills_snapshot.mkdir(parents=True)
        included = (
            list(skills) if skills is not None else _list_skill_names(workspace.project_skills)
        )
        copied: list[str] = []
        for skill in included:
            src = workspace.project_skills / skill
            if not src.is_dir():
                msg = f"project skill not found for snapshot: {src}"
                raise FileNotFoundError(msg)
            dest = skills_snapshot / skill
            shutil.copytree(src, dest)
            copied.append(skill)

        resolved_recipe = _resolve_recipe(
            recipe=recipe,
            recipe_path=recipe_path,
            name=name,
            version=version,
            goal=goal,
            budgets=budgets,
            notes=notes,
            phases=phases,
        )
        referenced_tools: set[tuple[str, str]] = set()
        if resolved_recipe is not None:
            referenced_tools = {
                (str(step.skill), str(step.tool))
                for phase in resolved_recipe.phases
                for step in phase.steps
                if step.action == "invoke" and step.skill and step.tool
            }
            global_skills = default_skills_root()
            shared_skills = workspace.shared_skills
            for skill in sorted({skill for skill, _tool in referenced_tools}):
                if skill in copied:
                    continue
                source = workspace.project_skills / skill
                if not source.is_dir():
                    source = shared_skills / skill
                if not source.is_dir():
                    source = global_skills / skill
                if not source.is_dir():
                    msg = f"referenced skill not found for snapshot: {skill}"
                    raise FileNotFoundError(msg)
                shutil.copytree(source, skills_snapshot / skill)
                copied.append(skill)
        package_catalog = load_skills(
            default_skills_root(),
            project_root=skills_snapshot,
            database=workspace.db,
        )
        execution_policy = []
        for skill, tool in sorted(referenced_tools):
            item = package_catalog.resolve(skill, tool)
            if workspace.env == "dev" and not item.allow_in_prod:
                msg = (
                    f"automation references production-denied tool {item.name}; "
                    "publish an explicitly allowed adapter instead"
                )
                raise ValueError(msg)
            execution_policy.append(
                {
                    "name": item.name,
                    "effect": item.effect,
                    "idempotency": item.idempotency,
                    "requires_approval": item.requires_approval,
                    "allow_in_prod": item.allow_in_prod,
                    "timeout_seconds": item.timeout_seconds,
                    "secret_names": list(item.secret_names),
                    "network_hosts": list(item.network_hosts),
                    "filesystem_roots": list(item.filesystem_roots),
                }
            )
        phase_plan = (
            [entry.model_dump(mode="json") for entry in resolved_recipe.phase_plan_entries()]
            if resolved_recipe is not None
            else _normalize_phase_plan(phases)
        )

        recipe_file: str | None = None
        distill_file: str | None = None
        if resolved_recipe is not None:
            issues = validate_recipe(resolved_recipe)
            if issues:
                msg = "invalid recipe: " + "; ".join(issues)
                raise ValueError(msg)
            physical_paths = detect_physical_paths(resolved_recipe.model_dump(mode="json"))
            if physical_paths:
                msg = (
                    "package.not_portable: recipe contains machine-specific paths; "
                    f"first offending value: {physical_paths[0]!r}"
                )
                raise ValueError(msg)
            resolved_recipe = resolved_recipe.model_copy(
                update={
                    "name": name,
                    "version": version,
                    "goal": goal or resolved_recipe.goal,
                    "budgets": budgets or resolved_recipe.budgets,
                    "notes": notes or resolved_recipe.notes,
                }
            )
            write_recipe(staging / "recipe.json", resolved_recipe)
            recipe_file = str(auto_dir / "recipe.json")
            phase_plan = [
                entry.model_dump(mode="json") for entry in resolved_recipe.phase_plan_entries()
            ]
            if distill_report is not None:
                atomic_write_json(staging / "distill.json", distill_report)
                distill_file = str(auto_dir / "distill.json")

        manifest = {
            "schema_version": 1,
            "name": name,
            "version": version,
            "project_id": workspace.project_id,
            "goal": goal or (resolved_recipe.goal if resolved_recipe else ""),
            "source_environment": workspace.env,
            "created_at": (
                existing_manifest.get("created_at")
                if existing_manifest is not None
                else datetime.now(UTC).isoformat()
            ),
            "skills": copied,
            "phases": phase_plan,
            "budgets": budgets or (dict(resolved_recipe.budgets) if resolved_recipe else {}),
            "notes": notes or (resolved_recipe.notes if resolved_recipe else ""),
            "require_approval_in_prod": False,
            "has_recipe": resolved_recipe is not None,
            "recipe_file": "recipe.json" if resolved_recipe is not None else None,
            "distill_file": "distill.json" if distill_file else None,
            "source_run_id": resolved_recipe.source_run_id if resolved_recipe else None,
            "execution_policy": execution_policy,
        }
        manifest["package_digest"] = package_digest(staging, manifest)
        atomic_write_json(staging / "manifest.json", manifest)

        if existing_manifest is not None:
            existing_digest = str(existing_manifest["package_digest"])
            if existing_digest != manifest["package_digest"]:
                msg = (
                    f"automation version is immutable: {name}@{version} already exists "
                    "with different content"
                )
                raise FileExistsError(msg)
        else:
            staging.replace(auto_dir)
        _write_channel(workspace, name, version, manifest)
        return {
            "ok": True,
            "manifest": manifest,
            "path": str(auto_dir / "manifest.json"),
            "package_digest": manifest["package_digest"],
            "skills_copied": copied,
            "recipe_path": recipe_file,
            "distill_path": distill_file,
            "has_recipe": resolved_recipe is not None,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def push_to_prod(
    *,
    root: str | Path | None = None,
    project: str | None = None,
    name: str,
    version: str | None = None,
    overwrite_skills: bool = True,
) -> dict[str, Any]:
    """Promote an immutable package by atomically changing the prod channel."""
    del overwrite_skills
    dev = Workspace.open(root, project=project, env="dev")
    prod = Workspace.open(root, project=project, env="prod")
    _validate_name(name)
    ver = version or _read_channel(dev, name) or _read_latest(dev.automations / name)
    if ver is None:
        msg = f"no published version for automation {name!r} in dev (run publish first)"
        raise FileNotFoundError(msg)
    src = dev.automations / name / ver
    manifest_path = src / "manifest.json"
    if not manifest_path.is_file():
        msg = f"dev automation version not found: {src}"
        raise FileNotFoundError(msg)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = verify_package(src, manifest)

    _write_channel(prod, name, ver, manifest)

    push_record = {
        "name": name,
        "version": ver,
        "project": dev.project,
        "pushed_at": datetime.now(UTC).isoformat(),
        "from_env": "dev",
        "to_env": "prod",
        "package_digest": digest,
        "has_recipe": (src / "recipe.json").is_file(),
    }
    return {
        "ok": True,
        **push_record,
        "prod_db": str(prod.db),
        "channel_path": str(prod.channels / f"{name}.json"),
        "recipe_path": str(src / "recipe.json") if (src / "recipe.json").is_file() else None,
    }


def load_automation(
    workspace: Workspace,
    name: str,
    *,
    version: str | None = None,
) -> dict[str, Any]:
    """Load a published automation package (manifest + optional recipe + skills dir)."""
    _validate_name(name)
    ver = version or _read_channel(workspace, name) or _read_latest(workspace.automations / name)
    if ver is None:
        msg = f"no automation {name!r} in env {workspace.env!r}"
        raise FileNotFoundError(msg)
    auto_dir = workspace.automations / name / ver
    manifest_path = auto_dir / "manifest.json"
    if not manifest_path.is_file():
        msg = f"automation version not found: {auto_dir}"
        raise FileNotFoundError(msg)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = verify_package(auto_dir, manifest)
    recipe: Recipe | None = None
    recipe_path = auto_dir / "recipe.json"
    if recipe_path.is_file():
        recipe = load_recipe(recipe_path)
    return {
        "ok": True,
        "name": name,
        "version": ver,
        "path": str(auto_dir),
        "manifest": manifest,
        "package_digest": digest,
        "recipe": recipe_to_dict(recipe) if recipe else None,
        "recipe_obj": recipe,
        "skills_dir": str(auto_dir / "skills"),
        "has_recipe": recipe is not None,
    }


def list_automations(workspace: Workspace) -> list[dict[str, Any]]:
    """List automation names and versions available in this environment."""
    items: list[dict[str, Any]] = []
    if not workspace.automations.is_dir():
        return items
    for name_dir in sorted(p for p in workspace.automations.iterdir() if p.is_dir()):
        latest = _read_channel(workspace, name_dir.name) or _read_latest(name_dir)
        versions = sorted(
            p.name for p in name_dir.iterdir() if p.is_dir() and (p / "manifest.json").is_file()
        )
        has_recipe = False
        if latest:
            has_recipe = (name_dir / latest / "recipe.json").is_file()
        items.append(
            {
                "name": name_dir.name,
                "latest": latest,
                "versions": versions,
                "has_recipe": has_recipe,
            }
        )
    return items


def _read_channel(workspace: Workspace, name: str) -> str | None:
    path = workspace.channels / f"{name}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return str(raw.get("version") or "") or None


def _write_channel(
    workspace: Workspace,
    name: str,
    version: str,
    manifest: dict[str, Any],
) -> None:
    workspace.channels.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        workspace.channels / f"{name}.json",
        {
            "schema_version": 1,
            "name": name,
            "version": version,
            "package_digest": manifest["package_digest"],
            "promoted_at": datetime.now(UTC).isoformat(),
            "source_run_id": manifest.get("source_run_id"),
        },
        mode=0o600,
    )


def _resolve_recipe(
    *,
    recipe: Recipe | dict[str, Any] | None,
    recipe_path: str | Path | None,
    name: str,
    version: str,
    goal: str,
    budgets: dict[str, Any] | None,
    notes: str,
    phases: list[str] | list[dict[str, Any]] | None,
) -> Recipe | None:
    if recipe is not None:
        if isinstance(recipe, Recipe):
            return recipe
        return recipe_from_dict(recipe)
    if recipe_path is not None:
        return load_recipe(recipe_path)
    # Recipe is optional unless explicitly provided via recipe/recipe_path.
    del name, version, goal, budgets, notes, phases
    return None


def _list_skill_names(project_skills: Path) -> list[str]:
    if not project_skills.is_dir():
        return []
    return sorted(
        p.name for p in project_skills.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
    )


def _read_latest(name_dir: Path) -> str | None:
    latest = name_dir / "LATEST"
    if not latest.is_file():
        return None
    return latest.read_text(encoding="utf-8").strip() or None


def _validate_name(name: str) -> None:
    if not name or any(ch in name for ch in "/\\") or name in {".", ".."}:
        msg = f"invalid automation name: {name!r}"
        raise ValueError(msg)


def _validate_version(version: str) -> None:
    if not version or any(ch in version for ch in "/\\") or version in {".", ".."}:
        msg = f"invalid version: {version!r}"
        raise ValueError(msg)


def _normalize_phase_plan(
    phases: list[str] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not phases:
        return []
    result: list[dict[str, Any]] = []
    for item in phases:
        if isinstance(item, str):
            result.extend(entry.model_dump(mode="json") for entry in parse_phase_plan([item]))
        else:
            result.append(PhasePlanEntry.model_validate(item).model_dump(mode="json"))
    return result
