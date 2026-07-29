"""Typed contract version 1.0 for machine callers."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this type at runtime.
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.0"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


class ContractModel(BaseModel):
    """Strict base for all caller-supplied contract values."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"$schema": JSON_SCHEMA_DIALECT},
    )


class Effect(StrEnum):
    """Operation effect classification."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class Idempotency(StrEnum):
    """External retry rule."""

    NOT_APPLICABLE = "not_applicable"
    SUPPORTED = "supported"
    REQUIRED = "required"


class ExecutionMode(StrEnum):
    """Operation completion mode."""

    SYNC = "sync"
    TASK = "task"


class TaskState(StrEnum):
    """Durable task state."""

    QUEUED = "queued"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    APPROVAL_REQUIRED = "approval_required"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CommandContext(ContractModel):
    """Explicit or resolved operation scope."""

    project: str | None = None
    environment: Literal["dev", "prod"] | None = None
    run_id: str | None = None


class CommandRequest(ContractModel):
    """One strict machine call."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: str | None = None
    idempotency_key: str | None = None
    context: CommandContext = Field(default_factory=CommandContext)
    arguments: dict[str, Any] = Field(default_factory=dict)


class NextAction(ContractModel):
    """One safe operation that can follow a result."""

    operation: str
    reason: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class StructuredError(ContractModel):
    """Stable failure information for machine decisions."""

    code: str
    category: str
    message: str
    retryable: bool
    retry_after_ms: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    remediation: list[NextAction] = Field(default_factory=list)
    diagnostic_id: str | None = None


class CommandResult(ContractModel):
    """Common success or failure envelope."""

    schema_version: Literal["1.0"] = "1.0"
    operation: str
    request_id: str
    ok: bool
    changed: bool
    context: CommandContext = Field(default_factory=CommandContext)
    result: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    error: StructuredError | None = None

    @model_validator(mode="after")
    def _validate_result_shape(self) -> Self:
        if self.ok and self.error is not None:
            msg = "a successful result cannot contain an error"
            raise ValueError(msg)
        if not self.ok and self.error is None:
            msg = "a failed result must contain an error"
            raise ValueError(msg)
        return self


class OperationManifest(ContractModel):
    """Public description of one catalog operation."""

    operation_id: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect: Effect
    idempotency: Idempotency
    approval_rule: str
    plan_rule: str
    secret_input_paths: list[str]
    filesystem_requirements: list[str]
    network_requirements: list[str]
    pagination: bool
    execution_mode: ExecutionMode
    timeout_seconds: int = Field(gt=0)


class Page(ContractModel):
    """Bounded stable page."""

    items: list[dict[str, Any]]
    next_cursor: str | None = None
    has_more: bool = False
    snapshot: str


class OperationTask(ContractModel):
    """Durable execution record for a long command."""

    task_id: str
    operation: str
    request_id: str
    state: TaskState
    created_at: datetime
    updated_at: datetime
    result: CommandResult | None = None


class TaskEvent(ContractModel):
    """One ordered resumable task event."""

    event_id: str
    sequence: int = Field(ge=0)
    timestamp: datetime
    type: str
    task_id: str
    request_id: str
    data: dict[str, Any] = Field(default_factory=dict)


class ProgressEvent(ContractModel):
    """One JSON Lines progress item."""

    schema_version: Literal["1.0"] = "1.0"
    type: str
    request_id: str
    data: dict[str, Any] = Field(default_factory=dict)


class DestructivePlan(ContractModel):
    """Reviewable plan bound to current state."""

    plan_id: str
    operation: str
    arguments: dict[str, Any]
    effects: list[dict[str, Any]]
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    required_approvals: list[dict[str, Any]] = Field(default_factory=list)
    state_digest: str
    expires_at: datetime
    apply_operation: str


ERROR_CODES = frozenset(
    {
        "archive.digest_mismatch",
        "archive.incompatible",
        "archive.invalid",
        "archive.path_unsafe",
        "artifact.file_missing",
        "artifact.identity_conflict",
        "home.schema_unsupported",
        "operation.failed",
        "operation.not_found",
        "operation.contract_violation",
        "package.digest_mismatch",
        "package.not_portable",
        "path.capability_denied",
        "path.escape_denied",
        "path.invalid_logical_path",
        "plan.expired",
        "plan.state_changed",
        "project.active_runs",
        "project.layout_migration_required",
        "project.not_found",
        "request.idempotency_conflict",
        "request.invalid",
        "retention.plan_stale",
        "secret.missing",
        "secret.provider_failed",
        "store.backup_failed",
        "store.integrity_failed",
        "store.locked",
        "store.migration_failed",
        "store.open_failed",
        "store.restore_failed",
        "store.schema_mismatch",
    }
)

__all__ = [
    "CONTRACT_VERSION",
    "ERROR_CODES",
    "JSON_SCHEMA_DIALECT",
    "CommandContext",
    "CommandRequest",
    "CommandResult",
    "DestructivePlan",
    "Effect",
    "ExecutionMode",
    "Idempotency",
    "NextAction",
    "OperationManifest",
    "OperationTask",
    "Page",
    "ProgressEvent",
    "StructuredError",
    "TaskEvent",
    "TaskState",
]
