"""Operator CLI for AI-driven RPA runs (no hand-written Python required)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from activegraph.store.sqlite import SQLiteEventStore

from roi_h.harness import RunSession
from roi_h.harness.automation import list_automations
from roi_h.harness.custom import define_project_tool
from roi_h.harness.domain import BudgetSpec
from roi_h.harness.journeys import (
    new_run_id,
    run_automation,
    ship_automation,
    validate_run_id,
)
from roi_h.harness.loader import default_skills_root, load_skills, resolve_skills_root
from roi_h.harness.secrets import delete_secret, list_secrets, set_secret
from roi_h.harness.workspace import (
    Workspace,
    create_project,
    delete_project,
    get_active_project,
    init_home,
    list_projects,
    rename_project,
    resolve_home,
    set_active_env,
    set_active_project,
)

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _configure_ui_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--home",
        default=None,
        help="Home root (default: ~/.roi-h or ROI_H_HOME)",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Start the observer without opening a browser",
    )
    parser.set_defaults(handler=_cmd_ui)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``roi-h`` / ``python -m roi_h``."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = args.handler(args)
    except (
        FileExistsError,
        FileNotFoundError,
        ImportError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        RuntimeError,
    ) as exc:
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    if result is not None:
        _emit(result)
    return 0 if result is None or result.get("ok", True) else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roi-h",
        description="ROI-H operator CLI for skills-based RPA (dev/prod aware).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    rpa = sub.add_parser("rpa", help="RPA harness commands")
    rpa_sub = rpa.add_subparsers(dest="rpa_command", required=True)
    observer_shortcut = sub.add_parser(
        "ui",
        help="Open the local read-only run observer",
    )
    _configure_ui_parser(observer_shortcut)

    # project
    project = rpa_sub.add_parser("project", help="Create, list, and switch named projects")
    project_sub = project.add_subparsers(dest="project_command", required=True)

    project_list = project_sub.add_parser("list", help="List projects under home")
    project_list.add_argument(
        "--home",
        default=None,
        help="Home root (default: ~/.roi-h or ROI_H_HOME)",
    )
    project_list.set_defaults(handler=_cmd_project_list)

    project_show = project_sub.add_parser("show", help="Show a project (default: active)")
    project_show.add_argument("name", nargs="?", default=None)
    _add_workspace_options(project_show)
    project_show.set_defaults(handler=_cmd_project_show)

    project_create = project_sub.add_parser("create", help="Create a named project")
    project_create.add_argument("name")
    project_create.add_argument("--display-name", default="")
    project_create.add_argument(
        "--use",
        action="store_true",
        default=True,
        help="Set as active project (default: true)",
    )
    project_create.add_argument(
        "--no-use",
        action="store_false",
        dest="use",
        help="Do not change the active project",
    )
    project_create.add_argument("--env", choices=["dev", "prod"], default="dev")
    project_create.add_argument(
        "--home",
        default=None,
        help="Home root (default: ~/.roi-h or ROI_H_HOME)",
    )
    project_create.set_defaults(handler=_cmd_project_create)

    project_use = project_sub.add_parser("use", help="Set the sticky active project")
    project_use.add_argument("name")
    project_use.add_argument(
        "--home",
        default=None,
        help="Home root (default: ~/.roi-h or ROI_H_HOME)",
    )
    project_use.set_defaults(handler=_cmd_project_use)

    project_init = project_sub.add_parser(
        "init",
        help="Ensure home has a project (creates 'default' if empty)",
    )
    project_init.add_argument(
        "--project",
        default="default",
        help="Name for the initial project when home is empty (default: default)",
    )
    project_init.add_argument("--display-name", default="")
    project_init.add_argument(
        "--home",
        default=None,
        help="Home root (default: ~/.roi-h or ROI_H_HOME)",
    )
    project_init.set_defaults(handler=_cmd_project_init)

    project_delete = project_sub.add_parser("delete", help="Delete a project (destructive)")
    project_delete.add_argument("name")
    project_delete.add_argument(
        "--force",
        action="store_true",
        help="Required confirmation flag",
    )
    project_delete.add_argument("--home", default=None)
    project_delete.set_defaults(handler=_cmd_project_delete)

    project_rename = project_sub.add_parser("rename", help="Rename a project")
    project_rename.add_argument("name")
    project_rename.add_argument("new_name")
    project_rename.add_argument("--home", default=None)
    project_rename.set_defaults(handler=_cmd_project_rename)

    # env
    env = rpa_sub.add_parser("env", help="Show or set active environment (dev|prod)")
    env_sub = env.add_subparsers(dest="env_command", required=True)
    env_show = env_sub.add_parser("show", help="Show active workspace paths")
    _add_workspace_options(env_show)
    env_show.set_defaults(handler=_cmd_env_show)
    env_set = env_sub.add_parser("set", help="Set active environment on the project")
    env_set.add_argument("name", choices=["dev", "prod"])
    _add_workspace_options(env_set)
    env_set.set_defaults(handler=_cmd_env_set)

    # tools
    tools = rpa_sub.add_parser("tools", help="List global + project tools")
    _add_workspace_options(tools)
    _add_skills_option(tools)
    tools.set_defaults(handler=_cmd_tools)

    # start
    start = rpa_sub.add_parser("start", help="Create/open a durable run")
    _add_workspace_options(start)
    _add_skills_option(start)
    _add_budget_options(start)
    start.add_argument("--run-id", default=None)
    start.add_argument("--goal", required=True)
    start.add_argument("--actor", default="ai")
    start.add_argument(
        "--auto-approve",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override approval policy (default: prod on, dev off)",
    )
    start.add_argument(
        "--phase",
        action="append",
        default=None,
        dest="phases",
        help=(
            "Phase plan entry (repeatable). "
            "Forms: name | name:desc | name:role=explore|work|verify | name:role=work:desc"
        ),
    )
    start.add_argument(
        "--from-handoff",
        default=None,
        help="Seed completed phase(s) from a handoff package or phases/ directory",
    )
    start.set_defaults(handler=_cmd_start)

    # invoke
    invoke = rpa_sub.add_parser("invoke", help="Invoke skill.tool")
    _add_run_options(invoke)
    invoke.add_argument("skill")
    invoke.add_argument("tool")
    invoke.add_argument("--args", default="{}")
    invoke.add_argument("--actor", default="ai")
    invoke.add_argument(
        "--force",
        action="store_true",
        help="Skip approval gate for this call",
    )
    invoke.set_defaults(handler=_cmd_invoke)

    adapt = rpa_sub.add_parser(
        "adapt",
        help="Run a bounded Codex-guided development loop",
    )
    _add_run_options(adapt)
    adapt.add_argument("--goal", required=True)
    adapt.add_argument(
        "--tool",
        action="append",
        required=True,
        dest="adaptive_tools",
        help="Allowed skill.tool name (repeatable; destructive tools are rejected)",
    )
    adapt.add_argument("--max-turns", type=int, default=6)
    adapt.add_argument("--actor", default="codex")
    adapt.set_defaults(handler=_cmd_adapt)

    # status
    status = rpa_sub.add_parser("status", help="Run summary + approvals + artifacts + advice")
    _add_run_options(status)
    status.set_defaults(handler=_cmd_status)

    observer = rpa_sub.add_parser("ui", help="Open the local read-only run observer")
    _configure_ui_parser(observer)

    cancel = rpa_sub.add_parser("cancel", help="Request cooperative run cancellation")
    _add_run_options(cancel)
    cancel.add_argument("--reason", default="operator requested cancellation")
    cancel.set_defaults(handler=_cmd_cancel)

    reconcile = rpa_sub.add_parser(
        "reconcile",
        help="Check graph records against artifact and handoff files",
    )
    _add_run_options(reconcile)
    reconcile.add_argument(
        "--repair",
        action="store_true",
        help="Apply only unambiguous metadata, orphan-record, and handoff repairs",
    )
    reconcile.set_defaults(handler=_cmd_reconcile)

    # custom
    custom = rpa_sub.add_parser("custom", help="Define a project-local tool")
    _add_workspace_options(custom)
    _add_skills_option(custom)
    custom.add_argument("--skill", required=True)
    custom.add_argument("--tool", required=True)
    custom.add_argument("--description", default="")
    custom.add_argument("--script", default=None)
    custom.add_argument("--overwrite", action="store_true")
    custom.set_defaults(handler=_cmd_custom)

    # approvals
    approvals = rpa_sub.add_parser("approvals", help="List pending tool approvals")
    _add_run_options(approvals)
    approvals.add_argument("--status", default="pending", choices=["pending", "all"])
    approvals.set_defaults(handler=_cmd_approvals)

    approve = rpa_sub.add_parser("approve", help="Grant a pending approval and execute the tool")
    _add_run_options(approve)
    approve.add_argument("approval_id")
    approve.add_argument("--by", default="user")
    approve.set_defaults(handler=_cmd_approve)

    # artifacts
    artifacts = rpa_sub.add_parser("artifact", help="Manage run file artifacts")
    art_sub = artifacts.add_subparsers(dest="artifact_command", required=True)
    art_put = art_sub.add_parser("put", help="Store a file on the run")
    _add_run_options(art_put)
    art_put.add_argument("--file", required=True)
    art_put.add_argument("--name", default=None)
    art_put.set_defaults(handler=_cmd_artifact_put)
    art_list = art_sub.add_parser("list", help="List artifacts for a run")
    _add_run_options(art_list)
    art_list.set_defaults(handler=_cmd_artifact_list)

    # phases
    phase = rpa_sub.add_parser("phase", help="Checkpointed run phases + handoff packages")
    phase_sub = phase.add_subparsers(dest="phase_command", required=True)

    phase_begin = phase_sub.add_parser("begin", help="Open a phase (only one open at a time)")
    _add_run_options(phase_begin)
    phase_begin.add_argument("name", help="Phase name slug (e.g. browse_download)")
    phase_begin.add_argument("--description", default="")
    phase_begin.add_argument(
        "--require-artifact",
        action="append",
        default=None,
        dest="require_artifacts",
        help="Artifact name that must exist when phase ends as done (repeatable)",
    )
    phase_begin.set_defaults(handler=_cmd_phase_begin)

    phase_end = phase_sub.add_parser("end", help="Close open phase and write handoff package")
    _add_run_options(phase_end)
    phase_end.add_argument(
        "--summary",
        default="{}",
        help="JSON object summary stored on the handoff contract",
    )
    phase_end.add_argument(
        "--require-artifact",
        action="append",
        default=None,
        dest="require_artifacts",
        help="Override required artifacts for this end (repeatable)",
    )
    phase_end.set_defaults(handler=_cmd_phase_end)

    phase_fail = phase_sub.add_parser("fail", help="Mark open phase as failed")
    _add_run_options(phase_fail)
    phase_fail.add_argument("--error", required=True, help="Failure reason")
    phase_fail.add_argument("--summary", default="{}")
    phase_fail.set_defaults(handler=_cmd_phase_fail)

    phase_skip = phase_sub.add_parser("skip", help="Skip a phase (or close open as skipped)")
    _add_run_options(phase_skip)
    phase_skip.add_argument("name")
    phase_skip.add_argument("--reason", default="")
    phase_skip.set_defaults(handler=_cmd_phase_skip)

    phase_retry = phase_sub.add_parser(
        "retry",
        help="Open a new instance of a prior phase (history kept)",
    )
    _add_run_options(phase_retry)
    phase_retry.add_argument("name")
    phase_retry.add_argument("--description", default="")
    phase_retry.add_argument(
        "--require-artifact",
        action="append",
        default=None,
        dest="require_artifacts",
    )
    phase_retry.set_defaults(handler=_cmd_phase_retry)

    phase_list = phase_sub.add_parser("list", help="List phases for a run")
    _add_run_options(phase_list)
    phase_list.set_defaults(handler=_cmd_phase_list)

    # deterministic run
    run_cmd = rpa_sub.add_parser(
        "run",
        help="Execute a published automation recipe end-to-end (no AI orchestration)",
    )
    _add_workspace_options(run_cmd)
    _add_skills_option(run_cmd)
    _add_budget_options(run_cmd)
    run_cmd.add_argument("name", help="Automation name")
    run_cmd.add_argument("--version", default=None, help="Default: LATEST in this env")
    run_cmd.add_argument("--run-id", default=None, help="Optional run id (default: generated)")
    run_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the execution plan without invoking tools",
    )
    run_cmd.add_argument(
        "--from-handoff",
        default=None,
        help="Seed completed phases from a handoff package, then run remaining recipe phases",
    )
    run_cmd.add_argument(
        "--auto-approve",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Approval policy (default: prod on / recipe force)",
    )
    run_cmd.add_argument(
        "--no-force",
        action="store_true",
        help="Do not force-skip approval gates on recipe invokes",
    )
    run_cmd.add_argument("--actor", default="recipe")
    run_cmd.add_argument(
        "--set",
        action="append",
        default=None,
        dest="set_args",
        help="Override invoke args on every step (repeatable), e.g. headless=false",
    )
    run_cmd.add_argument(
        "--feedback",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Automatic feedback.record policy (default: on in dev, off in prod)",
    )
    run_cmd.set_defaults(handler=_cmd_run)

    # ship: publish + push (+ optional prod dry-run)
    ship = rpa_sub.add_parser(
        "ship",
        help="Publish (auto-distill) + push to prod in one command",
    )
    _add_workspace_options(ship)
    _add_skills_option(ship)
    _add_budget_options(ship)
    ship.add_argument("--name", required=True)
    ship.add_argument("--version", required=True)
    ship.add_argument("--from-run", required=True, dest="from_run")
    ship.add_argument("--goal", default="")
    ship.add_argument("--notes", default="")
    ship.add_argument("--skill", action="append", default=None)
    ship.add_argument("--full-transcript", action="store_true")
    ship.add_argument(
        "--prod-dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="After push, dry-run the recipe in prod (default: true)",
    )
    ship.add_argument(
        "--prod-run",
        action="store_true",
        help="After push, execute the recipe live in prod",
    )
    ship.add_argument(
        "--set",
        action="append",
        default=None,
        dest="set_args",
        help="Arg overrides for optional prod dry-run/run",
    )
    ship.set_defaults(handler=_cmd_ship)

    # secrets
    secrets = rpa_sub.add_parser("secret", help="Project secrets (values never printed)")
    secrets_sub = secrets.add_subparsers(dest="secret_command", required=True)
    sec_list = secrets_sub.add_parser("list", help="List secret names")
    _add_workspace_options(sec_list)
    sec_list.set_defaults(handler=_cmd_secret_list)
    sec_set = secrets_sub.add_parser("set", help="Set a secret value")
    _add_workspace_options(sec_set)
    sec_set.add_argument("name")
    sec_set.add_argument("value")
    sec_set.set_defaults(handler=_cmd_secret_set)
    sec_del = secrets_sub.add_parser("delete", help="Delete a secret")
    _add_workspace_options(sec_del)
    sec_del.add_argument("name")
    sec_del.set_defaults(handler=_cmd_secret_delete)

    autos = rpa_sub.add_parser("automations", help="List versioned automations in this env")
    _add_workspace_options(autos)
    autos.set_defaults(handler=_cmd_automations)

    return parser


def _add_workspace_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--home",
        default=None,
        help="Home root (default: ~/.roi-h or ROI_H_HOME)",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Project name (default: config / ROI_H_PROJECT / sole project)",
    )
    parser.add_argument(
        "--env",
        choices=["dev", "prod"],
        default=None,
        help="Environment override (default: project config / ROI_H_ENV / dev)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Override SQLite path (default: <home>/projects/<project>/<env>/rpa.sqlite)",
    )


def _add_skills_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skills",
        default=None,
        help="Global skills root (default: repo skills/ or ROI_H_SKILLS)",
    )


def _add_budget_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-events", type=int, default=None)
    parser.add_argument("--max-tool-calls", type=int, default=None)
    parser.add_argument("--max-seconds", type=float, default=None)


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    _add_workspace_options(parser)
    _add_skills_option(parser)
    _add_budget_options(parser)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--auto-approve",
        action=argparse.BooleanOptionalAction,
        default=None,
    )


def _workspace(args: argparse.Namespace) -> Workspace:
    return Workspace.open(
        getattr(args, "home", None),
        project=getattr(args, "project", None),
        env=getattr(args, "env", None),
        db=getattr(args, "db", None),
    )


def _budget(args: argparse.Namespace) -> BudgetSpec:
    return BudgetSpec(
        max_events=getattr(args, "max_events", None),
        max_tool_calls=getattr(args, "max_tool_calls", None),
        max_seconds=getattr(args, "max_seconds", None),
    )


def _cmd_project_list(args: argparse.Namespace) -> dict[str, Any]:
    home = resolve_home(args.home)
    items = list_projects(home)
    return {
        "ok": True,
        "home": str(home),
        "active": get_active_project(home),
        "projects": items,
        "count": len(items),
    }


def _cmd_project_show(args: argparse.Namespace) -> dict[str, Any]:
    name = args.name or getattr(args, "project", None)
    if name:
        ws = Workspace.open(args.home, project=name, env=getattr(args, "env", None))
    else:
        ws = _workspace(args)
    return {"ok": True, **ws.to_dict()}


def _cmd_project_create(args: argparse.Namespace) -> dict[str, Any]:
    return create_project(
        args.home,
        args.name,
        display_name=args.display_name,
        set_active=args.use,
        env=args.env,
    )


def _cmd_project_use(args: argparse.Namespace) -> dict[str, Any]:
    return set_active_project(args.home, args.name)


def _cmd_project_init(args: argparse.Namespace) -> dict[str, Any]:
    return init_home(args.home, project=args.project, display_name=args.display_name)


def _cmd_env_show(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    return {"ok": True, **ws.to_dict()}


def _cmd_env_set(args: argparse.Namespace) -> dict[str, Any]:
    data = set_active_env(args.home, args.name, project=getattr(args, "project", None))
    return {"ok": True, **data}


def _cmd_tools(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    catalog = load_skills(
        resolve_skills_root(args.skills),
        shared_root=ws.shared_skills,
        project_root=ws.project_skills,
        database=ws.db,
    )
    tools = [item.model_dump(mode="json") for item in catalog.list_tools()]
    return {
        "ok": True,
        "project": ws.project,
        "env": ws.env,
        "skills_root": str(catalog.global_root),
        "shared_skills_root": str(ws.shared_skills),
        "project_skills_root": str(ws.project_skills),
        "tools": tools,
        "count": len(tools),
        "global_count": sum(1 for t in tools if t.get("scope") == "global"),
        "shared_count": sum(1 for t in tools if t.get("scope") == "shared"),
        "project_count": sum(1 for t in tools if t.get("scope") == "project"),
    }


def _cmd_start(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    run_id = args.run_id or _new_run_id(args.goal)
    _validate_run_id(run_id)
    harness = _open_harness(
        ws,
        run_id=run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=True,
    )
    run = harness.start_run(args.goal, actor=args.actor, phase_plan=args.phases)
    seeded = None
    if args.from_handoff:
        seeded = harness.seed_from_handoff(args.from_handoff)
    return {
        "ok": True,
        "project": ws.project,
        "env": ws.env,
        "run_id": harness.runtime.run_id,
        "db": str(ws.db),
        "project_skills_root": str(ws.project_skills),
        "artifacts_root": str(ws.artifacts / harness.runtime.run_id),
        "phases_root": str(ws.artifacts / harness.runtime.run_id / "phases"),
        "goal": args.goal,
        "object_id": run.id,
        "auto_approve": harness.auto_approve,
        "budget": harness.budget.model_dump(mode="json"),
        "phase_plan": run.data.get("phase_plan") or [],
        "seeded": seeded,
        "next": {
            "phase_begin": f"roi-h rpa phase begin --run-id {run_id} <name>",
            "invoke": f"roi-h rpa invoke --run-id {run_id} <skill> <tool> --args '{{...}}'",
            "phase_end": f"roi-h rpa phase end --run-id {run_id}",
            "status": f"roi-h rpa status --run-id {run_id}",
            "ship": f"roi-h rpa ship --name JOB --version 1.0.0 --from-run {run_id}",
        },
    }


def _cmd_invoke(args: argparse.Namespace) -> dict[str, Any]:
    _validate_run_id(args.run_id)
    ws = _workspace(args)
    harness = _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )
    step = harness.invoke(
        args.skill,
        args.tool,
        _parse_args_json(args.args),
        actor=args.actor,
        force=args.force,
    )
    return {
        "ok": step.status == "ok",
        "project": ws.project,
        "env": ws.env,
        "run_id": step.run_id,
        "step_id": step.step_id,
        "skill": step.skill,
        "tool": step.tool,
        "scope": step.scope,
        "name": f"{step.skill}.{step.tool}",
        "args": step.args,
        "output": step.output,
        "status": step.status,
        "error": step.error,
        "failure": step.failure.model_dump(mode="json") if step.failure else None,
        "approval_id": step.approval_id,
        "phase": step.phase,
        "phase_id": step.phase_id,
        "invocation_id": step.invocation_id,
        "idempotency_key": step.idempotency_key,
        "attempt": step.attempt,
    }


def _cmd_adapt(args: argparse.Namespace) -> dict[str, Any]:
    _validate_run_id(args.run_id)
    ws = _workspace(args)
    harness = _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )
    return harness.adapt(
        args.goal,
        tools=args.adaptive_tools,
        max_turns=args.max_turns,
        actor=args.actor,
    )


def _cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    _validate_run_id(args.run_id)
    ws = _workspace(args)
    harness = _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )
    summary = harness.status()
    summary["ok"] = summary["error_steps"] == 0 and summary["pending_approvals"] == 0
    return summary


def _cmd_ui(args: argparse.Namespace) -> None:
    from roi_h.observer import serve_observer  # noqa: PLC0415

    serve_observer(
        resolve_home(args.home),
        port=args.port,
        open_browser=not args.no_open,
    )


def _cmd_cancel(args: argparse.Namespace) -> dict[str, Any]:
    _validate_run_id(args.run_id)
    ws = _workspace(args)
    harness = _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )
    return harness.cancel(reason=args.reason)


def _cmd_reconcile(args: argparse.Namespace) -> dict[str, Any]:
    _validate_run_id(args.run_id)
    ws = _workspace(args)
    harness = _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )
    return harness.reconcile(repair=args.repair).model_dump(mode="json")


def _cmd_custom(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    source = None
    source_path = None
    if args.script == "-":
        source = sys.stdin.read()
    elif args.script:
        source_path = args.script
    result = define_project_tool(
        skill=args.skill,
        tool=args.tool,
        description=args.description,
        project_root=ws.project_skills,
        source=source,
        source_path=source_path,
        overwrite=args.overwrite,
    )
    result["env"] = ws.env
    return result


def _cmd_approvals(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    harness = _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )
    items = harness.list_approvals(status=args.status)
    return {"ok": True, "run_id": args.run_id, "approvals": items, "count": len(items)}


def _cmd_approve(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    harness = _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )
    step = harness.approve(args.approval_id, approved_by=args.by)
    return {
        "ok": step.status == "ok",
        "run_id": step.run_id,
        "step_id": step.step_id,
        "status": step.status,
        "output": step.output,
        "error": step.error,
        "failure": step.failure.model_dump(mode="json") if step.failure else None,
        "approval_id": step.approval_id,
        "invocation_id": step.invocation_id,
        "idempotency_key": step.idempotency_key,
        "attempt": step.attempt,
    }


def _cmd_artifact_put(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    harness = _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )
    return harness.put_artifact(args.file, name=args.name)


def _cmd_artifact_list(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    harness = _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )
    items = harness.list_artifacts()
    return {"ok": True, "run_id": args.run_id, "artifacts": items, "count": len(items)}


def _cmd_phase_begin(args: argparse.Namespace) -> dict[str, Any]:
    harness = _harness_for_run(args)
    phase = harness.begin_phase(
        args.name,
        description=args.description,
        require_artifacts=args.require_artifacts,
    )
    return {"ok": True, "run_id": args.run_id, "phase": phase}


def _cmd_phase_end(args: argparse.Namespace) -> dict[str, Any]:
    harness = _harness_for_run(args)
    phase = harness.end_phase(
        summary=_parse_args_json(args.summary),
        require_artifacts=args.require_artifacts,
    )
    return {"ok": True, "run_id": args.run_id, "phase": phase}


def _cmd_phase_fail(args: argparse.Namespace) -> dict[str, Any]:
    harness = _harness_for_run(args)
    phase = harness.fail_phase(
        error=args.error,
        summary=_parse_args_json(args.summary),
    )
    return {"ok": True, "run_id": args.run_id, "phase": phase}


def _cmd_phase_skip(args: argparse.Namespace) -> dict[str, Any]:
    harness = _harness_for_run(args)
    phase = harness.skip_phase(args.name, reason=args.reason)
    return {"ok": True, "run_id": args.run_id, "phase": phase}


def _cmd_phase_retry(args: argparse.Namespace) -> dict[str, Any]:
    harness = _harness_for_run(args)
    phase = harness.retry_phase(
        args.name,
        description=args.description,
        require_artifacts=args.require_artifacts,
    )
    return {"ok": True, "run_id": args.run_id, "phase": phase}


def _cmd_phase_list(args: argparse.Namespace) -> dict[str, Any]:
    harness = _harness_for_run(args)
    items = harness.list_phases()
    return {
        "ok": True,
        "run_id": args.run_id,
        "phases": items,
        "count": len(items),
        "current_phase": harness.status().get("current_phase"),
    }


def _cmd_ship(args: argparse.Namespace) -> dict[str, Any]:
    """Publish --from-run (distill) → push → optional prod dry-run/run."""
    if getattr(args, "env", None) is None:
        args.env = "dev"
    ws = _workspace(args)
    budgets = _budget(args).model_dump(mode="json", exclude_none=True)
    return ship_automation(
        ws,
        name=args.name,
        version=args.version,
        from_run=args.from_run,
        goal=args.goal,
        notes=args.notes,
        skills=args.skill,
        budgets=budgets,
        skills_root=args.skills,
        budget=_budget(args),
        distill=not args.full_transcript,
        prod_dry_run=bool(args.prod_dry_run),
        prod_run=bool(args.prod_run),
        set_args=args.set_args,
    )


def _cmd_run(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    collect_feedback = args.feedback if args.feedback is not None else ws.env != "prod"
    return run_automation(
        ws,
        name=args.name,
        version=args.version,
        run_id=args.run_id,
        skills_root=args.skills,
        budget=_budget(args),
        dry_run=args.dry_run,
        from_handoff=args.from_handoff,
        auto_approve=args.auto_approve,
        force=not args.no_force,
        actor=args.actor,
        set_args=getattr(args, "set_args", None),
        collect_feedback=collect_feedback,
    )


def _cmd_project_delete(args: argparse.Namespace) -> dict[str, Any]:
    return delete_project(args.home, args.name, force=args.force)


def _cmd_project_rename(args: argparse.Namespace) -> dict[str, Any]:
    return rename_project(args.home, args.name, args.new_name)


def _cmd_secret_list(args: argparse.Namespace) -> dict[str, Any]:
    return list_secrets(_workspace(args))


def _cmd_secret_set(args: argparse.Namespace) -> dict[str, Any]:
    return set_secret(_workspace(args), args.name, args.value)


def _cmd_secret_delete(args: argparse.Namespace) -> dict[str, Any]:
    return delete_secret(_workspace(args), args.name)


def _harness_for_run(args: argparse.Namespace) -> RunSession:
    _validate_run_id(args.run_id)
    ws = _workspace(args)
    return _open_harness(
        ws,
        run_id=args.run_id,
        skills=args.skills,
        budget=_budget(args),
        auto_approve=args.auto_approve,
        create_if_missing=False,
    )


def _cmd_automations(args: argparse.Namespace) -> dict[str, Any]:
    ws = _workspace(args)
    items = list_automations(ws)
    return {
        "ok": True,
        "project": ws.project,
        "env": ws.env,
        "automations": items,
        "count": len(items),
    }


def _open_harness(
    workspace: Workspace,
    *,
    run_id: str,
    skills: str | None,
    budget: BudgetSpec,
    auto_approve: bool | None,
    create_if_missing: bool,
) -> RunSession:
    workspace.db.parent.mkdir(parents=True, exist_ok=True)
    if workspace.db.exists() and _run_id_exists(workspace.db, run_id):
        return RunSession.reopen(
            workspace,
            run_id=run_id,
            skills_root=skills,
            budget=budget,
            auto_approve=auto_approve,
        )
    if not create_if_missing:
        if not workspace.db.exists():
            msg = f"database does not exist: {workspace.db} (run `roi-h rpa start` first)"
            raise FileNotFoundError(msg)
        msg = f"run_id {run_id!r} not found in {workspace.db}"
        raise FileNotFoundError(msg)
    return RunSession.create(
        workspace,
        run_id=run_id,
        skills_root=skills,
        budget=budget,
        auto_approve=auto_approve,
    )


def _run_id_exists(path: Path, run_id: str) -> bool:
    try:
        runs = SQLiteEventStore.list_runs(str(path))
    except (OSError, RuntimeError, ValueError):
        return False
    return any(getattr(run, "run_id", None) == run_id for run in runs)


def _parse_args_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        msg = f"invalid --args JSON: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(value, dict):
        msg = "--args must be a JSON object"
        raise TypeError(msg)
    return value


def _validate_run_id(run_id: str) -> None:
    validate_run_id(run_id)


def _new_run_id(goal: str) -> str:
    return new_run_id(goal)


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


__all__ = ["default_skills_root", "main"]


if __name__ == "__main__":
    sys.exit(main())
