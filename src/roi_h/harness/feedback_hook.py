"""Optional post-run feedback recording."""

from __future__ import annotations

from typing import Any

from roi_h.harness.domain import Recipe


def record_run_feedback(
    harness: Any,
    recipe: Recipe,
    result: dict[str, Any],
    *,
    force: bool = True,
) -> dict[str, Any] | None:
    """Record a compact run summary when ``feedback.record`` is installed."""
    try:
        harness.catalog.resolve("feedback", "record")
    except KeyError:
        return None

    feedback = build_run_feedback(recipe, result, list(result.get("executed") or []))
    feedback["summary"].update(
        {
            "project": harness.workspace.project,
            "env": harness.workspace.env,
            "run_id": harness.runtime.run_id,
        }
    )
    try:
        step = harness.invoke(
            "feedback",
            "record",
            {
                "automation": recipe.name,
                "version": recipe.version,
                "ok": bool(result.get("ok")),
                **feedback,
            },
            actor="recipe",
            force=force,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}
    return {
        "status": step.status,
        "output": step.output,
        "error": step.error,
        "failure": step.failure.model_dump(mode="json") if step.failure else None,
        "invocation_id": step.invocation_id,
        "attempt": step.attempt,
    }


def build_run_feedback(
    recipe: Recipe,
    result: dict[str, Any],
    executed: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive a generic summary and one actionable suggestion."""
    ok = bool(result.get("ok"))
    tools = [
        f"{item['skill']}.{item['tool']}"
        for item in executed
        if item.get("skill") and item.get("tool") and item.get("skill") != "feedback"
    ]
    summary = {
        "automation": recipe.name,
        "version": recipe.version,
        "ok": ok,
        "failed_phase": result.get("failed_phase"),
        "error": result.get("error"),
        "step_count": result.get("step_count") or len(executed),
        "phase_count": len(result.get("phases") or []),
        "tools_used": list(dict.fromkeys(tools)),
        "goal": recipe.goal,
    }
    if ok:
        suggestions = ["Record operator friction when a successful run still needed intervention."]
        severity = "info"
    else:
        suggestions = ["Inspect the failed phase and final tool result before changing the recipe."]
        severity = "bug"
    status = "PASS" if ok else "FAIL"
    notes = (
        f"[{status}] automation={recipe.name} "
        f"phase={result.get('failed_phase')!r} error={result.get('error')!r}"
    )
    return {
        "summary": summary,
        "suggestions": suggestions,
        "notes": notes,
        "severity": severity,
    }


__all__ = ["build_run_feedback", "record_run_feedback"]
