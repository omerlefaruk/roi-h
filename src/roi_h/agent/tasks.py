"""Durable background task records for operations that outlive one CLI call."""

from __future__ import annotations

import json
import subprocess
import sys
import time
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
    """Persist task state, input, and ordered events in the selected home."""

    def __init__(self, home: str | Path | None) -> None:
        """Bind one data home without creating it until a task is written."""
        self.home = resolve_home(home)
        self.root = self.home / "runtime" / "agent-tasks"

    def begin(
        self,
        operation: str,
        request_id: str,
        *,
        request: dict[str, Any],
    ) -> OperationTask:
        """Create a queued task and persist the worker request."""
        now = datetime.now(UTC)
        task = OperationTask(
            task_id=f"task_{uuid4().hex}",
            operation=operation,
            request_id=request_id,
            state=TaskState.QUEUED,
            created_at=now,
            updated_at=now,
        )
        self._write(task, [self._event(task, 1, "task.queued", {})], request=request)
        return task

    def launch(self, task: OperationTask) -> None:
        """Start the detached worker for one queued task."""
        command = [
            sys.executable,
            "-m",
            "roi_h.agent.task_worker",
            "--home",
            str(self.home),
            "--task-id",
            task.task_id,
        ]
        options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform == "win32":
            options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            options["start_new_session"] = True
        subprocess.Popen(command, **options)  # noqa: S603

    def mark_working(self, task_id: str) -> OperationTask:
        """Move a queued task to working and append its event."""
        task = self.show(task_id)
        if task.state != TaskState.QUEUED:
            return task
        task = task.model_copy(update={"state": TaskState.WORKING, "updated_at": _now()})
        events = self._read_events(task_id)
        events.append(self._event(task, len(events) + 1, "task.working", {}))
        self._write(task, events)
        return task

    def succeed(self, task: OperationTask, result: CommandResult) -> OperationTask:
        """Set a successful terminal result unless the task was cancelled."""
        current = self.show(task.task_id)
        if current.state == TaskState.CANCELLED:
            return current
        task = current.model_copy(
            update={
                "state": TaskState.SUCCEEDED,
                "updated_at": _now(),
                "result": result,
            }
        )
        events = self._read_events(task.task_id)
        events.append(self._event(task, len(events) + 1, "task.succeeded", {}))
        self._write(task, events)
        return task

    def fail(self, task: OperationTask, result: CommandResult) -> OperationTask:
        """Set a failed terminal result unless the task was cancelled."""
        current = self.show(task.task_id)
        if current.state == TaskState.CANCELLED:
            return current
        task = current.model_copy(
            update={
                "state": TaskState.FAILED,
                "updated_at": _now(),
                "result": result,
            }
        )
        events = self._read_events(task.task_id)
        events.append(self._event(task, len(events) + 1, "task.failed", {}))
        self._write(task, events)
        return task

    def show(self, task_id: str) -> OperationTask:
        """Load one task without changing its state."""
        raw = self._read(task_id)
        return OperationTask.model_validate(raw["task"])

    def request(self, task_id: str) -> dict[str, Any]:
        """Load the private worker request for one task."""
        raw = self._read(task_id)
        value = raw.get("request")
        return value if isinstance(value, dict) else {}

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

    def wait(self, task_id: str, timeout_seconds: float) -> OperationTask:
        """Wait for a task to finish, then return its current state."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            task = self.show(task_id)
            if task.state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}:
                return task
            if time.monotonic() >= deadline:
                return task
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def cancel(self, task_id: str) -> OperationTask:
        """Cancel a queued task; a working task is allowed to finish safely."""
        task = self.show(task_id)
        if task.state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}:
            return task
        if task.state == TaskState.WORKING:
            return task
        task = task.model_copy(update={"state": TaskState.CANCELLED, "updated_at": _now()})
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
            timestamp=_now(),
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

    def _write(
        self,
        task: OperationTask,
        events: list[TaskEvent],
        *,
        request: dict[str, Any] | None = None,
    ) -> None:
        existing = self._read(task.task_id) if self._path(task.task_id).is_file() else {}
        atomic_write_json(
            self._path(task.task_id),
            {
                "schema_version": 1,
                "task": task.model_dump(mode="json"),
                "events": [item.model_dump(mode="json") for item in events],
                "request": request if request is not None else existing.get("request", {}),
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
    """Wait for or poll one task."""
    timeout = request.arguments.get("timeout_seconds", 0)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
        message = "timeout_seconds must be a non-negative number"
        raise ValueError(message)
    return (
        TaskStore(request.arguments.get("home"))
        .wait(
            _task_id(request),
            float(timeout),
        )
        .model_dump(mode="json")
    )


def task_cancel(request: CommandRequest) -> dict[str, Any]:
    """Cancel one queued task."""
    return (
        TaskStore(request.arguments.get("home")).cancel(_task_id(request)).model_dump(mode="json")
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


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "TaskStore",
    "task_cancel",
    "task_events",
    "task_list",
    "task_show",
    "task_wait",
]
