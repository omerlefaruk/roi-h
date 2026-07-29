"""Deep operator journeys: ship and run automations.

CLI (and scripts) call these; argv parsing stays in the adapter.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from activegraph.store.sqlite import SQLiteEventStore

from roi_h.harness.application import RunSession
from roi_h.harness.automation import load_automation, publish_manifest, push_to_prod
from roi_h.harness.distill import build_recipe_from_run
from roi_h.harness.domain import BudgetSpec, Recipe
from roi_h.harness.lease import run_lease
from roi_h.harness.recipe_lang import apply_recipe_overrides
from roi_h.harness.runner import run_recipe
from roi_h.harness.workspace import Workspace

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_run_id(run_id: str) -> None:
    if not _RUN_ID_RE.match(run_id):
        msg = (
            "run id must be 1-128 chars, start with alphanumeric, "
            "and contain only letters, digits, '.', '_' or '-'"
        )
        raise ValueError(msg)


def new_run_id(goal: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", goal.strip().lower())[:40].strip("-") or "run"
    return f"{slug}-{uuid.uuid4().hex[:8]}"


def recipe_from_run(
    session: RunSession,
    *,
    name: str,
    version: str,
    goal: str = "",
    notes: str = "",
    distill: bool = True,
) -> tuple[Recipe, dict[str, Any]]:
    """Distill one durable run into a linear automation recipe."""
    run = next(iter(session.runtime.graph.objects(type="rpa.run")), None)
    run_goal = str(run.data.get("goal") or "") if run is not None else ""
    phases = session.list_phases()
    steps = [
        {"id": item.id, **dict(item.data)}
        for item in session.runtime.graph.objects(type="rpa.step")
    ]
    artifacts = [
        {"id": item.id, **dict(item.data)}
        for item in session.runtime.graph.objects(type="rpa.artifact")
    ]
    return build_recipe_from_run(
        name=name,
        version=version,
        goal=goal or run_goal,
        phase_objects=phases,
        step_objects=steps,
        artifact_objects=artifacts,
        budgets=session.budget.model_dump(mode="json", exclude_none=True),
        source_run_id=session.runtime.run_id,
        notes=notes,
        only_ok=True,
        distill=distill,
    )


def publish_from_run(
    workspace: Workspace,
    *,
    name: str,
    version: str,
    from_run: str,
    goal: str = "",
    notes: str = "",
    skills: list[str] | None = None,
    budgets: dict[str, Any] | None = None,
    phases: list[str] | list[dict[str, Any]] | None = None,
    skills_root: str | Path | None = None,
    budget: BudgetSpec | None = None,
    distill: bool = True,
    recipe_path: str | Path | None = None,
) -> dict[str, Any]:
    """Export recipe from a run (optional) and publish an automation package."""
    if from_run and recipe_path:
        msg = "pass only one of from_run or recipe_path"
        raise ValueError(msg)
    distill_report: dict[str, Any] | None = None
    recipe: Recipe | None = None
    if from_run:
        validate_run_id(from_run)
        harness = RunSession.reopen(
            workspace,
            run_id=from_run,
            skills_root=skills_root,
            budget=budget,
            auto_approve=None,
        )
        recipe, distill_report = recipe_from_run(
            harness,
            name=name,
            version=version,
            goal=goal,
            notes=notes,
            distill=distill,
        )
    result = publish_manifest(
        workspace,
        name=name,
        version=version,
        goal=goal,
        skills=skills,
        budgets=budgets,
        notes=notes,
        phases=phases,
        recipe=recipe,
        recipe_path=recipe_path,
        distill_report=distill_report,
    )
    if distill_report is not None:
        result["distill"] = distill_report
    return result


def ship_automation(
    workspace: Workspace,
    *,
    name: str,
    version: str,
    from_run: str,
    goal: str = "",
    notes: str = "",
    skills: list[str] | None = None,
    budgets: dict[str, Any] | None = None,
    skills_root: str | Path | None = None,
    budget: BudgetSpec | None = None,
    distill: bool = True,
    prod_dry_run: bool = False,
    prod_run: bool = False,
    set_args: list[str] | None = None,
) -> dict[str, Any]:
    """Publish --from-run (distill) → push → optional prod dry-run/run."""
    if workspace.env != "dev":
        msg = "ship must use env=dev"
        raise ValueError(msg)
    validate_run_id(from_run)

    published = publish_from_run(
        workspace,
        name=name,
        version=version,
        from_run=from_run,
        goal=goal,
        notes=notes,
        skills=skills,
        budgets=budgets,
        skills_root=skills_root,
        budget=budget,
        distill=distill,
    )
    pushed = push_to_prod(
        root=workspace.root,
        project=workspace.project,
        name=name,
        version=version,
    )
    result: dict[str, Any] = {
        "ok": True,
        "shipped": True,
        "name": name,
        "version": version,
        "project": workspace.project,
        "publish": published,
        "push": pushed,
        "distill": published.get("distill"),
        "prod_dry_run": None,
        "prod_run": None,
    }
    if prod_dry_run or prod_run:
        prod_ws = Workspace.open(workspace.root, project=workspace.project, env="prod")
        run_result = run_automation(
            prod_ws,
            name=name,
            version=version,
            skills_root=skills_root,
            budget=budget,
            dry_run=not prod_run,
            auto_approve=True,
            force=True,
            actor="ship",
            set_args=set_args,
            collect_feedback=True,
        )
        if prod_run:
            result["prod_run"] = run_result
        else:
            result["prod_dry_run"] = run_result
    result["next"] = {
        "run": f"roi-h rpa env set prod && roi-h rpa run {name}",
        "headed": f"roi-h rpa run {name} --set headless=false",
    }
    return result


def run_automation(
    workspace: Workspace,
    *,
    name: str,
    version: str | None = None,
    run_id: str | None = None,
    skills_root: str | Path | None = None,
    budget: BudgetSpec | None = None,
    dry_run: bool = False,
    from_handoff: str | Path | None = None,
    auto_approve: bool | None = None,
    force: bool = True,
    actor: str = "recipe",
    set_args: list[str] | None = None,
    collect_feedback: bool = True,
) -> dict[str, Any]:
    """Execute one immutable automation package under an exclusive run lease."""
    rid = run_id or new_run_id(name)
    validate_run_id(rid)
    with run_lease(workspace, rid):
        return _run_automation(
            workspace,
            name=name,
            version=version,
            run_id=rid,
            skills_root=skills_root,
            budget=budget,
            dry_run=dry_run,
            from_handoff=from_handoff,
            auto_approve=auto_approve,
            force=force,
            actor=actor,
            set_args=set_args,
            collect_feedback=collect_feedback,
        )


def _run_automation(
    workspace: Workspace,
    *,
    name: str,
    version: str | None = None,
    run_id: str | None = None,
    skills_root: str | Path | None = None,
    budget: BudgetSpec | None = None,
    dry_run: bool = False,
    from_handoff: str | Path | None = None,
    auto_approve: bool | None = None,
    force: bool = True,
    actor: str = "recipe",
    set_args: list[str] | None = None,
    collect_feedback: bool = True,
) -> dict[str, Any]:
    """Load a published automation and execute its recipe on a fresh run."""
    package = load_automation(workspace, name, version=version)
    recipe_obj = package.get("recipe_obj")
    if recipe_obj is None:
        msg = (
            f"automation {name!r} v{package['version']} has no recipe.json — "
            "publish with --from-run or --recipe first"
        )
        raise FileNotFoundError(msg)
    recipe: Recipe = recipe_obj
    if set_args:
        recipe = apply_recipe_overrides(recipe, set_args=set_args)

    cli_budget = budget or BudgetSpec()
    budget_dict = dict(recipe.budgets or {})
    for key in ("max_events", "max_tool_calls", "max_seconds"):
        val = getattr(cli_budget, key, None)
        if val is not None:
            budget_dict[key] = val
    resolved_budget = BudgetSpec(
        max_events=budget_dict.get("max_events"),
        max_tool_calls=budget_dict.get("max_tool_calls"),
        max_seconds=budget_dict.get("max_seconds"),
    )

    rid = run_id or new_run_id(recipe.goal or recipe.name)
    validate_run_id(rid)

    skills_dir = Path(package["skills_dir"])
    project_skills = skills_dir if skills_dir.is_dir() else None

    existing = workspace.db.is_file() and any(
        item.run_id == rid for item in SQLiteEventStore.list_runs(str(workspace.db))
    )
    if existing:
        # A command can be interrupted while a browser download is in flight.
        # Reopen and continue its durable run instead of creating duplicate
        # event ids and losing the completed phases.
        harness = RunSession.reopen(
            workspace,
            run_id=rid,
            skills_root=skills_root,
            project_skills=project_skills,
            budget=resolved_budget,
            auto_approve=auto_approve,
        )
    else:
        harness = RunSession.create(
            workspace,
            run_id=rid,
            skills_root=skills_root,
            project_skills=project_skills,
            budget=resolved_budget,
            auto_approve=auto_approve,
        )
        goal = recipe.goal or f"run {recipe.name}@{recipe.version}"
        harness.start_run(
            goal,
            actor=actor,
            phase_plan=recipe.phase_plan_entries(),
            automation_name=str(package["name"]),
            automation_version=str(package["version"]),
            package_digest=str(package["package_digest"]),
        )

    skip_phases: set[str] = set()
    seeded = None
    if from_handoff:
        seeded = harness.seed_from_handoff(from_handoff)
        for item in seeded.get("seeded_phases") or []:
            pname = item.get("name")
            if pname:
                skip_phases.add(str(pname))

    result = run_recipe(
        harness,
        recipe,
        dry_run=dry_run,
        skip_phases=skip_phases,
        force=force,
        actor=actor,
        collect_feedback=collect_feedback,
    )
    result["automation"] = {
        "name": package["name"],
        "version": package["version"],
        "path": package["path"],
        "skills_dir": package["skills_dir"],
        "package_digest": package["package_digest"],
    }
    if seeded is not None:
        result["seeded"] = seeded
    result["next"] = {
        "status": f"roi-h rpa status --run-id {rid}",
        "feedback": "roi-h rpa invoke --run-id RUN feedback list --args '{}'",
    }
    return result


__all__ = [
    "new_run_id",
    "publish_from_run",
    "recipe_from_run",
    "run_automation",
    "ship_automation",
    "validate_run_id",
]
