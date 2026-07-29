"""Distill a successful run into a prod recipe (drop explore noise)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from roi_h.harness.domain import (
    PhaseRole,
    Recipe,
    RecipePhase,
    RecipeStep,
    infer_phase_role,
)

_EXPORTABLE_PHASE_STATUSES = frozenset({"done", "open"})

# Generic discovery actuators: dropped once the run has successful project tools.
_DISCOVERY_SKILLS = frozenset({"browser"})
# Tool output keys that look like file paths worth auto-artifacting.
_PATH_OUTPUT_KEYS = (
    "path",
    "excel_path",
    "file",
    "filepath",
    "output_path",
    "screenshot_path",
    "download_path",
    "artifact_path",
)


def export_recipe_from_run(
    *,
    name: str,
    version: str,
    goal: str,
    phase_objects: list[dict[str, Any]],
    step_objects: list[dict[str, Any]],
    artifact_objects: list[dict[str, Any]] | None = None,
    budgets: dict[str, Any] | None = None,
    source_run_id: str | None = None,
    notes: str = "",
    only_ok: bool = True,
    distill: bool = True,
) -> Recipe:
    """Build a prod-ready recipe from a run (distills explore noise by default).

    Default ``distill=True`` (always on for publish/export unless disabled):

    1. only successful steps (``status=ok``)
    2. drop exploration phases (``explore``, ``probe``, ``debug``, …)
    3. if the run used project-local tools successfully, drop generic
       discovery skills (``browser.*``) — those were for AI reconnaissance
    4. drop empty phases
    5. if nothing remains, fall back to the full successful transcript

    Pass ``distill=False`` only when you need a forensic full-run transcript.
    """
    recipe, _report = build_recipe_from_run(
        name=name,
        version=version,
        goal=goal,
        phase_objects=phase_objects,
        step_objects=step_objects,
        artifact_objects=artifact_objects,
        budgets=budgets,
        source_run_id=source_run_id,
        notes=notes,
        only_ok=only_ok,
        distill=distill,
    )
    return recipe


def build_recipe_from_run(
    *,
    name: str,
    version: str,
    goal: str,
    phase_objects: list[dict[str, Any]],
    step_objects: list[dict[str, Any]],
    artifact_objects: list[dict[str, Any]] | None = None,
    budgets: dict[str, Any] | None = None,
    source_run_id: str | None = None,
    notes: str = "",
    only_ok: bool = True,
    distill: bool = True,
) -> tuple[Recipe, dict[str, Any]]:
    """Like ``export_recipe_from_run`` but also returns a distill report for operators."""
    ordered = _select_export_phases(phase_objects, only_ok=only_ok)
    if not ordered:
        msg = "cannot export recipe: no exportable phases found on run"
        raise ValueError(msg)

    if distill:
        phases, report = _distill_phases(
            ordered,
            step_objects,
            only_ok=only_ok,
            auto_artifacts=True,
        )
        if not phases or sum(len(p.steps) for p in phases) == 0:
            phases = [
                _phase_to_recipe(
                    phase,
                    step_objects,
                    only_ok=only_ok,
                    keep_step=lambda _s: True,
                    auto_artifacts=True,
                )
                for phase in ordered
            ]
            phases = [p for p in phases if p.steps or p.role == "verify"]
            report = {
                **report,
                "mode": "full_fallback",
                "fallback": True,
                "fallback_reason": "distill removed every step; kept successful transcript",
                "kept_phases": [p.name for p in phases],
                "kept_steps": sum(len(p.steps) for p in phases),
            }
        else:
            report = {
                **report,
                "mode": "prod",
                "fallback": False,
                "kept_phases": [p.name for p in phases],
                "kept_steps": sum(len(p.steps) for p in phases),
            }
    else:
        phases = [
            _phase_to_recipe(
                phase,
                step_objects,
                only_ok=only_ok,
                keep_step=lambda _s: True,
                auto_artifacts=True,
            )
            for phase in ordered
        ]
        phases = [p for p in phases if p.steps or p.role == "verify"]
        report = {
            "mode": "full",
            "fallback": False,
            "dropped_phases": [],
            "dropped_steps": [],
            "kept_phases": [p.name for p in phases],
            "kept_steps": sum(len(p.steps) for p in phases),
            "auto_artifacts": [],
        }

    if not phases:
        msg = "cannot export recipe: no successful steps found on run"
        raise ValueError(msg)

    # Soft-verify: empty verify phases with only require_artifacts from plan stay
    # without forced fake steps.
    del artifact_objects

    note_bits = [notes.strip()] if notes.strip() else []
    note_bits.append(_format_distill_note(report))
    merged_notes = "\n".join(note_bits)

    recipe = Recipe(
        name=name,
        version=version,
        goal=goal,
        phases=phases,
        budgets=dict(budgets or {}),
        source_run_id=source_run_id,
        notes=merged_notes,
    )
    return recipe, report


def _format_distill_note(report: dict[str, Any]) -> str:
    mode = report.get("mode")
    kept = report.get("kept_steps", 0)
    dropped_p = len(report.get("dropped_phases") or [])
    dropped_s = len(report.get("dropped_steps") or [])
    if mode == "full":
        return "export mode=full (no distill)"
    if mode == "full_fallback":
        return (
            f"export mode=full_fallback: distill removed all steps; "
            f"kept successful transcript ({kept} steps)"
        )
    return (
        f"export mode=prod: kept {kept} steps in phases {report.get('kept_phases')}; "
        f"dropped {dropped_p} phases, {dropped_s} discovery/explore steps"
    )


def _phase_role(phase: dict[str, Any]) -> PhaseRole:
    name = str(phase.get("name") or "")
    raw = phase.get("role")
    explicit: PhaseRole | None = raw if raw in {"explore", "work", "verify"} else None
    return infer_phase_role(name, explicit)


def _distill_phases(
    ordered_phases: list[dict[str, Any]],
    step_objects: list[dict[str, Any]],
    *,
    only_ok: bool,
    auto_artifacts: bool = True,
) -> tuple[list[RecipePhase], dict[str, Any]]:
    dropped_phases: list[dict[str, Any]] = []
    dropped_steps: list[dict[str, Any]] = []
    auto_artifact_steps: list[dict[str, Any]] = []

    has_project = any(
        step.get("status") == "ok" and str(step.get("scope") or "") == "project"
        for step in step_objects
    )

    out: list[RecipePhase] = []
    for phase in ordered_phases:
        phase_name = str(phase.get("name") or "")
        role = _phase_role(phase)
        if role == "explore":
            n_steps = len(_steps_for_phase(phase, step_objects, only_ok=only_ok))
            dropped_phases.append(
                {
                    "name": phase_name,
                    "role": role,
                    "reason": "exploration_phase",
                    "steps": n_steps,
                }
            )
            continue

        def _keep(step: dict[str, Any], *, _has_project: bool = has_project) -> bool:
            skill = str(step.get("skill") or "")
            scope = str(step.get("scope") or "")
            return not (_has_project and skill in _DISCOVERY_SKILLS and scope != "project")

        dropped_steps.extend(
            {
                "phase": phase_name,
                "skill": step.get("skill"),
                "tool": step.get("tool"),
                "reason": "discovery_tool_superseded_by_project_skill",
            }
            for step in _steps_for_phase(phase, step_objects, only_ok=only_ok)
            if not _keep(step)
        )

        recipe_phase = _phase_to_recipe(
            phase,
            step_objects,
            only_ok=only_ok,
            keep_step=_keep,
            auto_artifacts=auto_artifacts,
            auto_artifact_log=auto_artifact_steps,
        )
        if not recipe_phase.steps:
            # Soft verify: no forced artifacts; empty phases (incl. verify) stay out of prod.
            dropped_phases.append(
                {
                    "name": phase_name,
                    "role": role,
                    "reason": "empty_after_distill",
                    "steps": 0,
                }
            )
            continue
        out.append(recipe_phase)

    return out, {
        "dropped_phases": dropped_phases,
        "dropped_steps": dropped_steps,
        "prefer_project_tools": has_project,
        "auto_artifacts": auto_artifact_steps,
    }


def _select_export_phases(
    phase_objects: list[dict[str, Any]],
    *,
    only_ok: bool,
) -> list[dict[str, Any]]:
    phases_sorted = sorted(
        phase_objects,
        key=lambda item: (int(item.get("index") or 0), str(item.get("phase_id") or "")),
    )
    by_name: dict[str, dict[str, Any]] = {}
    for phase in phases_sorted:
        status = phase.get("status")
        if status == "skipped":
            continue
        if only_ok and status not in _EXPORTABLE_PHASE_STATUSES:
            continue
        name_key = str(phase.get("name") or "")
        if not name_key:
            continue
        prev = by_name.get(name_key)
        if prev is None or int(phase.get("index") or 0) >= int(prev.get("index") or 0):
            by_name[name_key] = phase
    return sorted(by_name.values(), key=lambda item: int(item.get("index") or 0))


def _steps_for_phase(
    phase: dict[str, Any],
    step_objects: list[dict[str, Any]],
    *,
    only_ok: bool,
) -> list[dict[str, Any]]:
    phase_id = phase.get("phase_id") or phase.get("id")
    phase_name = str(phase.get("name") or "")
    out: list[dict[str, Any]] = []
    for step in step_objects:
        if not (
            step.get("phase_id") == phase_id
            or (step.get("phase") == phase_name and not step.get("phase_id"))
        ):
            continue
        status = step.get("status")
        if status == "pending_approval":
            continue
        if only_ok and status != "ok":
            continue
        out.append(step)
    return out


def _phase_to_recipe(
    phase: dict[str, Any],
    step_objects: list[dict[str, Any]],
    *,
    only_ok: bool,
    keep_step: Callable[[dict[str, Any]], bool] | None = None,
    auto_artifacts: bool = False,
    auto_artifact_log: list[dict[str, Any]] | None = None,
) -> RecipePhase:
    phase_name = str(phase["name"])
    role = _phase_role(phase)
    keep = keep_step or (lambda _s: True)
    recipe_steps: list[RecipeStep] = []
    counter = 0
    for step in _steps_for_phase(phase, step_objects, only_ok=only_ok):
        if not keep(step):
            continue
        counter += 1
        skill = str(step.get("skill") or "")
        tool = str(step.get("tool") or "")
        step_id = f"{skill}_{tool}_{counter}".replace(".", "_")
        if not re.match(r"^[A-Za-z]", step_id):
            step_id = f"s{counter}_{step_id}"
        step_id = step_id[:64]
        recipe_steps.append(
            RecipeStep(
                id=step_id,
                action="invoke",
                skill=skill,
                tool=tool,
                args=dict(step.get("args") or {}),
            )
        )
        if auto_artifacts:
            for art in _auto_artifact_steps_for_output(step_id, step.get("output") or {}):
                recipe_steps.append(art)
                if auto_artifact_log is not None:
                    auto_artifact_log.append(
                        {
                            "phase": phase_name,
                            "from_step": step_id,
                            "artifact": art.name,
                            "source": art.source,
                        }
                    )
    require = list(phase.get("require_artifacts") or [])
    if role == "verify" and not recipe_steps:
        require = []  # soft verify — no forced artifacts
    return RecipePhase(
        name=phase_name,
        description=str(phase.get("description") or ""),
        role=role,
        require_artifacts=require,
        summary=dict(phase.get("summary") or {}),
        steps=recipe_steps,
    )


def _auto_artifact_steps_for_output(step_id: str, output: object) -> list[RecipeStep]:
    """Emit artifact put steps for path-like fields on a tool output."""
    if not isinstance(output, dict):
        return []
    steps: list[RecipeStep] = []
    seen: set[str] = set()
    for key in _PATH_OUTPUT_KEYS:
        if key not in output:
            continue
        raw = output.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        # Only auto-wire absolute or relative file-looking paths.
        looks_like_file = (
            raw.startswith("/")
            or raw.endswith((".csv", ".xlsx", ".xls", ".json", ".txt", ".png", ".pdf", ".html"))
            or ("/" in raw or "\\" in raw)
        )
        if not looks_like_file:
            continue
        name = Path(raw).name or f"{step_id}-{key}"
        if name in seen:
            continue
        seen.add(name)
        art_id = f"artifact_{step_id}_{key}"[:64]
        if not re.match(r"^[A-Za-z]", art_id):
            art_id = f"a_{art_id}"[:64]
        steps.append(
            RecipeStep(
                id=art_id,
                action="artifact",
                name=name,
                source=f"{{{{steps.{step_id}.output.{key}}}}}",
            )
        )
    return steps


__all__ = [
    "build_recipe_from_run",
    "export_recipe_from_run",
]
