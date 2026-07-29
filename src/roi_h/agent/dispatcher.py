"""Common machine dispatcher and error mapping."""

from __future__ import annotations

from uuid import uuid4

from roi_h.agent.catalog import OperationCatalog, build_catalog
from roi_h.agent.contract import (
    CommandContext,
    CommandRequest,
    CommandResult,
    Effect,
    StructuredError,
)


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
        try:
            manifest = self.catalog.describe(operation_id)[0]
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
        return CommandResult(
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


__all__ = ["Dispatcher", "invalid_request_result"]
