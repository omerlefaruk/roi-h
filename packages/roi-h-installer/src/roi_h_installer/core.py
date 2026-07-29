"""Installer planning and inspection behavior."""

import hashlib
import json
import os
import platform
from pathlib import Path

from pydantic import ValidationError

from roi_h_installer.models import (
    DataState,
    EffectKind,
    ErrorCategory,
    InstallationState,
    InstallerErrorCode,
    InstallerFailure,
    InstallOperation,
    InstallPlan,
    InstallRequest,
    InstallResult,
    PlanEffect,
    PlanRequirement,
    PointerState,
    RecoveryStep,
    RequirementKind,
    StagingState,
    TrustedRelease,
)

_DIGEST_SIZE = 16


class InstallerError(RuntimeError):
    """Exception with a stable machine-readable installer failure."""

    def __init__(self, failure: InstallerFailure) -> None:
        """Create an exception from a structured failure."""
        super().__init__(failure.message)
        self.failure = failure


def _fail(
    code: InstallerErrorCode,
    category: ErrorCategory,
    message: str,
    recovery_action: str,
    *,
    retryable: bool = False,
) -> InstallerError:
    return InstallerError(
        InstallerFailure(
            code=code,
            category=category,
            message=message,
            retryable=retryable,
            diagnostic_id=None,
            recovery_action=recovery_action,
        )
    )


def _install_root() -> Path:
    configured = os.environ.get("ROI_H_INSTALL_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return (Path(xdg_data_home).expanduser() / "roi-h").resolve()
    return (Path.home() / ".local" / "share" / "roi-h").resolve()


def _data_home() -> Path:
    configured = os.environ.get("ROI_H_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".roi-h").resolve()


def _inspect_at(install_root: Path, data_home: Path) -> InstallationState:
    versions_root = install_root / "versions"
    installed_versions = (
        tuple(sorted(path.name for path in versions_root.iterdir() if path.is_dir()))
        if versions_root.is_dir()
        else ()
    )
    pointer = install_root / "current"
    if not pointer.exists() and not pointer.is_symlink():
        pointer_state = PointerState.MISSING
        active_version = None
    elif pointer.exists():
        pointer_state = PointerState.ACTIVE
        active_version = pointer.resolve().name
    else:
        pointer_state = PointerState.BROKEN
        active_version = None

    transactions_root = install_root / "transactions"
    staging_present = transactions_root.is_dir() and any(transactions_root.iterdir())

    return InstallationState(
        install_root=install_root,
        data_home=data_home,
        managed=(install_root / "install-state.json").is_file(),
        active_version=active_version,
        installed_versions=installed_versions,
        pointer_state=pointer_state,
        data_state=DataState.PRESENT if data_home.exists() else DataState.MISSING,
        staging_state=StagingState.PRESENT if staging_present else StagingState.NONE,
    )


def inspect() -> InstallationState:
    """Inspect the configured installation without changing it."""
    return _inspect_at(_install_root(), _data_home())


def _load_local_release(request: InstallRequest) -> TrustedRelease:
    try:
        content = request.release_description.read_text(encoding="utf-8")
        release = TrustedRelease.model_validate_json(content)
    except (OSError, ValidationError, ValueError) as exc:
        raise _fail(
            InstallerErrorCode.RELEASE_METADATA_UNTRUSTED,
            ErrorCategory.VALIDATION,
            "The local release description is not trusted release metadata.",
            "Use a valid description from the local trusted release repository.",
        ) from exc
    if release.channel != request.channel:
        raise _fail(
            InstallerErrorCode.RELEASE_CHANNEL_NOT_FOUND,
            ErrorCategory.NOT_FOUND,
            "The release description does not contain the selected channel.",
            "Select the channel in the trusted release description.",
        )
    if request.version is not None and release.version != request.version:
        raise _fail(
            InstallerErrorCode.RELEASE_VERSION_NOT_FOUND,
            ErrorCategory.NOT_FOUND,
            "The release description does not contain the requested version.",
            "Select the version in the trusted release description.",
        )
    return release


def _assert_separate_roots(install_root: Path, data_home: Path) -> None:
    if (
        install_root == data_home
        or install_root.is_relative_to(data_home)
        or data_home.is_relative_to(install_root)
    ):
        raise _fail(
            InstallerErrorCode.INSTALL_PATH_UNAVAILABLE,
            ErrorCategory.VALIDATION,
            "The install root and data home must not contain each other.",
            "Select separate install and data roots.",
        )


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def plan(request: InstallRequest) -> InstallPlan:
    """Plan an initial install without changing local state."""
    if request.operation is not InstallOperation.INSTALL:
        raise _fail(
            InstallerErrorCode.INSTALL_OPERATION_NOT_SUPPORTED,
            ErrorCategory.UNSUPPORTED,
            "This installer slice supports only an initial install plan.",
            "Use an install request for a root with no current installation.",
        )

    install_root = request.install_root.expanduser().resolve()
    data_home = request.data_home.expanduser().resolve()
    _assert_separate_roots(install_root, data_home)
    state = _inspect_at(install_root, data_home)
    if state.managed or state.pointer_state is not PointerState.MISSING:
        raise _fail(
            InstallerErrorCode.INSTALL_OPERATION_NOT_SUPPORTED,
            ErrorCategory.CONFLICT,
            "This installer slice cannot replace a current installation.",
            "Use the update planner for a managed installation.",
        )

    release = _load_local_release(request)
    normalized = {
        "request": request.model_dump(mode="json"),
        "state": state.model_dump(mode="json"),
        "release": release.model_dump(mode="json"),
    }
    state_digest = _digest(state.model_dump(mode="json"))
    identity = _digest(normalized)[: _DIGEST_SIZE * 2]
    release_file = request.release_description.expanduser().resolve()
    return InstallPlan(
        plan_id=f"plan_{identity}",
        request_id=request.request_id,
        transaction_id=f"txn_{identity}",
        operation=request.operation,
        install_root=install_root,
        data_home=data_home,
        current_version=state.active_version,
        requested_version=release.version,
        selected_channel=release.channel,
        selected_release=release,
        requirements=(
            PlanRequirement(
                kind=RequirementKind.FILE,
                name="trusted_release_description",
                value=str(release_file),
                satisfied=release_file.is_file(),
            ),
            PlanRequirement(
                kind=RequirementKind.PLATFORM,
                name="runtime_platform",
                value=f"{platform.system().lower()}-{platform.machine().lower()}",
                satisfied=True,
            ),
        ),
        effects=(
            PlanEffect(kind=EffectKind.CREATE_INSTALL_ROOT, target=str(install_root)),
            PlanEffect(
                kind=EffectKind.CREATE_VERSION,
                target=str(install_root / "versions" / release.version),
            ),
            PlanEffect(
                kind=EffectKind.INSTALL_LAUNCHER,
                target=str(install_root / "installer" / "current"),
            ),
            PlanEffect(
                kind=EffectKind.ACTIVATE_VERSION,
                target=str(install_root / "current"),
            ),
        ),
        recovery_steps=(
            RecoveryStep(
                action="remove_unactivated_staging",
                reason="A failure before activation must leave no active partial release.",
            ),
            RecoveryStep(
                action="preserve_data_home",
                reason="Install recovery must not delete user data.",
            ),
        ),
        state_digest=state_digest,
        staging_rule="stage_before_activation",
        activation_rule="activate_after_staged_doctor",
        network_required=False,
        changed=True,
    )


def apply(install_plan: InstallPlan) -> InstallResult:
    """Apply a plan when transaction execution is available."""
    del install_plan
    raise _fail(
        InstallerErrorCode.INSTALL_OPERATION_NOT_SUPPORTED,
        ErrorCategory.UNSUPPORTED,
        "Install apply is not available in this implementation slice.",
        "Use plan only until the transaction executor is implemented.",
    )
