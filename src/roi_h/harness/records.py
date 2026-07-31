"""Typed graph payloads for durable ``rpa.*`` objects.

These models are the seam for ActiveGraph object data. Mutators build payloads
through them so field layout lives in one place (not free dicts at call sites).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from roi_h.harness.domain import (
    ExecutionFailure,
    IdempotencyMode,
    InvocationStatus,
    PhaseRole,
    PhaseStatus,
    SkillScope,
    StepStatus,
    ToolEffect,
)

ApprovalStatus = Literal["pending", "granted", "denied"]


class RunRecord(BaseModel):
    """Payload for ``rpa.run`` graph objects."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    status: str = "open"
    actor: str = "ai"
    env: str = "dev"
    current_phase_id: str | None = None
    current_phase: str | None = None
    phase_plan: list[dict[str, Any]] = Field(default_factory=list)
    seeded_from: str | None = None
    automation_name: str | None = None
    automation_version: str | None = None
    package_digest: str | None = None
    cancel_reason: str | None = None
    completed_at: str | None = None

    def to_graph(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PhaseRecord(BaseModel):
    """Payload for ``rpa.phase`` graph objects."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    name: str
    index: int
    status: PhaseStatus = "open"
    description: str = ""
    role: PhaseRole = "work"
    require_artifacts: list[str] = Field(default_factory=list)
    artifact_names: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    handoff_path: str | None = None
    end_event_id: str | None = None
    seeded: bool = False
    source_run_id: str | None = None
    source_phase_id: str | None = None

    def to_graph(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        if not self.seeded:
            data.pop("seeded", None)
            data.pop("source_run_id", None)
            data.pop("source_phase_id", None)
        return data


class StepRecord(BaseModel):
    """Payload for ``rpa.step`` graph objects."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    skill: str
    tool: str
    name: str
    scope: SkillScope = "global"
    args: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus
    error: str | None = None
    failure: ExecutionFailure | None = None
    approval_id: str | None = None
    phase: str | None = None
    phase_id: str | None = None
    invocation_id: str
    idempotency_key: str
    attempt: int = 1
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None

    def to_graph(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class InvocationRecord(BaseModel):
    """ActiveGraph-owned lifecycle record for one external tool attempt."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    invocation_id: str
    idempotency_key: str
    attempt: int = 1
    skill: str
    tool: str
    name: str
    scope: SkillScope = "global"
    args: dict[str, Any] = Field(default_factory=dict)
    actor: str = "ai"
    status: InvocationStatus = "scheduled"
    effect: ToolEffect = "read"
    idempotency: IdempotencyMode = "none"
    filesystem_grants: list[str] = Field(default_factory=list)
    approval_id: str | None = None
    phase: str | None = None
    phase_id: str | None = None
    step_id: str | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None

    def to_graph(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ApprovalRecord(BaseModel):
    """Payload for ``rpa.approval`` graph objects."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str
    run_id: str
    skill: str
    tool: str
    name: str
    scope: SkillScope = "global"
    args: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = "pending"
    requested_by: str = "ai"
    reason: str = ""
    approved_by: str | None = None
    phase: str | None = None
    phase_id: str | None = None
    invocation_id: str
    idempotency_key: str
    attempt: int = 1

    def to_graph(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        if data.get("approved_by") is None:
            data.pop("approved_by", None)
        return data


class ArtifactRecord(BaseModel):
    """Payload for ``rpa.artifact`` graph objects."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    run_id: str
    name: str
    uri: str
    bytes: int
    sha256: str
    media_type: str = "application/octet-stream"
    source: str = ""
    created_at: str | None = None
    phase: str | None = None
    phase_id: str | None = None
    seeded: bool = False

    def to_graph(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        if not self.seeded:
            data.pop("seeded", None)
        return data


__all__ = [
    "ApprovalRecord",
    "ApprovalStatus",
    "ArtifactRecord",
    "InvocationRecord",
    "PhaseRecord",
    "RunRecord",
    "StepRecord",
]
