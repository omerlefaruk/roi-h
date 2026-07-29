"""Common machine dispatcher and error mapping."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from roi_h.agent.catalog import OperationCatalog, build_catalog
from roi_h.agent.contract import (
    CommandContext,
    CommandRequest,
    CommandResult,
    Effect,
    StructuredError,
)
from roi_h.harness.atomicfs import atomic_write_json
from roi_h.harness.workspace import resolve_home

if TYPE_CHECKING:
    from pathlib import Path


class Dispatcher:
    """Describe and execute catalog operations."""

    def __init__(self, catalog: OperationCatalog | None = None) -> None:
        """Bind a catalog."""
        self.catalog = catalog or build_catalog()

    def describe(self, operation_id: str | None = None) -> CommandResult:
        """Return machine-readable operation manifests."""
        request_id = _request_id(None)
        try:
            manifests = self.catalog.describe(operation_id)
        except KeyError as exc:
            return _failure(
                "system.describe",
                request_id,
                "operation.not_found",
                "not_found",
                str(exc.args[0]),
            )
        return CommandResult(
            operation="system.describe",
            request_id=request_id,
            ok=True,
            changed=False,
            result={
                "operations": [item.model_dump(mode="json") for item in manifests],
                "count": len(manifests),
            },
        )

    def execute(self, operation_id: str, request: CommandRequest) -> CommandResult:
        """Validate, route, and wrap one operation."""
        request_id = _request_id(request.request_id)
        request = request.model_copy(update={"request_id": request_id})
        try:
            manifest = self.catalog.describe(operation_id)[0]
            if manifest.idempotency.value == "required" and not request.idempotency_key:
                return _failure(
                    operation_id,
                    request_id,
                    "request.invalid",
                    "invalid_request",
                    "idempotency_key is required for this operation",
                )
            replay = _idempotency_replay(operation_id, request)
            if replay is not None:
                if replay.get("fingerprint") != _request_fingerprint(operation_id, request):
                    return _failure(
                        operation_id,
                        request_id,
                        "request.idempotency_conflict",
                        "conflict",
                        "The idempotency key was already used with different arguments.",
                    )
                return CommandResult(
                    operation=operation_id,
                    request_id=request_id,
                    ok=True,
                    changed=bool(replay["changed"]),
                    context=CommandContext.model_validate(replay["context"]),
                    result=dict(replay["result"]),
                    warnings=["Returned the first result for this idempotency key."],
                )
            result = self.catalog.execute(operation_id, request)
        except KeyError as exc:
            return _failure(
                operation_id,
                request_id,
                "operation.not_found",
                "not_found",
                str(exc.args[0]),
            )
        except (FileNotFoundError, ValueError, TypeError, RuntimeError, OSError) as exc:
            return _mapped_failure(operation_id, request_id, exc)
        response = CommandResult(
            operation=operation_id,
            request_id=request_id,
            ok=True,
            changed=manifest.effect is not Effect.READ,
            context=CommandContext(
                project=request.context.project,
                environment=request.context.environment,
                run_id=request.context.run_id,
            ),
            result=result,
        )
        _save_idempotency(operation_id, request, response)
        return response


def invalid_request_result(
    operation_id: str,
    message: str,
    *,
    request_id: str | None = None,
) -> CommandResult:
    """Create one structured invalid-input result."""
    return _failure(
        operation_id,
        _request_id(request_id),
        "request.invalid",
        "invalid_request",
        message,
    )


def _mapped_failure(
    operation_id: str,
    request_id: str,
    exc: Exception,
) -> CommandResult:
    message = str(exc)
    prefix = message.partition(":")[0]
    if "." in prefix and " " not in prefix:
        code = prefix
    elif isinstance(exc, FileNotFoundError):
        code = "project.not_found" if "project" in message else "operation.failed"
    else:
        code = "operation.failed"
    category = "not_found" if code.endswith("not_found") else "domain"
    return _failure(operation_id, request_id, code, category, message)


def _failure(
    operation_id: str,
    request_id: str,
    code: str,
    category: str,
    message: str,
) -> CommandResult:
    return CommandResult(
        operation=operation_id,
        request_id=request_id,
        ok=False,
        changed=False,
        error=StructuredError(
            code=code,
            category=category,
            message=message,
            retryable=False,
        ),
    )


def _request_id(value: str | None) -> str:
    return value or f"req_{uuid4().hex}"


def _idempotency_replay(
    operation_id: str,
    request: CommandRequest,
) -> dict[str, Any] | None:
    path = _idempotency_path(operation_id, request)
    if path is None or not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def _save_idempotency(
    operation_id: str,
    request: CommandRequest,
    result: CommandResult,
) -> None:
    path = _idempotency_path(operation_id, request)
    if path is None or result.result is None:
        return
    atomic_write_json(
        path,
        {
            "operation": operation_id,
            "fingerprint": _request_fingerprint(operation_id, request),
            "changed": result.changed,
            "context": result.context.model_dump(mode="json"),
            "result": result.result,
        },
        mode=0o600,
    )


def _idempotency_path(operation_id: str, request: CommandRequest) -> Path | None:
    key = request.idempotency_key
    if not key or operation_id == "secret.set":
        return None
    home = resolve_home(request.arguments.get("home"))
    token = hashlib.sha256(key.encode()).hexdigest()
    return home / "runtime" / "agent-idempotency" / f"{token}.json"


def _request_fingerprint(operation_id: str, request: CommandRequest) -> str:
    canonical = json.dumps(
        {
            "operation": operation_id,
            "context": request.context.model_dump(mode="json"),
            "arguments": request.arguments,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


__all__ = ["Dispatcher", "invalid_request_result"]
