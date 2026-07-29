"""Harness domain types shared with skills and tests."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SkillScope = Literal["global", "shared", "project"]
StepStatus = Literal["ok", "error", "pending_approval"]
ToolEffect = Literal["read", "write", "destructive"]
IdempotencyMode = Literal["none", "key", "reconcile"]
InvocationStatus = Literal[
    "scheduled",
    "running",
    "succeeded",
    "failed",
    "outcome_unknown",
]
FailureKind = Literal[
    "validation",
    "approval",
    "budget",
    "timeout",
    "transient_external",
    "permanent_external",
    "internal",
    "unknown",
]
ReconciliationSeverity = Literal["warning", "error"]
EnvName = Literal["dev", "prod"]
PhaseStatus = Literal["open", "done", "failed", "skipped"]
PhaseRole = Literal["explore", "work", "verify"]

_PHASE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_EXPLORATION_PHASE_NAMES = frozenset(
    {
        "explore",
        "discovery",
        "discover",
        "probe",
        "debug",
        "analyze",
        "analysis",
        "research",
        "recon",
        "map",
        "scratch",
        "investigate",
    }
)
_VERIFY_PHASE_NAMES = frozenset({"verify", "check", "assert", "validate"})


class ToolInfo(BaseModel):
    """A catalog entry the external AI can list without reading Python sources."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Canonical tool name, skill.tool_id")
    skill: str
    tool_id: str
    description: str
    scope: SkillScope = "global"
    requires_approval: bool = False
    deterministic: bool = False
    effect: ToolEffect = "read"
    idempotency: IdempotencyMode = "none"
    allow_in_prod: bool = True
    timeout_seconds: float = Field(default=120.0, gt=0)
    secret_names: list[str] = Field(default_factory=list)
    network_hosts: list[str] = Field(default_factory=list)
    filesystem_roots: list[str] = Field(default_factory=list)
    script_path: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class StepResult(BaseModel):
    """One durable tool invocation recorded on the run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str
    run_id: str
    skill: str
    tool: str
    scope: SkillScope = "global"
    args: dict[str, Any]
    output: dict[str, Any]
    status: StepStatus
    error: str | None = None
    failure: ExecutionFailure | None = None
    approval_id: str | None = None
    phase: str | None = None
    phase_id: str | None = None
    invocation_id: str
    idempotency_key: str
    attempt: int = Field(default=1, ge=1)


class ExecutionFailure(BaseModel):
    """Structured tool failure with a concise operator-facing message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FailureKind
    message: str
    exception_type: str | None = None
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class InvocationIdentity(BaseModel):
    """Logical invocation identity, stable across retry attempts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: str
    idempotency_key: str
    attempt: int = Field(default=1, ge=1)

    @classmethod
    def fresh(cls, run_id: str) -> InvocationIdentity:
        token = uuid.uuid4().hex
        invocation_id = f"inv_{token[:24]}"
        return cls(
            invocation_id=invocation_id,
            idempotency_key=f"roi-h:{run_id}:{invocation_id}",
        )

    def for_attempt(self, attempt: int) -> InvocationIdentity:
        return self.model_copy(update={"attempt": attempt})


class ReconciliationIssue(BaseModel):
    """One graph/filesystem consistency finding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    severity: ReconciliationSeverity
    message: str
    object_id: str | None = None
    path: str | None = None
    repaired: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ReconciliationReport(BaseModel):
    """Typed result for a run reconciliation pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    repair_requested: bool
    ok: bool
    artifacts_scanned: int
    phases_scanned: int
    repairs: int
    issues: list[ReconciliationIssue] = Field(default_factory=list)


class BudgetSpec(BaseModel):
    """Operator-facing run limits (mapped into ActiveGraph + harness checks)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_events: int | None = None
    max_tool_calls: int | None = None
    max_seconds: float | None = None

    def to_activegraph_limits(self) -> dict[str, float | int]:
        """Convert to ActiveGraph ``Budget`` limits dict (omit unset fields)."""
        limits: dict[str, float | int] = {}
        if self.max_events is not None:
            limits["max_events"] = self.max_events
        if self.max_tool_calls is not None:
            limits["max_tool_calls"] = self.max_tool_calls
        if self.max_seconds is not None:
            limits["max_seconds"] = self.max_seconds
        return limits


class PhasePlanEntry(BaseModel):
    """Declared phase in a run or published automation plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""
    role: PhaseRole = "work"
    require_artifacts: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return validate_phase_name(value)


class HandoffManifest(BaseModel):
    """Contract written when a phase ends — the unit of restart and fixture reuse."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: str
    phase_id: str
    run_id: str
    index: int
    status: PhaseStatus
    artifacts: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    require_artifacts: list[str] = Field(default_factory=list)
    source_run_id: str | None = None


class PhaseInfo(BaseModel):
    """Operator-facing phase snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    phase_id: str
    name: str
    index: int
    status: PhaseStatus
    description: str = ""
    role: PhaseRole = "work"
    require_artifacts: list[str] = Field(default_factory=list)
    artifact_names: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    handoff_path: str | None = None
    end_event_id: str | None = None
    step_count: int = 0
    ok_steps: int = 0
    error_steps: int = 0


# --- Deterministic automation recipes (prod runner) ---

RecipeAction = Literal["invoke", "artifact"]


class RecipeStep(BaseModel):
    """One fixed step in a phase recipe."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(description="Stable step id for goto / templates")
    action: RecipeAction = "invoke"
    skill: str | None = None
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    # artifact action: store a file under the run
    name: str | None = Field(default=None, description="Artifact name for action=artifact")
    source: str | None = Field(
        default=None,
        description="Path or template for action=artifact, e.g. {{last.output.path}}",
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not re.match(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$", value):
            msg = f"invalid recipe step id: {value!r}"
            raise ValueError(msg)
        return value


class RecipePhase(BaseModel):
    """One phase in a frozen automation recipe."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""
    role: PhaseRole = "work"
    require_artifacts: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    steps: list[RecipeStep] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return validate_phase_name(value)


class Recipe(BaseModel):
    """Closed, versioned control flow for ``roi-h rpa run``.

    Built in dev by exporting a successful run (or hand-authored JSON).
    Prod executes this without an external AI choosing tools.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str = "0.0.0"
    goal: str = ""
    phases: list[RecipePhase] = Field(default_factory=list)
    budgets: dict[str, Any] = Field(default_factory=dict)
    source_run_id: str | None = None
    notes: str = ""

    def phase_plan_entries(self) -> list[PhasePlanEntry]:
        """Project recipe phases into a start_run phase plan."""
        return [
            PhasePlanEntry(
                name=phase.name,
                description=phase.description,
                role=phase.role,
                require_artifacts=list(phase.require_artifacts),
            )
            for phase in self.phases
        ]


def validate_phase_name(name: str) -> str:
    """Validate a phase name slug."""
    if not _PHASE_NAME_RE.match(name):
        msg = (
            "phase name must start with a letter and contain only "
            "letters, digits, '_' or '-' (max 64 chars)"
        )
        raise ValueError(msg)
    return name


def infer_phase_role(name: str, explicit: PhaseRole | None = None) -> PhaseRole:
    """Infer phase role from an explicit value or conventional name."""
    if explicit is not None:
        return explicit
    key = name.strip().lower()
    if key in _EXPLORATION_PHASE_NAMES:
        return "explore"
    if key in _VERIFY_PHASE_NAMES:
        return "verify"
    return "work"


def parse_phase_plan(
    entries: Sequence[str | PhasePlanEntry | dict[str, Any]] | None,
) -> list[PhasePlanEntry]:
    """Normalize CLI/API phase plan entries into ``PhasePlanEntry`` values.

    Accepted forms:
    - ``name``
    - ``name:description``
    - ``name:role=explore``
    - ``name:role=work:download portal``
    - ``name:role=verify``
    """
    if not entries:
        return []
    result: list[PhasePlanEntry] = []
    for item in entries:
        if isinstance(item, PhasePlanEntry):
            role = infer_phase_role(item.name, item.role)
            result.append(item.model_copy(update={"role": role}))
            continue
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            role_raw = item.get("role")
            role = infer_phase_role(
                name,
                role_raw if role_raw in {"explore", "work", "verify"} else None,
            )
            result.append(
                PhasePlanEntry(
                    name=name,
                    description=str(item.get("description") or ""),
                    role=role,
                    require_artifacts=list(item.get("require_artifacts") or []),
                )
            )
            continue
        text = str(item).strip()
        if not text:
            continue
        result.append(_parse_phase_token(text))
    return result


def _parse_phase_token(text: str) -> PhasePlanEntry:
    """Parse ``name``, ``name:desc``, or ``name:role=explore[:desc]``."""
    if ":" not in text or text.startswith("http"):
        name = text
        return PhasePlanEntry(name=name, role=infer_phase_role(name))

    name, _, rest = text.partition(":")
    name = name.strip()
    rest = rest.strip()
    role: PhaseRole | None = None
    description = rest

    if rest.startswith("role="):
        role_part, _, desc_part = rest.partition(":")
        role_value = role_part.removeprefix("role=").strip().lower()
        if role_value not in {"explore", "work", "verify"}:
            msg = f"invalid phase role {role_value!r}; use explore|work|verify"
            raise ValueError(msg)
        role = role_value  # type: ignore[assignment]
        description = desc_part.strip()
    elif rest in {"explore", "work", "verify"}:
        # allow name:explore shorthand
        role = rest  # type: ignore[assignment]
        description = ""

    return PhasePlanEntry(
        name=name,
        description=description,
        role=infer_phase_role(name, role),
    )
