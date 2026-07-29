"""Linear recipe JSON I/O."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roi_h.harness.distill import build_recipe_from_run, export_recipe_from_run
from roi_h.harness.domain import Recipe
from roi_h.harness.recipe_lang import (
    apply_recipe_overrides,
    lookup_path,
    resolve_templates,
    validate_recipe,
)


def recipe_to_dict(recipe: Recipe) -> dict[str, Any]:
    """Serialize a recipe to a JSON-friendly dict."""
    return recipe.model_dump(mode="json")


def recipe_from_dict(data: dict[str, Any]) -> Recipe:
    """Validate and load a recipe from a dict."""
    return Recipe.model_validate(data)


def load_recipe(path: str | Path) -> Recipe:
    """Load a recipe.json file."""
    p = Path(path).expanduser()
    if not p.is_file():
        msg = f"recipe not found: {p}"
        raise FileNotFoundError(msg)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"recipe must be a JSON object: {p}"
        raise TypeError(msg)
    return recipe_from_dict(raw)


def write_recipe(path: str | Path, recipe: Recipe) -> Path:
    """Write recipe.json (pretty, stable keys)."""
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(recipe_to_dict(recipe), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out


__all__ = [
    "apply_recipe_overrides",
    "build_recipe_from_run",
    "export_recipe_from_run",
    "load_recipe",
    "lookup_path",
    "recipe_from_dict",
    "recipe_to_dict",
    "resolve_templates",
    "validate_recipe",
    "write_recipe",
]
