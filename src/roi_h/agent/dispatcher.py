"""Common machine dispatcher and error mapping."""

from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

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
    from collections.abc import Mapping
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

    def execute(  # noqa: PLR0911 - contract failures return at their detection point.
        self,
        operation_id: str,
        request: CommandRequest,
    ) -> CommandResult:
        """Validate, route, and wrap one operation."""
        request_id = _request_id(request.request_id)
        request = request.model_copy(update={"request_id": request_id})
        try:
            definition = self.catalog.definition(operation_id)
            manifest = definition.manifest
            if manifest.idempotency.value == "required" and not request.idempotency_key:
                return _failure(
                    operation_id,
                    request_id,
                    "request.invalid",
                    "invalid_request",
                    "idempotency_key is required for this operation",
                )
            invalid_input = _model_failure(
                operation_id,
                request_id,
                definition.input_model,
                request.arguments,
                code="request.invalid",
                category="invalid_request",
                root="arguments",
            )
            if invalid_input is not None:
                return invalid_input
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
            claim = _claim_idempotency(operation_id, request)
            if claim is not None:
                if claim.get("fingerprint") != _request_fingerprint(operation_id, request):
                    return _failure(
                        operation_id,
                        request_id,
                        "request.idempotency_conflict",
                        "conflict",
                        "The idempotency key was already used with different arguments.",
                    )
                return _failure(
                    operation_id,
                    request_id,
                    "request.in_progress",
                    "conflict",
                    "The first request is still running or has an unknown outcome.",
                    retryable=True,
                    retry_after_ms=500,
                )
            result = self.catalog.execute(operation_id, request)
            invalid_output = _model_failure(
                operation_id,
                request_id,
                definition.output_model,
                result,
                code="operation.contract_violation",
                category="internal",
                root="result",
            )
            if invalid_output is not None:
                _release_idempotency(operation_id, request)
                return invalid_output
        except KeyError as exc:
            _release_idempotency(operation_id, request)
            return _failure(
                operation_id,
                request_id,
                "operation.not_found",
                "not_found",
                str(exc.args[0]),
            )
        except (FileNotFoundError, ValueError, TypeError, RuntimeError, OSError) as exc:
            _release_idempotency(operation_id, request)
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
    elif isinstance(exc, PermissionError) and "ROI-H cannot write to data home " in message:
        code = "home.access_denied"
    elif isinstance(exc, FileNotFoundError):
        code = "project.not_found" if "project" in message else "operation.failed"
    else:
        code = "operation.failed"
    category = (
        "permission"
        if code == "home.access_denied"
        else "not_found"
        if code.endswith("not_found")
        else "domain"
    )
    return _failure(operation_id, request_id, code, category, message)


def _failure(  # noqa: PLR0913 - error contract fields stay explicit.
    operation_id: str,
    request_id: str,
    code: str,
    category: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
    retry_after_ms: int | None = None,
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
            retryable=retryable,
            retry_after_ms=retry_after_ms,
            details=details or {},
        ),
    )


def _model_failure(  # noqa: PLR0913 - model context fields stay explicit.
    operation_id: str,
    request_id: str,
    model: type[BaseModel],
    value: object,
    *,
    code: str,
    category: str,
    root: str,
) -> CommandResult | None:
    try:
        model.model_validate(value)
    except ValidationError as exc:
        errors = sorted(
            exc.errors(include_url=False, include_context=False, include_input=False),
            key=_error_location,
        )
        error = errors[0]
        location = _error_location(error)
        suffix = ".".join(location)
        path = f"{root}.{suffix}" if suffix else root
        error_type = str(error["type"])
        validator = (
            _literal_validator(model, location)
            if error_type == "literal_error"
            else {
                "extra_forbidden": "additionalProperties",
                "greater_than_equal": "minimum",
                "less_than_equal": "maximum",
                "missing": "required",
                "value_error": "anyOf",
            }.get(error_type, "type" if error_type.endswith("_type") else error_type)
        )
        return _failure(
            operation_id,
            request_id,
            code,
            category,
            str(error["msg"]),
            details={"path": path, "validator": validator},
        )
    return None


def _literal_validator(model: type[BaseModel], location: list[str]) -> str:
    schema: object = model.model_json_schema()
    for part in location:
        if not isinstance(schema, dict):
            break
        properties = schema.get("properties")
        schema = properties.get(part, {}) if isinstance(properties, dict) else {}
    return "const" if isinstance(schema, dict) and "const" in schema else "enum"


def _error_location(error: Mapping[str, object]) -> list[str]:
    raw_location = error.get("loc", ())
    location = [str(part) for part in raw_location] if isinstance(raw_location, tuple) else []
    if error.get("type") in {"extra_forbidden", "missing"}:
        return location[:-1]
    return location


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


def _claim_idempotency(
    operation_id: str,
    request: CommandRequest,
) -> dict[str, Any] | None:
    """Claim one effect before execution so a crash cannot silently replay it."""
    path = _idempotency_path(operation_id, request)
    if path is None:
        return None
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fingerprint = _request_fingerprint(operation_id, request)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            raw = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"fingerprint": fingerprint}
        return raw if isinstance(raw, dict) else {"fingerprint": fingerprint}
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "operation": operation_id,
                "fingerprint": fingerprint,
                "request_id": request.request_id,
                "pid": os.getpid(),
            },
            stream,
            sort_keys=True,
        )
        stream.flush()
        os.fsync(stream.fileno())
    return None


def _release_idempotency(operation_id: str, request: CommandRequest) -> None:
    path = _idempotency_path(operation_id, request)
    if path is not None:
        path.with_suffix(".lock").unlink(missing_ok=True)


def _save_idempotency(
    operation_id: str,
    request: CommandRequest,
    result: CommandResult,
) -> None:
    path = _idempotency_path(operation_id, request)
    if path is None:
        return
    if result.result is not None:
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
    _release_idempotency(operation_id, request)


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
