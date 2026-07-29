"""Durable home-level task records for long command execution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from roi_h.agent.contract import (
    CommandResult,
    OperationTask,
    TaskEvent,
    TaskState,
)
from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.workspace import resolve_home

if TYPE_CHECKING:
    from pathlib import Path

    from roi_h.agent.contract import CommandRequest


class TaskStore:
    """Persist task state and ordered events outside project stores."""

    def __init__(self, home: str | Path | None) -> None:
        """Bind one data home."""
        self.home = resolve_home(home)
        self.root = self.home / "runtime" / "agent-tasks"

    def begin(self, operation: str, request_id: str) -> OperationTask:
        """Create a queued task and move it to working."""
        now = datetime.now(UTC)
        task = OperationTask(
            task_id=f"task_{uuid4().hex}",
            operation=operation,
            request_id=request_id,
            state=TaskState.QUEUED,
            created_at=now,
            updated_at=now,
        )
        events = [
            self._event(task, 1, "task.queued", {}),
            self._event(task, 2, "task.working", {}),
        ]
        task = task.model_copy(
            update={"state": TaskState.WORKING, "updated_at": datetime.now(UTC)}
        )
        self._write(task, events)
        return task

    def succeed(self, task: OperationTask, result: CommandResult) -> OperationTask:
        """Set a successful terminal result."""
        task = task.model_copy(
            update={
                "state": TaskState.SUCCEEDED,
                "updated_at": datetime.now(UTC),
                "result": result,
            }
        )
        events = self._read_events(task.task_id)
        events.append(self._event(task, len(events) + 1, "task.succeeded", {}))
        self._write(task, events)
        return task

    def show(self, task_id: str) -> OperationTask:
        """Load one task."""
        raw = self._read(task_id)
        return OperationTask.model_validate(raw["task"])

    def list_tasks(self) -> list[OperationTask]:
        """List tasks newest first."""
        if not self.root.is_dir():
            return []
        tasks = [self.show(path.stem) for path in self.root.glob("task_*.json")]
        return sorted(tasks, key=lambda item: item.updated_at, reverse=True)

    def events(
        self,
        task_id: str,
        *,
        after: str | None,
        limit: int,
    ) -> dict[str, Any]:
        """Read a bounded event page after one opaque event ID."""
        events = self._read_events(task_id)
        sequence = _event_sequence(after)
        remaining = [item for item in events if item.sequence > sequence]
        selected = remaining[:limit]
        has_more = len(remaining) > limit
        return {
            "task_id": task_id,
            "items": [item.model_dump(mode="json") for item in selected],
            "next_cursor": selected[-1].event_id if has_more and selected else None,
            "has_more": has_more,
            "snapshot": f"task-event:{events[-1].sequence if events else 0}",
        }

    def cancel(self, task_id: str) -> OperationTask:
        """Cancel a nonterminal task."""
        task = self.show(task_id)
        if task.state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}:
            return task
        task = task.model_copy(
            update={"state": TaskState.CANCELLED, "updated_at": datetime.now(UTC)}
        )
        events = self._read_events(task_id)
        events.append(self._event(task, len(events) + 1, "task.cancelled", {}))
        self._write(task, events)
        return task

    def _event(
        self,
        task: OperationTask,
        sequence: int,
        event_type: str,
        data: dict[str, Any],
    ) -> TaskEvent:
        return TaskEvent(
            event_id=f"event:{sequence}",
            sequence=sequence,
            timestamp=datetime.now(UTC),
            type=event_type,
            task_id=task.task_id,
            request_id=task.request_id,
            data=data,
        )

    def _read(self, task_id: str) -> dict[str, Any]:
        path = self._path(task_id)
        if not path.is_file():
            msg = f"task not found: {task_id}"
            raise FileNotFoundError(msg)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            msg = f"task record is invalid: {task_id}"
            raise TypeError(msg)
        return raw

    def _read_events(self, task_id: str) -> list[TaskEvent]:
        return [TaskEvent.model_validate(item) for item in self._read(task_id)["events"]]

    def _write(self, task: OperationTask, events: list[TaskEvent]) -> None:
        atomic_write_json(
            self._path(task.task_id),
            {
                "task": task.model_dump(mode="json"),
                "events": [item.model_dump(mode="json") for item in events],
            },
            mode=0o600,
        )

    def _path(self, task_id: str) -> Path:
        if not task_id.startswith("task_") or not task_id.removeprefix("task_").isalnum():
            msg = f"invalid task ID: {task_id}"
            raise ValueError(msg)
        return self.root / f"{task_id}.json"


def task_list(request: CommandRequest) -> dict[str, Any]:
    """List durable tasks."""
    items = TaskStore(request.arguments.get("home")).list_tasks()
    limit = _limit(request)
    return {
        "items": [item.model_dump(mode="json") for item in items[:limit]],
        "next_cursor": None,
        "has_more": len(items) > limit,
        "snapshot": f"tasks:{len(items)}",
    }


def task_show(request: CommandRequest) -> dict[str, Any]:
    """Show one durable task."""
    return TaskStore(request.arguments.get("home")).show(_task_id(request)).model_dump(mode="json")


def task_events(request: CommandRequest) -> dict[str, Any]:
    """Read resumable task events."""
    return TaskStore(request.arguments.get("home")).events(
        _task_id(request),
        after=request.arguments.get("after"),
        limit=_limit(request),
    )


def task_wait(request: CommandRequest) -> dict[str, Any]:
    """Return current task state; callers can repeat after timeout."""
    return task_show(request)


def task_cancel(request: CommandRequest) -> dict[str, Any]:
    """Cancel one nonterminal task."""
    return (
        TaskStore(request.arguments.get("home"))
        .cancel(_task_id(request))
        .model_dump(mode="json")
    )


def _task_id(request: CommandRequest) -> str:
    value = request.arguments.get("task_id")
    if not isinstance(value, str) or not value:
        msg = "task_id is required"
        raise ValueError(msg)
    return value


def _limit(request: CommandRequest) -> int:
    value = request.arguments.get("limit", 50)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        msg = "limit must be a positive integer"
        raise ValueError(msg)
    return min(value, 200)


def _event_sequence(value: str | None) -> int:
    if value is None:
        return 0
    if not value.startswith("event:"):
        msg = "task event ID is invalid"
        raise ValueError(msg)
    return int(value.removeprefix("event:"))


__all__ = [
    "TaskStore",
    "task_cancel",
    "task_events",
    "task_list",
    "task_show",
    "task_wait",
]
