"""Export recipe → publish → deterministic rpa run."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from roi_h import RunSession
from roi_h.harness.automation import load_automation, publish_manifest, push_to_prod
from roi_h.harness.control import request_cancellation
from roi_h.harness.custom import define_project_tool
from roi_h.harness.domain import Recipe, RecipePhase, RecipeStep
from roi_h.harness.journeys import recipe_from_run
from roi_h.harness.loader import default_skills_root
from roi_h.harness.runner import run_recipe
from roi_h.harness.workspace import Workspace, create_project


def _open_ws(tmp_path: Path, *, env: str = "dev", project: str = "demo") -> Workspace:
    home = tmp_path / ".roi-h"
    if not (home / "projects" / project).is_dir():
        create_project(home, project, set_active=True)
    return Workspace.open(home, project=project, env=env)


def _roi_h(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "roi_h", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def test_export_publish_run_round_trip(tmp_path: Path) -> None:
    skills = default_skills_root()
    home = tmp_path / ".roi-h"
    ws = _open_ws(tmp_path)

    # project tool that writes a file (for artifact step in hand-authored branch)
    define_project_tool(
        skill="util",
        tool="write_note",
        description="write a note file",
        project_root=ws.project_skills,
        source=(
            "from pathlib import Path\n"
            "from pydantic import BaseModel, Field\n"
            "TOOL_ID='write_note'\nDESCRIPTION='w'\n"
            "TOOL_EFFECT='write'\nIDEMPOTENCY='reconcile'\n"
            "REQUIRES_APPROVAL=False\nALLOW_IN_PROD=True\n"
            "TIMEOUT_SECONDS=120.0\nSECRET_NAMES=()\nNETWORK_HOSTS=()\n"
            "FILESYSTEM_ROOTS=('run:output:read-write',)\n"
            "class Input(BaseModel):\n"
            "    text: str = 'hi'\n"
            "    path: str = Field(default='note.txt')\n"
            "class Output(BaseModel):\n"
            "    path: str\n"
            "    text: str\n"
            "def run(args: Input) -> Output:\n"
            "    p = Path(args.path)\n"
            "    p.write_text(args.text, encoding='utf-8')\n"
            "    return Output(path=str(p.resolve()), text=args.text)\n"
        ),
    )

    harness = RunSession.create(
        ws,
        run_id="build1",
        skills_root=skills,
        auto_approve=True,
    )
    harness.start_run(
        "build recipe",
        phase_plan=["browse:open site", "finish:wrap up"],
    )
    harness.begin_phase("browse")
    assert harness.invoke("browser", "navigate", {"url": "https://example.com/"}).status == "ok"
    assert harness.invoke("browser", "snapshot", {}).status == "ok"
    harness.end_phase(summary={"url": "https://example.com/"})

    harness.begin_phase("finish")
    step = harness.invoke(
        "util",
        "write_note",
        {"text": "done", "path": "run://output/n.txt"},
    )
    assert step.status == "ok"
    harness.put_artifact(step.output["path"], name="note.txt")
    harness.end_phase()

    recipe, distill = recipe_from_run(harness, name="demo-job", version="1.0.0")
    # Project tool present → browser explore noise distilled out of prod recipe.
    assert [p.name for p in recipe.phases] == ["finish"]
    assert distill["mode"] == "prod"
    invoke_steps = [s for s in recipe.phases[0].steps if s.action == "invoke"]
    assert len(invoke_steps) == 1
    assert invoke_steps[0].tool == "write_note"
    # Auto-artifact may be distilled from path-like tool outputs.
    write_id = invoke_steps[0].id

    # enrich finish phase with artifact step for re-run
    finish_steps = [
        invoke_steps[0],
        RecipeStep(
            id="put_note",
            action="artifact",
            name="note.txt",
            source=f"{{{{steps.{write_id}.output.path}}}}",
        ),
    ]
    # rewrite write path to tmp under each run via template on path arg
    finish_steps[0] = RecipeStep(
        id=write_id,
        action="invoke",
        skill="util",
        tool="write_note",
        args={"text": "done", "path": "run://output/run-note.txt"},
    )
    recipe = Recipe(
        name=recipe.name,
        version=recipe.version,
        goal=recipe.goal,
        phases=[
            RecipePhase(
                name="finish",
                description=recipe.phases[0].description,
                require_artifacts=["note.txt"],
                summary=dict(recipe.phases[0].summary),
                steps=finish_steps,
            ),
        ],
        source_run_id=recipe.source_run_id,
        budgets=dict(recipe.budgets),
    )

    published = publish_manifest(
        ws,
        name="demo-job",
        version="1.0.0",
        goal="demo",
        skills=["util"],
        recipe=recipe,
    )
    assert published["has_recipe"] is True
    assert (ws.automations / "demo-job" / "1.0.0" / "recipe.json").is_file()

    push = push_to_prod(root=home, project="demo", name="demo-job", version="1.0.0")
    assert push["ok"] is True
    assert push["has_recipe"] is True

    prod = Workspace.open(home, project="demo", env="prod")
    package = load_automation(prod, "demo-job")
    assert package["has_recipe"] is True

    runner = RunSession.create(
        prod,
        run_id="prod-run-1",
        skills_root=skills,
        project_skills=package["skills_dir"],
        auto_approve=True,
    )
    runner.start_run("prod", phase_plan=package["recipe_obj"].phase_plan_entries())
    result = run_recipe(runner, package["recipe_obj"], force=True)
    assert result["ok"] is True, result
    assert result["step_count"] >= 2  # write_note + artifact put
    status = runner.status()
    assert status["error_steps"] == 0, [
        item for item in status["steps"] if item["data"]["status"] == "error"
    ]
    phase_names = {p["name"]: p["status"] for p in status["phases"]}
    assert "browse" not in phase_names  # distilled out of prod recipe
    assert phase_names.get("finish") == "done"
    assert any(a["name"] == "note.txt" for a in status["artifacts"])


def test_recipe_honors_durable_cancellation_before_next_step(tmp_path: Path) -> None:
    workspace = _open_ws(tmp_path)
    harness = RunSession.create(
        workspace,
        run_id="cancelled-run",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("cancel me")
    request_cancellation(workspace, harness.runtime.run_id, reason="maintenance window")
    recipe = Recipe(
        name="cancelled",
        version="1.0.0",
        goal="must not invoke",
        phases=[
            RecipePhase(
                name="only",
                steps=[
                    RecipeStep(
                        id="navigate",
                        action="invoke",
                        skill="browser",
                        tool="navigate",
                        args={"url": "https://example.com"},
                    )
                ],
            )
        ],
    )

    result = run_recipe(harness, recipe, force=True)

    assert result["ok"] is False
    status = harness.status()
    assert status["run_status"] == "cancelled"
    assert status["cancel_reason"] == "maintenance window"
    assert status["completed_at"]
    assert status["invocation_count"] == 0


def test_shared_skill_is_snapshotted_and_frozen_for_execution(tmp_path: Path) -> None:
    workspace = _open_ws(tmp_path)
    shared_skill = workspace.shared_skills / "sharedutil"
    (shared_skill / "scripts").mkdir(parents=True)
    (shared_skill / "SKILL.md").write_text("# sharedutil\n", encoding="utf-8")
    (shared_skill / "scripts" / "echo.py").write_text(
        (
            "from pydantic import BaseModel\n"
            "TOOL_ID='echo'\n"
            "DESCRIPTION='echo'\n"
            "TOOL_EFFECT='read'\n"
            "ALLOW_IN_PROD=True\n"
            "class Input(BaseModel):\n"
            "    value: str\n"
            "class Output(BaseModel):\n"
            "    ok: bool = True\n"
            "    value: str\n"
            "def run(args: Input) -> Output:\n"
            "    return Output(value=args.value)\n"
        ),
        encoding="utf-8",
    )
    recipe = Recipe(
        name="shared-job",
        version="1.0.0",
        phases=[
            RecipePhase(
                name="main",
                steps=[
                    RecipeStep(
                        id="echo",
                        skill="sharedutil",
                        tool="echo",
                        args={"value": "frozen"},
                    )
                ],
            )
        ],
    )

    published = publish_manifest(
        workspace,
        name="shared-job",
        version="1.0.0",
        recipe=recipe,
    )
    package_skills = Path(published["path"]).parent / "skills"
    assert (package_skills / "sharedutil" / "scripts" / "echo.py").is_file()

    shutil.rmtree(shared_skill)
    runner = RunSession.create(
        workspace,
        run_id="shared-frozen",
        project_skills=package_skills,
        auto_approve=True,
    )
    runner.start_run("shared frozen", phase_plan=recipe.phase_plan_entries())
    result = run_recipe(runner, recipe, force=True)

    assert result["ok"] is True
    assert result["executed"][0]["output"]["value"] == "frozen"


def test_cli_ship_then_run_dry_and_live(tmp_path: Path) -> None:
    skills = str(default_skills_root())
    home = str(tmp_path / ".roi-h")
    init = _roi_h("rpa", "project", "create", "demo", "--home", home, cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    common = ["--home", home, "--env", "dev", "--skills", skills, "--auto-approve"]

    start = _roi_h(
        "rpa",
        "start",
        *common,
        "--run-id",
        "cli-build",
        "--goal",
        "cli recipe",
        "--phase",
        "only",
        cwd=tmp_path,
    )
    assert start.returncode == 0, start.stderr

    assert (
        _roi_h(
            "rpa",
            "phase",
            "begin",
            *common,
            "--run-id",
            "cli-build",
            "only",
            cwd=tmp_path,
        ).returncode
        == 0
    )
    inv = _roi_h(
        "rpa",
        "invoke",
        *common,
        "--run-id",
        "cli-build",
        "browser",
        "navigate",
        "--args",
        '{"url":"https://example.com/"}',
        cwd=tmp_path,
    )
    assert inv.returncode == 0, inv.stdout + inv.stderr
    assert (
        _roi_h(
            "rpa",
            "phase",
            "end",
            *common,
            "--run-id",
            "cli-build",
            cwd=tmp_path,
        ).returncode
        == 0
    )

    shipped = _roi_h(
        "rpa",
        "automation",
        "ship",
        "--home",
        home,
        "--env",
        "dev",
        "--skills",
        skills,
        "--name",
        "cli-job",
        "--version",
        "1.0.0",
        "--from-run",
        "cli-build",
        "--no-prod-dry-run",
        cwd=tmp_path,
    )
    assert shipped.returncode == 0, shipped.stderr

    dry = _roi_h(
        "rpa",
        "automation",
        "run",
        "cli-job",
        "--home",
        home,
        "--env",
        "prod",
        "--skills",
        skills,
        "--dry-run",
        "--run-id",
        "dry1",
        cwd=tmp_path,
    )
    assert dry.returncode == 0, dry.stderr
    dry_payload = json.loads(dry.stdout)
    assert dry_payload["dry_run"] is True
    assert dry_payload["ok"] is True
    assert dry_payload["step_count"] >= 1

    live = _roi_h(
        "rpa",
        "automation",
        "run",
        "cli-job",
        "--home",
        home,
        "--env",
        "prod",
        "--skills",
        skills,
        "--run-id",
        "live1",
        cwd=tmp_path,
    )
    assert live.returncode == 0, live.stdout + live.stderr
    live_payload = json.loads(live.stdout)
    assert live_payload["ok"] is True
    assert live_payload["step_count"] >= 1


def test_recipe_treats_structured_tool_ok_false_as_failure(tmp_path: Path) -> None:
    skills = default_skills_root()
    ws = _open_ws(tmp_path)
    define_project_tool(
        skill="util",
        tool="structured_failure",
        description="Return an operational failure without raising",
        project_root=ws.project_skills,
        source=(
            "from pydantic import BaseModel\n"
            "TOOL_ID='structured_failure'\n"
            "DESCRIPTION='structured failure'\n"
            "REQUIRES_APPROVAL=False\n"
            "class Input(BaseModel):\n    pass\n"
            "class Output(BaseModel):\n"
            "    ok: bool = False\n"
            "    message: str = 'download missing'\n"
            "def run(args: Input) -> Output:\n"
            "    return Output()\n"
        ),
    )
    recipe = Recipe(
        name="structured-failure",
        version="1.0.0",
        phases=[
            RecipePhase(
                name="main",
                steps=[RecipeStep(id="download", skill="util", tool="structured_failure")],
            )
        ],
    )
    harness = RunSession.create(
        ws,
        run_id="structured-failure",
        skills_root=skills,
        auto_approve=True,
    )
    harness.start_run("structured failure", phase_plan=recipe.phase_plan_entries())

    result = run_recipe(harness, recipe, force=True)

    assert result["ok"] is False
    assert result["failed_phase"] == "main"
    executed = result["executed"][0]
    assert executed["attempt"] == 1
    assert executed["output"]["ok"] is False
    assert executed["error"] == "download missing"


def test_run_from_handoff_skips_seeded_phase(tmp_path: Path) -> None:
    skills = default_skills_root()
    ws = _open_ws(tmp_path)
    harness = RunSession.create(ws, run_id="src", skills_root=skills, auto_approve=True)
    harness.start_run("src", phase_plan=["a", "b"])
    harness.begin_phase("a")
    harness.invoke("browser", "navigate", {"url": "https://example.com/"})
    sample = tmp_path / "a.txt"
    sample.write_text("seed", encoding="utf-8")
    harness.put_artifact(sample, name="a.txt")
    ended = harness.end_phase()
    handoff = ended["handoff"]["handoff_path"]

    recipe = Recipe(
        name="partial",
        version="1.0.0",
        phases=[
            RecipePhase(
                name="a",
                require_artifacts=["a.txt"],
                steps=[
                    RecipeStep(
                        id="nav",
                        skill="browser",
                        tool="navigate",
                        args={"url": "https://example.com/"},
                    )
                ],
            ),
            RecipePhase(
                name="b",
                steps=[
                    RecipeStep(id="snap", skill="browser", tool="snapshot", args={}),
                ],
            ),
        ],
    )
    publish_manifest(ws, name="partial", version="1.0.0", recipe=recipe)

    runner = RunSession.create(
        ws,
        run_id="partial-run",
        skills_root=skills,
        project_skills=ws.automations / "partial" / "1.0.0" / "skills",
        auto_approve=True,
    )
    runner.start_run("partial", phase_plan=recipe.phase_plan_entries())
    seeded = runner.seed_from_handoff(handoff)
    skip = {str(p["name"]) for p in seeded["seeded_phases"]}
    result = run_recipe(runner, recipe, skip_phases=skip, force=True)
    assert result["ok"] is True, result
    phases = {p["name"]: p for p in result["phases"]}
    assert phases["a"]["status"] == "skipped"
    assert phases["b"]["status"] == "done"
