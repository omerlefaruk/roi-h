"""Linear recipe validation, templates, and CLI overrides."""

from __future__ import annotations

import re
from typing import Any

from roi_h.harness.domain import Recipe, RecipePhase, RecipeStep, validate_phase_name

_TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
# Paths may include numeric list indices: steps.find.output.data.messages.0.id
_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def validate_recipe(recipe: Recipe) -> list[str]:
    """Return human-readable validation issues (empty if ok)."""
    issues: list[str] = []
    if not recipe.name.strip():
        issues.append("recipe.name must be non-empty")
    if not recipe.phases:
        issues.append("recipe.phases must not be empty")
    seen_phases: set[str] = set()
    for phase in recipe.phases:
        issues.extend(_validate_phase(phase, seen_phases))
    return issues


def _validate_phase(phase: RecipePhase, seen_phases: set[str]) -> list[str]:
    issues: list[str] = []
    try:
        validate_phase_name(phase.name)
    except ValueError as exc:
        issues.append(str(exc))
    if phase.name in seen_phases:
        issues.append(f"duplicate phase name: {phase.name!r}")
    seen_phases.add(phase.name)
    step_ids = [step.id for step in phase.steps]
    if len(step_ids) != len(set(step_ids)):
        issues.append(f"phase {phase.name!r} has duplicate step ids")
    for step in phase.steps:
        issues.extend(_validate_step(step))
    return issues


def _validate_step(step: RecipeStep) -> list[str]:
    issues: list[str] = []
    if step.action == "invoke" and (not step.skill or not step.tool):
        issues.append(f"step {step.id!r}: invoke requires skill and tool")
    if step.action == "artifact" and (not step.name or not step.source):
        issues.append(f"step {step.id!r}: artifact requires name and source")
    return issues


def apply_recipe_overrides(
    recipe: Recipe,
    *,
    set_args: list[str] | None = None,
    arg_map: dict[str, object] | None = None,
) -> Recipe:
    """Apply CLI overrides to every invoke step's args.

    ``set_args`` entries look like ``headless=false`` or ``url=https://…``.
    ``arg_map`` is a direct dict merge into every invoke step.
    """
    overrides: dict[str, Any] = dict(arg_map or {})
    for item in set_args or []:
        if "=" not in item:
            msg = f"invalid --set {item!r}; use key=value"
            raise ValueError(msg)
        key, _, raw = item.partition("=")
        key = key.strip()
        raw = raw.strip()
        overrides[key] = _parse_override_value(raw)

    if not overrides:
        return recipe

    new_phases: list[RecipePhase] = []
    for phase in recipe.phases:
        new_steps: list[RecipeStep] = []
        for step in phase.steps:
            if step.action != "invoke":
                new_steps.append(step)
                continue
            merged = {**step.args, **overrides}
            new_steps.append(step.model_copy(update={"args": merged}))
        new_phases.append(phase.model_copy(update={"steps": new_steps}))
    return recipe.model_copy(update={"phases": new_phases})


def _parse_override_value(raw: str) -> object:
    low = raw.lower()
    if low in {"true", "yes"}:
        return True
    if low in {"false", "no"}:
        return False
    if low in {"null", "none"}:
        return None
    try:
        if any(ch in raw for ch in ".eE") and re.match(r"^-?\d", raw):
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def resolve_templates(value: object, context: dict[str, Any]) -> object:
    """Resolve ``{{path.to.value}}`` templates in strings; walk dicts/lists."""
    if isinstance(value, dict):
        return {str(k): resolve_templates(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, context) for item in value]
    if not isinstance(value, str):
        return value
    if "{{" not in value:
        return value

    def repl(match: re.Match[str]) -> str:
        path = match.group(1).strip()
        resolved = lookup_path(context, path)
        if resolved is None:
            msg = f"template path not found: {path!r}"
            raise KeyError(msg)
        return str(resolved)

    # whole-string template → preserve non-string types when possible
    whole = value.strip()
    if whole.startswith("{{") and whole.endswith("}}") and whole.count("{{") == 1:
        path = whole[2:-2].strip()
        resolved = lookup_path(context, path)
        if resolved is None:
            msg = f"template path not found: {path!r}"
            raise KeyError(msg)
        return resolved
    return _TEMPLATE_RE.sub(repl, value)


def lookup_path(context: dict[str, Any], path: str) -> object | None:
    """Resolve dotted path against context (returns None if missing).

    Keys may themselves contain dots (e.g. artifact name ``orders.xlsx``):
    matching prefers the longest key at each level.

    Numeric path segments index into lists (e.g. ``messages.0.id`` → first
    message id). Negative indices are not supported.
    """
    if not path or not _PATH_RE.match(path):
        return None
    parts = path.split(".")
    current: object = context
    i = 0
    while i < len(parts):
        part = parts[i]
        # List index: steps.foo.output.messages.0.id
        if isinstance(current, list) and part.isdigit():
            idx = int(part)
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
            i += 1
            continue
        if not isinstance(current, dict):
            return None
        matched_key: str | None = None
        matched_end = i
        for end in range(len(parts), i, -1):
            key = ".".join(parts[i:end])
            if key in current:
                matched_key = key
                matched_end = end
                break
        if matched_key is None:
            # Fall back: single segment as string key, or digit key on dict
            if part in current or (part.isdigit() and part in current):
                matched_key = part
                matched_end = i + 1
            else:
                return None
        current = current[matched_key]
        i = matched_end
    return current


__all__ = [
    "apply_recipe_overrides",
    "lookup_path",
    "resolve_templates",
    "validate_recipe",
]
