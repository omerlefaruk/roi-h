"""Human CLI adapter for the stable typed operation catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from roi_h.agent.contract import CommandContext, CommandRequest

if TYPE_CHECKING:
    from argparse import Namespace
from roi_h.agent.dispatcher import Dispatcher


def call_operation(
    args: Namespace,
    operation: str,
    **values: object,
) -> dict[str, Any]:
    """Call one typed operation and convert failures to human CLI errors."""
    request = CommandRequest(
        idempotency_key=getattr(args, "idempotency_key", None) or f"human:{uuid4().hex}",
        context=CommandContext(
            project=getattr(args, "project", None),
            environment=getattr(args, "env", None),
            run_id=cast("str | None", values.get("run_id")),
        ),
        arguments=_operation_arguments(args, **values),
    )
    response = Dispatcher().execute(operation, request)
    if not response.ok:
        error = response.error
        message = (
            f"{error.code}: {error.message}"
            if error is not None
            else "operation.failed: operation failed without an error"
        )
        raise RuntimeError(message)
    return {"ok": True, **(response.result or {})}


def _operation_arguments(args: Namespace, **values: object) -> dict[str, Any]:
    arguments = {
        "home": getattr(args, "home", None),
        "project": getattr(args, "project", None),
        "environment": getattr(args, "env", None),
        "skills": getattr(args, "skills", None),
        **values,
    }
    return {key: value for key, value in arguments.items() if value is not None}


__all__ = ["call_operation"]
