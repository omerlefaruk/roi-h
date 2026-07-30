"""Detached worker for durable agent tasks."""

from __future__ import annotations

import argparse

from roi_h.agent.contract import CommandContext, CommandResult, StructuredError
from roi_h.agent.tasks import TaskStore
from roi_h.harness.store_lifecycle import StoreLifecycle
from roi_h.harness.workspace import Workspace


def main(argv: list[str] | None = None) -> int:
    """Execute one persisted task and record its terminal result."""
    parser = argparse.ArgumentParser(prog="roi-h-task-worker")
    parser.add_argument("--home", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    tasks = TaskStore(args.home)
    task = tasks.mark_working(args.task_id)
    if task.state.name == "CANCELLED":
        return 0
    try:
        result = _run_task(tasks, task.task_id)
    except Exception as exc:  # noqa: BLE001 - the task must record every failure.
        result = CommandResult(
            operation=task.operation,
            request_id=task.request_id,
            ok=False,
            changed=False,
            context=CommandContext(),
            error=StructuredError(
                code="operation.failed",
                category="domain",
                message=f"{type(exc).__name__}: {exc}",
                retryable=False,
            ),
        )
        tasks.fail(task, result)
        return 1
    tasks.succeed(task, result)
    return 0


def _run_task(tasks: TaskStore, task_id: str) -> CommandResult:
    task = tasks.show(task_id)
    request = tasks.request(task_id)
    arguments = request.get("arguments")
    context = request.get("context")
    if not isinstance(arguments, dict) or not isinstance(context, dict):
        message = "task request is invalid"
        raise TypeError(message)
    workspace = Workspace.open(
        arguments.get("home"),
        project=context.get("project") or arguments.get("project"),
        env=context.get("environment") or arguments.get("environment"),
        db=arguments.get("db"),
    )
    backup = StoreLifecycle().backup(workspace, str(arguments.get("output") or ""))
    data = backup.to_dict()
    safe = {key: value for key, value in data.items() if key not in {"path", "manifest_path"}}
    return CommandResult(
        operation=task.operation,
        request_id=task.request_id,
        ok=True,
        changed=True,
        context=CommandContext(project=workspace.project, environment=workspace.env),
        result=safe,
    )


if __name__ == "__main__":
    raise SystemExit(main())
