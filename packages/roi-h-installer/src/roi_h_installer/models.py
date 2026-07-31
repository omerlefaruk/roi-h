"""Typed values returned by the installer interface."""

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


class ContractModel(BaseModel):
    """Base for strict, immutable installer contract values."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PointerState(StrEnum):
    """State of the active-version pointer."""

    MISSING = "missing"
    ACTIVE = "active"
    BROKEN = "broken"


class DataState(StrEnum):
    """State of the user data home."""

    MISSING = "missing"
    PRESENT = "present"


class StagingState(StrEnum):
    """State of an installer staging transaction."""

    NONE = "none"
    PRESENT = "present"


class InstallOperation(StrEnum):
    """Operations owned by the installer transaction."""

    INSTALL = "install"
    UPDATE = "update"
    EXACT_VERSION = "exact_version"
    ROLLBACK = "rollback"
    REPAIR = "repair"
    UNINSTALL = "uninstall"


class RequirementKind(StrEnum):
    """External requirement classes recorded in a plan."""

    FILE = "file"
    NETWORK = "network"
    DISK = "disk"
    PLATFORM = "platform"


class EffectKind(StrEnum):
    """State changes that an install plan can declare."""

    CREATE_INSTALL_ROOT = "create_install_root"
    CREATE_VERSION = "create_version"
    INSTALL_BROWSER = "install_browser"
    INSTALL_AGENT_INSTRUCTIONS = "install_agent_instructions"
    INSTALL_AGENT_SKILL = "install_agent_skill"
    INSTALL_LAUNCHER = "install_launcher"
    ACTIVATE_VERSION = "activate_version"


class ErrorCategory(StrEnum):
    """Stable installer failure categories."""

    VALIDATION = "validation"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    UNSUPPORTED = "unsupported"
    INTERNAL = "internal"


class InstallerErrorCode(StrEnum):
    """Stable codes that callers use instead of English messages."""

    INSTALL_NOT_MANAGED = "install.not_managed"
    INSTALL_OPERATION_NOT_SUPPORTED = "install.operation_not_supported"
    INSTALL_PATH_UNAVAILABLE = "install.path_unavailable"
    PYTHON_INSTALL_FAILED = "python.install_failed"
    ENVIRONMENT_CREATE_FAILED = "environment.create_failed"
    DEPENDENCY_INSTALL_FAILED = "dependency.install_failed"
    BROWSER_INSTALL_FAILED = "browser.install_failed"
    AGENT_INSTRUCTIONS_FAILED = "agent_instructions.failed"
    DOCTOR_FAILED = "doctor.failed"
    UPDATE_PLAN_STALE = "update.plan_stale"
    UPDATE_ACTIVATION_FAILED = "update.activation_failed"
    RELEASE_METADATA_UNTRUSTED = "release.metadata_untrusted"
    RELEASE_CHANNEL_NOT_FOUND = "release.channel_not_found"
    RELEASE_TARGET_VERIFICATION_FAILED = "release.target_verification_failed"
    RELEASE_VERSION_NOT_FOUND = "release.version_not_found"


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$", strict=True)]
Version = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$",
        strict=True,
    ),
]


class TrustedTarget(ContractModel):
    """One target identity from trusted release metadata."""

    name: str = Field(min_length=1)
    length: int = Field(gt=0)
    sha256: Sha256

    @field_validator("name")
    @classmethod
    def target_name_is_logical(cls, value: str) -> str:
        """Reject physical and traversing target names."""
        if value in {".", ".."} or "/" in value or "\\" in value:
            msg = "Target names must be single logical path segments."
            raise ValueError(msg)
        return value


class DataCompatibility(ContractModel):
    """Data versions that a selected release can read and write."""

    readable_home_layouts: tuple[int, ...] = Field(min_length=1)
    writable_home_layout: int = Field(ge=1)
    activegraph_version: Version


class TrustedRelease(ContractModel):
    """A selected release from a trusted local description."""

    schema_version: Literal["1.0"] = "1.0"
    version: Version
    channel: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    python_version: Annotated[
        str,
        StringConstraints(pattern=r"^3\.12\.[0-9]+$", strict=True),
    ]
    browser_revision: str = Field(min_length=1)
    application_target: str = Field(min_length=1)
    installer_target: str | None = Field(default=None, min_length=1)
    installer_version: Version | None = None
    data_compatibility: DataCompatibility
    targets: tuple[TrustedTarget, ...] = Field(min_length=1)

    @field_validator("application_target", "installer_target")
    @classmethod
    def executable_target_is_logical(cls, value: str | None) -> str | None:
        """Reject physical and traversing executable target names."""
        if value is None:
            return None
        if value in {".", ".."} or "/" in value or "\\" in value:
            msg = "Executable targets must be one logical path segment."
            raise ValueError(msg)
        return value


class InstallRequest(ContractModel):
    """A typed request to create an installer plan."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._:-]+$")
    operation: InstallOperation
    install_root: Path
    data_home: Path
    channel: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    version: Version | None = None
    release_source: Literal["local_trusted"] = "local_trusted"
    release_description: Path


class PlanRequirement(ContractModel):
    """One requirement checked during planning."""

    kind: RequirementKind
    name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    satisfied: bool


class PlanEffect(ContractModel):
    """One declared state change."""

    kind: EffectKind
    target: str = Field(min_length=1)


class RecoveryStep(ContractModel):
    """One safe action after an interrupted apply."""

    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class InstallPlan(ContractModel):
    """A complete, read-only description of an installer transaction."""

    schema_version: Literal["1.0"] = "1.0"
    plan_id: str = Field(pattern=r"^plan_[0-9a-f]{32}$")
    request_id: str
    transaction_id: str = Field(pattern=r"^txn_[0-9a-f]{32}$")
    operation: InstallOperation
    install_root: Path
    data_home: Path
    current_version: Version | None
    requested_version: Version
    selected_channel: str
    selected_release: TrustedRelease
    release_description: Path
    requirements: tuple[PlanRequirement, ...]
    effects: tuple[PlanEffect, ...]
    recovery_steps: tuple[RecoveryStep, ...]
    state_digest: Sha256
    staging_rule: Literal["stage_before_activation"]
    activation_rule: Literal["activate_after_staged_doctor"]
    network_required: bool
    changed: bool


class InstallResult(ContractModel):
    """Terminal result from applying one install plan."""

    schema_version: Literal["1.0"] = "1.0"
    plan_id: str
    request_id: str
    transaction_id: str
    operation: InstallOperation
    installed_version: Version | None
    requested_version: Version
    selected_channel: str
    changed: bool
    pointer_state: PointerState
    data_state: DataState
    staging_state: StagingState
    retryable: bool
    diagnostic_id: str | None
    recovery_action: str | None


class InstallerFailure(ContractModel):
    """Structured failure attached to an installer exception."""

    code: InstallerErrorCode
    category: ErrorCategory
    message: str = Field(min_length=1)
    retryable: bool
    diagnostic_id: str | None
    recovery_action: str = Field(min_length=1)


class InstallationState(ContractModel):
    """Read-only state of one ROI-H installation root."""

    schema_version: Literal["1.0"] = "1.0"
    install_root: Path
    data_home: Path
    managed: bool
    active_version: str | None
    installed_versions: tuple[str, ...]
    pointer_state: PointerState
    data_state: DataState
    staging_state: StagingState
