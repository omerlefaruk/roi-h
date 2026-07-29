"""Stable external-AI command interface."""

from roi_h.agent.contract import (
    CONTRACT_VERSION,
    CommandRequest,
    CommandResult,
    DestructivePlan,
    Effect,
    Idempotency,
    NextAction,
    OperationManifest,
    OperationTask,
    Page,
    StructuredError,
    TaskEvent,
)

__all__ = [
    "CONTRACT_VERSION",
    "CommandRequest",
    "CommandResult",
    "DestructivePlan",
    "Effect",
    "Idempotency",
    "NextAction",
    "OperationManifest",
    "OperationTask",
    "Page",
    "StructuredError",
    "TaskEvent",
]
