"""Linear recipe templates and validation."""

from __future__ import annotations

import pytest

from roi_h.harness.domain import (
    Recipe,
    RecipePhase,
    RecipeStep,
    parse_phase_plan,
)
from roi_h.harness.recipe import export_recipe_from_run, resolve_templates, validate_recipe


def test_resolve_templates_nested() -> None:
    ctx = {
        "last": {"output": {"path": "/var/data/a.csv", "n": 3}},
        "artifacts": {"orders.xlsx": "/data/orders.xlsx"},
    }
    assert resolve_templates("{{last.output.path}}", ctx) == "/var/data/a.csv"
    assert resolve_templates("file={{artifacts.orders.xlsx}}", ctx) == "file=/data/orders.xlsx"
    assert resolve_templates({"src": "{{last.output.path}}", "n": "{{last.output.n}}"}, ctx) == {
        "src": "/var/data/a.csv",
        "n": 3,
    }


def test_resolve_templates_list_index() -> None:
    """Numeric path segments index into lists (messages.0.id)."""
    ctx = {
        "steps": {
            "find": {
                "output": {
                    "data": {
                        "messages": [
                            {"id": "msg_1", "thread_id": "thr_1"},
                            {"id": "msg_2", "thread_id": "thr_2"},
                        ]
                    },
                    "message_id": "msg_1",
                }
            }
        }
    }
    assert resolve_templates("{{steps.find.output.data.messages.0.id}}", ctx) == "msg_1"
    assert resolve_templates("{{steps.find.output.data.messages.1.thread_id}}", ctx) == "thr_2"
    with pytest.raises(KeyError):
        resolve_templates("{{steps.find.output.data.messages.9.id}}", ctx)


def test_validate_linear_recipe() -> None:
    good = Recipe(
        name="job",
        version="1.0.0",
        phases=[
            RecipePhase(
                name="p1",
                steps=[
                    RecipeStep(id="a", skill="browser", tool="navigate", args={"url": "https://x"}),
                    RecipeStep(id="b", skill="browser", tool="snapshot"),
                ],
            )
        ],
    )
    assert validate_recipe(good) == []

    bad = Recipe(
        name="job",
        phases=[
            RecipePhase(
                name="p1",
                steps=[
                    RecipeStep(
                        id="a",
                        skill="browser",
                        tool=None,
                    )
                ],
            )
        ],
    )
    issues = validate_recipe(bad)
    assert any("requires skill and tool" in item for item in issues)


def test_export_recipe_from_run_objects() -> None:
    recipe = export_recipe_from_run(
        name="weekly",
        version="1.0.0",
        goal="orders",
        source_run_id="r1",
        phase_objects=[
            {
                "phase_id": "ph1",
                "name": "browse",
                "index": 1,
                "status": "done",
                "description": "dl",
                "require_artifacts": [],
                "summary": {"portal": "x"},
            },
            {
                "phase_id": "ph2",
                "name": "normalize",
                "index": 2,
                "status": "done",
                "require_artifacts": ["clean.csv"],
                "summary": {},
            },
        ],
        step_objects=[
            {
                "phase_id": "ph1",
                "skill": "browser",
                "tool": "navigate",
                "status": "ok",
                "args": {"url": "https://example.com/"},
            },
            {
                "phase_id": "ph1",
                "skill": "browser",
                "tool": "snapshot",
                "status": "error",
                "args": {},
            },
            {
                "phase_id": "ph2",
                "skill": "browser",
                "tool": "click",
                "status": "ok",
                "args": {"selector": "#x"},
            },
        ],
    )
    assert recipe.name == "weekly"
    # No project tools → browser transcript kept (with failed snapshot dropped).
    assert len(recipe.phases) == 2
    assert len(recipe.phases[0].steps) == 1
    assert recipe.phases[0].steps[0].tool == "navigate"
    assert recipe.phases[1].require_artifacts == ["clean.csv"]


def test_parse_phase_role_tokens() -> None:
    plan = parse_phase_plan(["explore:role=explore", "solve:role=work:do it", "verify:role=verify"])
    assert [p.role for p in plan] == ["explore", "work", "verify"]
    assert plan[1].description == "do it"


def test_export_distills_explore_and_browser_when_project_tool_exists() -> None:
    recipe = export_recipe_from_run(
        name="input-forms",
        version="1.0.0",
        goal="challenge",
        source_run_id="r1",
        distill=True,
        phase_objects=[
            {"phase_id": "ph1", "name": "explore", "index": 1, "status": "done"},
            {"phase_id": "ph2", "name": "solve", "index": 2, "status": "done"},
            {"phase_id": "ph3", "name": "verify", "index": 3, "status": "done"},
        ],
        step_objects=[
            {
                "phase_id": "ph1",
                "skill": "browser",
                "tool": "navigate",
                "status": "ok",
                "scope": "global",
                "args": {"url": "https://rpachallenge.com/"},
            },
            {
                "phase_id": "ph1",
                "skill": "browser",
                "tool": "snapshot",
                "status": "ok",
                "scope": "global",
                "args": {},
            },
            {
                "phase_id": "ph2",
                "skill": "rpachallenge",
                "tool": "solve",
                "status": "ok",
                "scope": "project",
                "args": {},
            },
        ],
    )
    assert [p.name for p in recipe.phases] == ["solve"]
    assert len(recipe.phases[0].steps) == 1
    assert recipe.phases[0].steps[0].skill == "rpachallenge"
    assert recipe.phases[0].steps[0].tool == "solve"
    assert "mode=prod" in recipe.notes


def test_resolve_missing_template_raises() -> None:
    with pytest.raises(KeyError):
        resolve_templates("{{missing.path}}", {})
