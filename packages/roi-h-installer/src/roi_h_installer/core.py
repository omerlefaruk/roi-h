"""Installer planning and inspection behavior."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class _TransactionPaths:
    transactions_root: Path
    staging_root: Path
    staged_environment: Path
    final_environment: Path
    pointer: Path
    temporary_pointer: Path
    state_file: Path
    record_file: Path


@dataclass(frozen=True)
class _PreviousActivation:
    pointer_target: Path | None
    pointer_bytes: bytes | None
    state_bytes: bytes | None


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


def _is_windows() -> bool:
    return os.name == "nt"


def _resolve_pointer(install_root: Path, pointer: Path) -> Path | None:
    if _is_windows():
        if not pointer.is_file():
            return None
        try:
            version = pointer.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if _VERSION_PATTERN.fullmatch(version) is None:
            return None
        return install_root / "versions" / version
    if pointer.exists() or pointer.is_symlink():
        return pointer.resolve()
    return None


def _inspect_at(install_root: Path, data_home: Path) -> InstallationState:
    versions_root = install_root / "versions"
    installed_versions = (
        tuple(sorted(path.name for path in versions_root.iterdir() if path.is_dir()))
        if versions_root.is_dir()
        else ()
    )
    pointer = install_root / "current"
    pointer_target = _resolve_pointer(install_root, pointer)
    if not pointer.exists() and not pointer.is_symlink():
        pointer_state = PointerState.MISSING
        active_version = None
    elif pointer_target is not None and pointer_target.is_dir():
        pointer_state = PointerState.ACTIVE
        active_version = pointer_target.name
    else:
        pointer_state = PointerState.BROKEN
        active_version = None

    transactions_root = install_root / "transactions"
    staging_present = transactions_root.is_dir() and any(
        path.is_dir() and path.name.endswith(".staging") for path in transactions_root.iterdir()
    )

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


def _write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def plan(request: InstallRequest) -> InstallPlan:
    """Plan an install or managed update without changing local state."""
    if request.operation not in {InstallOperation.INSTALL, InstallOperation.UPDATE}:
        raise _fail(
            InstallerErrorCode.INSTALL_OPERATION_NOT_SUPPORTED,
            ErrorCategory.UNSUPPORTED,
            "This installer slice supports only install and update plans.",
            "Use an install or update request.",
        )

    install_root = request.install_root.expanduser().resolve()
    data_home = request.data_home.expanduser().resolve()
    _assert_separate_roots(install_root, data_home)
    state = _inspect_at(install_root, data_home)
    release = _load_local_release(request)
    changed = _plan_changed(request.operation, state, release)
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
        release_description=release_file,
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
            PlanRequirement(
                kind=RequirementKind.NETWORK,
                name="playwright_browser",
                value=release.browser_revision,
                satisfied=False,
            ),
        ),
        effects=_plan_effects(
            request.operation,
            install_root,
            release.version,
            changed=changed,
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
        network_required=changed,
        changed=changed,
    )


def _plan_changed(
    operation: InstallOperation,
    state: InstallationState,
    release: TrustedRelease,
) -> bool:
    healthy_managed = (
        state.managed
        and state.pointer_state is PointerState.ACTIVE
        and state.active_version is not None
    )
    if operation is InstallOperation.UPDATE:
        if not healthy_managed:
            raise _fail(
                InstallerErrorCode.INSTALL_NOT_MANAGED,
                ErrorCategory.CONFLICT,
                "An update requires a healthy managed installation.",
                "Inspect the installation or use an initial install request.",
            )
        return state.active_version != release.version
    if not state.managed and state.pointer_state is PointerState.MISSING:
        return True
    if healthy_managed and state.active_version == release.version:
        return False
    raise _fail(
        InstallerErrorCode.INSTALL_OPERATION_NOT_SUPPORTED,
        ErrorCategory.CONFLICT,
        "An install request cannot replace a different managed version.",
        "Use a managed update request.",
    )


def _plan_effects(
    operation: InstallOperation,
    install_root: Path,
    version: str,
    *,
    changed: bool,
) -> tuple[PlanEffect, ...]:
    if not changed:
        return ()
    effects = [
        PlanEffect(
            kind=EffectKind.CREATE_VERSION,
            target=str(install_root / "versions" / version),
        ),
        PlanEffect(
            kind=EffectKind.INSTALL_BROWSER,
            target=str(install_root / "browsers"),
        ),
        PlanEffect(
            kind=EffectKind.ACTIVATE_VERSION,
            target=str(install_root / "current"),
        ),
    ]
    if operation is InstallOperation.INSTALL:
        effects.insert(
            0,
            PlanEffect(kind=EffectKind.CREATE_INSTALL_ROOT, target=str(install_root)),
        )
        effects.insert(
            2,
            PlanEffect(
                kind=EffectKind.INSTALL_LAUNCHER,
                target=str(install_root / "installer" / "current"),
            ),
        )
    return tuple(effects)


def apply(install_plan: InstallPlan) -> InstallResult:
    """Apply an install or managed update from a verified local wheelhouse."""
    if install_plan.operation not in {InstallOperation.INSTALL, InstallOperation.UPDATE}:
        raise _fail(
            InstallerErrorCode.INSTALL_OPERATION_NOT_SUPPORTED,
            ErrorCategory.UNSUPPORTED,
            "This installer slice applies only install and update plans.",
            "Use an install or update plan.",
        )

    initial_state = _inspect_at(install_plan.install_root, install_plan.data_home)
    if (
        initial_state.managed
        and initial_state.pointer_state is PointerState.ACTIVE
        and initial_state.active_version == install_plan.requested_version
    ):
        return _install_result(install_plan, initial_state, changed=False)
    if _digest(initial_state.model_dump(mode="json")) != install_plan.state_digest:
        raise _fail(
            InstallerErrorCode.UPDATE_PLAN_STALE,
            ErrorCategory.CONFLICT,
            "The installation state changed after the plan was made.",
            "Create and review a new install plan.",
        )

    paths = _transaction_paths(install_plan)
    previous = _capture_previous_activation(paths)
    try:
        _execute_initial_install(install_plan, paths)
    except Exception as exc:
        failure = _normalize_apply_error(exc)
        _restore_previous_install(paths, previous)
        _record_failure(install_plan, paths, failure)
        if isinstance(exc, InstallerError):
            raise
        raise failure from exc

    final_state = _inspect_at(install_plan.install_root, install_plan.data_home)
    return _install_result(install_plan, final_state, changed=True)


def _install_result(
    install_plan: InstallPlan,
    state: InstallationState,
    *,
    changed: bool,
) -> InstallResult:
    return InstallResult(
        plan_id=install_plan.plan_id,
        request_id=install_plan.request_id,
        transaction_id=install_plan.transaction_id,
        operation=install_plan.operation,
        installed_version=state.active_version,
        requested_version=install_plan.requested_version,
        selected_channel=install_plan.selected_channel,
        changed=changed,
        pointer_state=state.pointer_state,
        data_state=state.data_state,
        staging_state=state.staging_state,
        retryable=False,
        diagnostic_id=None,
        recovery_action=None,
    )


def _transaction_paths(install_plan: InstallPlan) -> _TransactionPaths:
    install_root = install_plan.install_root
    transactions_root = install_root / "transactions"
    staging_root = transactions_root / f"{install_plan.transaction_id}.staging"
    return _TransactionPaths(
        transactions_root=transactions_root,
        staging_root=staging_root,
        staged_environment=staging_root / "environment",
        final_environment=install_root / "versions" / install_plan.requested_version,
        pointer=install_root / "current",
        temporary_pointer=install_root / f".current-{install_plan.transaction_id}",
        state_file=install_root / "install-state.json",
        record_file=transactions_root / f"{install_plan.transaction_id}.json",
    )


def _execute_initial_install(
    install_plan: InstallPlan,
    paths: _TransactionPaths,
) -> None:
    paths.transactions_root.mkdir(parents=True, exist_ok=True)
    if paths.staging_root.exists():
        shutil.rmtree(paths.staging_root)
    paths.staging_root.mkdir()
    targets = _verify_local_targets(install_plan)
    _assert_python_version(install_plan)
    paths.final_environment.parent.mkdir(parents=True, exist_ok=True)
    _assert_final_version_missing(paths.final_environment)
    working_environment = paths.final_environment if _is_windows() else paths.staged_environment
    _create_environment(working_environment)
    _install_wheelhouse(working_environment, install_plan, targets)
    _install_browser(working_environment, install_plan)
    _run_staged_doctor(working_environment, install_plan)

    if not _is_windows():
        paths.staged_environment.replace(paths.final_environment)
        _relocate_environment_scripts(paths.staged_environment, paths.final_environment)
    shutil.rmtree(paths.staging_root)
    _run_staged_doctor(paths.final_environment, install_plan)

    _activate_pointer(paths, install_plan.requested_version)
    _write_json_atomic(
        paths.state_file,
        {
            "schema_version": 1,
            "active_version": install_plan.requested_version,
            "channel": install_plan.selected_channel,
            "plan_id": install_plan.plan_id,
            "transaction_id": install_plan.transaction_id,
            "python_version": install_plan.selected_release.python_version,
            "browser_revision": install_plan.selected_release.browser_revision,
            "installer_version": install_plan.selected_release.installer_version,
            "targets": [
                target.model_dump(mode="json") for target in install_plan.selected_release.targets
            ],
        },
    )
    active_environment = paths.final_environment if _is_windows() else paths.pointer
    _run_staged_doctor(active_environment, install_plan)
    _write_json_atomic(
        paths.record_file,
        {
            "schema_version": "1.0",
            "status": "complete",
            "plan_id": install_plan.plan_id,
            "request_id": install_plan.request_id,
            "transaction_id": install_plan.transaction_id,
            "installed_version": install_plan.requested_version,
        },
    )


def _assert_final_version_missing(final_environment: Path) -> None:
    if final_environment.exists():
        raise _fail(
            InstallerErrorCode.UPDATE_PLAN_STALE,
            ErrorCategory.CONFLICT,
            "The selected version directory already exists.",
            "Inspect the installation and create a new plan.",
        )


def _capture_previous_activation(paths: _TransactionPaths) -> _PreviousActivation:
    pointer_target = (
        paths.pointer.resolve() if not _is_windows() and paths.pointer.is_symlink() else None
    )
    pointer_bytes = (
        paths.pointer.read_bytes() if _is_windows() and paths.pointer.is_file() else None
    )
    state_bytes = paths.state_file.read_bytes() if paths.state_file.is_file() else None
    return _PreviousActivation(
        pointer_target=pointer_target,
        pointer_bytes=pointer_bytes,
        state_bytes=state_bytes,
    )


def _write_bytes_atomic(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _activate_pointer(paths: _TransactionPaths, version: str) -> None:
    paths.temporary_pointer.unlink(missing_ok=True)
    if _is_windows():
        paths.temporary_pointer.write_text(f"{version}\n", encoding="ascii")
    else:
        paths.temporary_pointer.symlink_to(paths.final_environment, target_is_directory=True)
    paths.temporary_pointer.replace(paths.pointer)


def _restore_previous_install(
    paths: _TransactionPaths,
    previous: _PreviousActivation,
) -> None:
    if _is_windows():
        if previous.pointer_bytes is None:
            paths.pointer.unlink(missing_ok=True)
        else:
            _write_bytes_atomic(paths.pointer, previous.pointer_bytes)
    elif paths.pointer.is_symlink() and paths.pointer.resolve() == paths.final_environment:
        if previous.pointer_target is None:
            paths.pointer.unlink(missing_ok=True)
        else:
            rollback_pointer = paths.pointer.with_name(".current-rollback")
            rollback_pointer.unlink(missing_ok=True)
            rollback_pointer.symlink_to(previous.pointer_target, target_is_directory=True)
            rollback_pointer.replace(paths.pointer)
    paths.temporary_pointer.unlink(missing_ok=True)
    if paths.final_environment.exists():
        shutil.rmtree(paths.final_environment)
    if paths.staging_root.exists():
        shutil.rmtree(paths.staging_root)
    if previous.state_bytes is None:
        paths.state_file.unlink(missing_ok=True)
    else:
        _write_bytes_atomic(paths.state_file, previous.state_bytes)


def _record_failure(
    install_plan: InstallPlan,
    paths: _TransactionPaths,
    failure: InstallerError,
) -> None:
    try:
        paths.transactions_root.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            paths.record_file,
            {
                "schema_version": "1.0",
                "status": "failed",
                "plan_id": install_plan.plan_id,
                "request_id": install_plan.request_id,
                "transaction_id": install_plan.transaction_id,
                "failure": failure.failure.model_dump(mode="json"),
            },
        )
    except OSError:
        pass


def _verify_local_targets(install_plan: InstallPlan) -> dict[str, Path]:
    wheelhouse = install_plan.release_description.parent
    verified: dict[str, Path] = {}
    for target in install_plan.selected_release.targets:
        path = wheelhouse / target.name
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise _fail(
                InstallerErrorCode.RELEASE_TARGET_VERIFICATION_FAILED,
                ErrorCategory.VALIDATION,
                "A declared release target is not available.",
                "Restore the complete trusted local wheelhouse and create a new plan.",
            ) from exc
        if len(content) != target.length or hashlib.sha256(content).hexdigest() != target.sha256:
            raise _fail(
                InstallerErrorCode.RELEASE_TARGET_VERIFICATION_FAILED,
                ErrorCategory.VALIDATION,
                "A declared release target failed size or SHA-256 verification.",
                "Restore the trusted target bytes and create a new plan.",
            )
        verified[target.name] = path
    if install_plan.selected_release.application_target not in verified:
        raise _fail(
            InstallerErrorCode.RELEASE_TARGET_VERIFICATION_FAILED,
            ErrorCategory.VALIDATION,
            "The declared application wheel is not in the trusted target set.",
            "Add the application wheel to the trusted release description.",
        )
    return verified


def _assert_python_version(install_plan: InstallPlan) -> None:
    running_version = platform.python_version()
    required_version = install_plan.selected_release.python_version
    if sys.version_info[:2] != (3, 12) or running_version != required_version:
        raise _fail(
            InstallerErrorCode.PYTHON_INSTALL_FAILED,
            ErrorCategory.UNSUPPORTED,
            "The installer does not run with the Python version required by this release.",
            f"Run this installer with Python {install_plan.selected_release.python_version}.",
        )


def _create_environment(staged_environment: Path) -> None:
    try:
        venv.EnvBuilder(with_pip=True, symlinks=not _is_windows()).create(staged_environment)
    except Exception as exc:
        raise _fail(
            InstallerErrorCode.ENVIRONMENT_CREATE_FAILED,
            ErrorCategory.INTERNAL,
            "The staged Python environment could not be created.",
            "Remove the failed staging data and retry the install.",
            retryable=True,
        ) from exc


def _environment_python(environment: Path) -> Path:
    if _is_windows():
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _environment_cli(environment: Path) -> Path:
    if _is_windows():
        return environment / "Scripts" / "roi-h.exe"
    return environment / "bin" / "roi-h"


def _environment_playwright(environment: Path) -> Path:
    if _is_windows():
        return environment / "Scripts" / "playwright.exe"
    return environment / "bin" / "playwright"


def _relocate_environment_scripts(previous: Path, current: Path) -> None:
    if _is_windows():
        return
    scripts = current / "bin"
    previous_bytes = str(previous).encode()
    current_bytes = str(current).encode()
    for path in scripts.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_bytes()
        if content.startswith(b"#!") and previous_bytes in content:
            path.write_bytes(content.replace(previous_bytes, current_bytes))


def _run_process(
    command: list[str],
    *,
    environment: dict[str, str],
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    # The command uses the staged interpreter and verified local target paths.
    return subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=timeout,
    )


def _install_wheelhouse(
    staged_environment: Path,
    install_plan: InstallPlan,
    targets: dict[str, Path],
) -> None:
    application_name = install_plan.selected_release.application_target
    installer_name = install_plan.selected_release.installer_target
    wheel_names = [
        target.name
        for target in install_plan.selected_release.targets
        if target.name.endswith(".whl")
    ]
    dependency_wheels = [
        str(targets[name]) for name in wheel_names if name not in {application_name, installer_name}
    ]
    environment = os.environ.copy()
    environment["PIP_NO_INDEX"] = "1"
    python = str(_environment_python(staged_environment))
    commands = []
    if dependency_wheels:
        commands.append(
            [python, "-m", "pip", "install", "--no-index", "--no-deps", *dependency_wheels]
        )
    commands.append(
        [
            python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            str(targets[application_name]),
        ]
    )
    for command in commands:
        completed = _run_process(command, environment=environment)
        if completed.returncode != 0:
            raise _fail(
                InstallerErrorCode.DEPENDENCY_INSTALL_FAILED,
                ErrorCategory.INTERNAL,
                "The verified local wheelhouse could not be installed.",
                "Correct the complete wheelhouse and retry the install.",
                retryable=True,
            )


def _install_browser(
    staged_environment: Path,
    install_plan: InstallPlan,
) -> None:
    browser_root = install_plan.install_root / "browsers"
    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
    environment["ROI_H_BROWSER_REVISION"] = install_plan.selected_release.browser_revision
    completed = _run_process(
        [str(_environment_playwright(staged_environment)), "install", "chromium"],
        environment=environment,
        timeout=900,
    )
    expected_revision = browser_root / install_plan.selected_release.browser_revision
    if completed.returncode != 0 or not expected_revision.is_dir():
        raise _fail(
            InstallerErrorCode.BROWSER_INSTALL_FAILED,
            ErrorCategory.INTERNAL,
            "The required Playwright Chromium revision could not be installed.",
            "Check network access and platform requirements, then retry the install.",
            retryable=True,
        )


def _run_staged_doctor(staged_environment: Path, install_plan: InstallPlan) -> None:
    environment = os.environ.copy()
    state_file = install_plan.install_root / "install-state.json"
    doctor_install_root = (
        install_plan.install_root
        if state_file.is_file()
        else staged_environment / ".pre-activation-install-root"
    )
    environment["ROI_H_INSTALL_ROOT"] = str(doctor_install_root)
    environment["ROI_H_HOME"] = str(install_plan.data_home)
    completed = _run_process(
        [str(_environment_cli(staged_environment)), "doctor", "--output", "json"],
        environment=environment,
    )
    try:
        output: Any = json.loads(completed.stdout)
    except json.JSONDecodeError:
        output = None
    if completed.returncode != 0 or not isinstance(output, dict) or output.get("ok") is not True:
        raise _fail(
            InstallerErrorCode.DOCTOR_FAILED,
            ErrorCategory.INTERNAL,
            "The staged ROI-H doctor check failed.",
            "Inspect the failed transaction record and correct the release.",
        )


def _normalize_apply_error(error: Exception) -> InstallerError:
    if isinstance(error, InstallerError):
        return error
    return _fail(
        InstallerErrorCode.UPDATE_ACTIVATION_FAILED,
        ErrorCategory.INTERNAL,
        "The initial installation could not be activated.",
        "Inspect the failed transaction record and retry with a new plan.",
        retryable=True,
    )
