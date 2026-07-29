"""Immutable automation package and stable-interface boundaries."""

from pathlib import Path

import pytest

from roi_h import AutomationRegistry, WorkspaceCatalog
from roi_h.harness.automation import load_automation, publish_manifest
from roi_h.harness.domain import Recipe, RecipePhase, RecipeStep


def _recipe(*, url: str = "https://example.com") -> Recipe:
    return Recipe(
        name="integrity",
        version="1.0.0",
        goal="verify package integrity",
        phases=[
            RecipePhase(
                name="browse",
                steps=[
                    RecipeStep(
                        id="navigate",
                        action="invoke",
                        skill="browser",
                        tool="navigate",
                        args={"url": url},
                    )
                ],
            )
        ],
    )


def test_package_version_is_idempotent_but_not_mutable(tmp_path: Path) -> None:
    catalog = WorkspaceCatalog.at(tmp_path / ".roi-h")
    workspace = catalog.create("integrity")

    first = publish_manifest(
        workspace,
        name="integrity",
        version="1.0.0",
        recipe=_recipe(),
    )
    second = publish_manifest(
        workspace,
        name="integrity",
        version="1.0.0",
        recipe=_recipe(),
    )
    assert second["package_digest"] == first["package_digest"]

    with pytest.raises(FileExistsError, match="immutable"):
        publish_manifest(
            workspace,
            name="integrity",
            version="1.0.0",
            recipe=_recipe(url="https://example.org"),
        )


def test_package_tampering_is_rejected_before_load(tmp_path: Path) -> None:
    catalog = WorkspaceCatalog.at(tmp_path / ".roi-h")
    workspace = catalog.create("integrity")
    published = publish_manifest(
        workspace,
        name="integrity",
        version="1.0.0",
        recipe=_recipe(),
    )
    package = Path(published["path"]).parent
    recipe_path = package / "recipe.json"
    recipe_path.write_text(recipe_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="digest mismatch"):
        load_automation(workspace, "integrity", version="1.0.0")


def test_registry_exposes_verified_package_operations(tmp_path: Path) -> None:
    catalog = WorkspaceCatalog.at(tmp_path / ".roi-h")
    workspace = catalog.create("integrity")
    publish_manifest(
        workspace,
        name="integrity",
        version="1.0.0",
        recipe=_recipe(),
    )

    registry = AutomationRegistry(workspace)

    assert registry.list()[0]["latest"] == "1.0.0"
    assert registry.load("integrity")["package_digest"].startswith("sha256:")
