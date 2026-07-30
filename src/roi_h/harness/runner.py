"""Deterministic recipe runner for unattended ``roi-h rpa run``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from roi_h.harness.application import RunSession
from roi_h.harness.control import cancellation_request
from roi_h.harness.domain import Recipe, RecipePhase, RecipeStep
from roi_h.harness.feedback_hook import record_run_feedback
from roi_h.harness.graph_access import patch_run, run_object
from roi_h.harness.recipe_lang import resolve_templates, validate_recipe
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.secrets import resolve_secret_refs


def plan_recipe(
    recipe: Recipe,
    *,
    skip_phases: set[str] | None = None,
) -> dict[str, Any]:
    """Return a dry-run plan without executing tools."""
    issues = validate_recipe(recipe)
    skip = skip_phases or set()
    phases_plan: list[dict[str, Any]] = []
    for phase in recipe.phases:
        if phase.name in skip:
            phases_plan.append(
                {
                    "name": phase.name,
                    "skipped": True,
                    "reason": "already completed (handoff/seed)",
                    "steps": [],
                }
            )
            continue
        steps_plan = [
            {
                "id": step.id,
                "action": step.action,
                "skill": step.skill,
                "tool": step.tool,
                "args": step.args,
                "name": step.name,
                "source": step.source,
            }
            for step in phase.steps
        ]
        phases_plan.append(
            {
                "name": phase.name,
                "skipped": False,
                "description": phase.description,
                "require_artifacts": list(phase.require_artifacts),
                "summary": dict(phase.summary),
                "steps": steps_plan,
            }
        )
    return {
        "ok": not issues,
        "dry_run": True,
        "name": recipe.name,
        "version": recipe.version,
        "goal": recipe.goal,
        "issues": issues,
        "phases": phases_plan,
        "phase_count": len(recipe.phases),
        "step_count": sum(len(p.steps) for p in recipe.phases if p.name not in skip),
    }


def run_recipe(
    harness: RunSession,
    recipe: Recipe,
    *,
    dry_run: bool = False,
    skip_phases: set[str] | None = None,
    force: bool = True,
    actor: str = "recipe",
    collect_feedback: bool = True,
) -> dict[str, Any]:
    """Execute a closed recipe on an open harness run.

    ``skip_phases``: phase names already completed (e.g. seeded from handoff).
    ``force``: skip approval gates (prod runner default).
    ``collect_feedback``: after a live run, record compact run feedback.
    """
    issues = validate_recipe(recipe)
    if issues:
        return {
            "ok": False,
            "error": "invalid recipe",
            "issues": issues,
            "run_id": harness.runtime.run_id,
        }

    skip = set(skip_phases or ())
    if dry_run:
        plan = plan_recipe(recipe, skip_phases=skip)
        plan["run_id"] = harness.runtime.run_id
        plan["env"] = harness.workspace.env
        return plan

    context = _initial_context(harness, recipe)
    _sync_artifacts(harness, context)

    executed: list[dict[str, Any]] = []
    phases_out: list[dict[str, Any]] = []

    for phase in recipe.phases:
        if phase.name in skip:
            phases_out.append(
                {
                    "name": phase.name,
                    "status": "skipped",
                    "reason": "seeded/handoff",
                }
            )
            continue

        phase_result = _run_phase(
            harness,
            phase,
            context=context,
            force=force,
            actor=actor,
            executed=executed,
        )
        phases_out.append(phase_result)
        if not phase_result.get("ok"):
            cancelled = _cancellation_requested(harness)
            _finish_run(harness, "cancelled" if cancelled else "failed")
            result = {
                "ok": False,
                "run_id": harness.runtime.run_id,
                "env": harness.workspace.env,
                "name": recipe.name,
                "version": recipe.version,
                "failed_phase": phase.name,
                "error": phase_result.get("error"),
                "phases": phases_out,
                "executed": executed,
                "context_artifacts": dict(context.get("artifacts") or {}),
            }
            if collect_feedback and not cancelled:
                result["feedback"] = record_run_feedback(harness, recipe, result, force=force)
            return result

    result = {
        "ok": True,
        "run_id": harness.runtime.run_id,
        "env": harness.workspace.env,
        "name": recipe.name,
        "version": recipe.version,
        "phases": phases_out,
        "executed": executed,
        "step_count": len(executed),
        "context_artifacts": dict(context.get("artifacts") or {}),
    }
    if collect_feedback:
        result["feedback"] = record_run_feedback(harness, recipe, result, force=force)
    _finish_run(harness, "completed")
    return result


def _initial_context(harness: RunSession, recipe: Recipe) -> dict[str, Any]:
    return {
        "run_id": harness.runtime.run_id,
        "env": harness.workspace.env,
        "recipe": {"name": recipe.name, "version": recipe.version},
        "artifacts": {},
        "steps": {},
        "last": {},
        "output": {},
        "status": None,
        "phase": None,
    }


def _run_phase(
    harness: RunSession,
    phase: RecipePhase,
    *,
    context: dict[str, Any],
    force: bool,
    actor: str,
    executed: list[dict[str, Any]],
) -> dict[str, Any]:
    context["phase"] = phase.name
    try:
        harness.begin_phase(
            phase.name,
            description=phase.description,
            require_artifacts=list(phase.require_artifacts),
        )
    except RuntimeError as exc:
        return {"ok": False, "name": phase.name, "error": str(exc), "status": "error"}

    step_error = _run_phase_steps(
        harness,
        phase,
        context=context,
        force=force,
        actor=actor,
        executed=executed,
    )
    if step_error is not None:
        return _fail_open_phase(harness, phase, error=step_error)

    try:
        summary = resolve_templates(dict(phase.summary), context)
        if not isinstance(summary, dict):
            summary = {"value": summary}
        ended = harness.end_phase(summary=summary)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        return {
            "ok": False,
            "name": phase.name,
            "status": "failed",
            "error": f"phase end: {exc}",
        }

    _sync_artifacts(harness, context)
    return {
        "ok": True,
        "name": phase.name,
        "status": "done",
        "phase": ended,
    }


def _run_phase_steps(
    harness: RunSession,
    phase: RecipePhase,
    *,
    context: dict[str, Any],
    force: bool,
    actor: str,
    executed: list[dict[str, Any]],
) -> str | None:
    """Execute phase steps. Return error string or None on success."""
    for step in phase.steps:
        if _cancellation_requested(harness):
            return "run cancellation requested"

        try:
            step_result = _execute_step(
                harness,
                step,
                context=context,
                force=force,
                actor=actor,
            )
        except (KeyError, TypeError, ValueError, RuntimeError, FileNotFoundError) as exc:
            return f"step {step.id}: {type(exc).__name__}: {exc}"

        executed.append(step_result)
        if not step_result.get("ok"):
            return str(step_result.get("error") or f"step {step.id} failed")
    return None


def _execute_step(
    harness: RunSession,
    step: RecipeStep,
    *,
    context: dict[str, Any],
    force: bool,
    actor: str,
) -> dict[str, Any]:
    if step.action == "artifact":
        return _execute_artifact(harness, step, context=context)

    skill = step.skill
    tool = step.tool
    if not skill or not tool:
        msg = f"step {step.id}: invoke requires skill and tool"
        raise ValueError(msg)
    # Secrets first so {{secret.NAME}} is not treated as a template path.
    raw_args = resolve_secret_refs(dict(step.args), harness.workspace)
    if not isinstance(raw_args, dict):
        raw_args = dict(step.args)
    args = resolve_templates(raw_args, context)
    if not isinstance(args, dict):
        msg = f"step {step.id}: args must resolve to an object"
        raise TypeError(msg)
    result = harness.invoke(skill, tool, args, actor=actor, force=force)
    output = dict(result.output)
    _update_step_context(
        context,
        step_id=step.id,
        skill=skill,
        tool=tool,
        status=result.status,
        output=output,
        error=result.error,
        args=args,
        attempt=1,
    )
    tool_reported_failure = result.status == "ok" and output.get("ok") is False
    ok = result.status == "ok" and not tool_reported_failure
    error = (
        str(output.get("message") or "tool returned ok=false")
        if tool_reported_failure
        else result.error
    )
    if result.status == "pending_approval":
        error = "approval required during recipe run (use force/auto-approve)"
    return {
        "ok": ok,
        "phase": context.get("phase"),
        "step_id": step.id,
        "action": "invoke",
        "skill": skill,
        "tool": tool,
        "status": "error" if tool_reported_failure else result.status,
        "error": error,
        "output": output,
        "approval_id": result.approval_id,
        "attempt": 1,
        "rpa_step_id": result.step_id,
        "invocation_id": result.invocation_id,
        "idempotency_key": result.idempotency_key,
        "failure": result.failure.model_dump(mode="json") if result.failure else None,
    }


def _update_step_context(
    context: dict[str, Any],
    *,
    step_id: str,
    skill: str,
    tool: str,
    status: str,
    output: dict[str, Any],
    error: str | None,
    args: dict[str, Any],
    attempt: int,
) -> None:
    context["status"] = status
    context["output"] = output
    context["last"] = {
        "id": step_id,
        "status": status,
        "output": output,
        "error": error,
        "skill": skill,
        "tool": tool,
        "attempt": attempt,
    }
    context["steps"][step_id] = {
        "status": status,
        "output": output,
        "error": error,
        "args": args,
        "attempt": attempt,
    }


def _execute_artifact(
    harness: RunSession,
    step: RecipeStep,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    artifact_name = step.name
    artifact_source = step.source
    if not artifact_name or not artifact_source:
        msg = f"step {step.id}: artifact requires name and source"
        raise ValueError(msg)
    source = resolve_templates(artifact_source, context)
    if source is None or str(source).strip() == "":
        msg = f"step {step.id}: artifact source resolved empty"
        raise ValueError(msg)
    meta = harness.put_artifact(str(source), name=artifact_name)
    context["artifacts"][artifact_name] = meta.get("path") or str(source)
    context["last"] = {
        "id": step.id,
        "status": "ok",
        "output": meta,
        "error": None,
        "action": "artifact",
    }
    context["output"] = meta
    context["status"] = "ok"
    context["steps"][step.id] = {
        "status": "ok",
        "output": meta,
        "error": None,
    }
    return {
        "ok": True,
        "phase": context.get("phase"),
        "step_id": step.id,
        "action": "artifact",
        "name": artifact_name,
        "status": "ok",
        "output": meta,
    }


def _fail_open_phase(
    harness: RunSession,
    phase: RecipePhase,
    *,
    error: str,
) -> dict[str, Any]:
    try:
        open_phase = harness.status().get("current_phase")
        if open_phase == phase.name:
            harness.fail_phase(error=error)
    except (RuntimeError, ValueError):
        pass
    return {
        "ok": False,
        "name": phase.name,
        "status": "failed",
        "error": error,
    }


def _sync_artifacts(harness: RunSession, context: dict[str, Any]) -> None:
    artifacts = context.setdefault("artifacts", {})
    for item in harness.list_artifacts():
        name = item.get("name")
        path = item.get("path")
        if name and path:
            artifacts[str(name)] = str(path)


def _cancellation_requested(harness: RunSession) -> bool:
    run = run_object(harness.runtime)
    return bool(
        (run and run.data.get("status") == "cancel_requested")
        or cancellation_request(harness.workspace, harness.runtime.run_id)
    )


def _finish_run(harness: RunSession, status: str) -> None:
    run = run_object(harness.runtime)
    if run is None:
        return
    fields = {
        "status": status,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if status == "cancelled" and not run.data.get("cancel_reason"):
        request = cancellation_request(harness.workspace, harness.runtime.run_id)
        if request and request.get("reason"):
            fields["cancel_reason"] = str(request["reason"])
    patch_run(harness.runtime, fields)
    RunStorage(harness.workspace).finalize(harness.runtime.run_id, status=status)


__all__ = ["plan_recipe", "run_recipe"]
