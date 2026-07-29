"""Read-only installation health inspection."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

SCHEMA_VERSION: Literal[1] = 1
SUPPORTED_PYTHON = (3, 12)
BUILT_IN_SKILLS = ("browser", "excel", "feedback", "files", "http", "pdf", "shell")

type HealthStatus = Literal["pass", "fail", "pending"]
type ManagedInstallState = Literal["managed", "unmanaged", "invalid"]
type DataHomeAccess = Literal[
    "read_write",
    "read_only",
    "available_for_creation",
    "unavailable",
]
type JsonValue = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None


def default_install_root() -> Path:
    """Return the stable managed installation root without creating it."""
    configured = os.environ.get("ROI_H_INSTALL_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data).expanduser() / "ROI-H").resolve()
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return (Path(xdg_data_home).expanduser() / "roi-h").resolve()
    return (Path.home() / ".local" / "share" / "roi-h").resolve()


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """One stable, machine-readable health check."""

    code: str
    status: HealthStatus
    message: str
    details: dict[str, JsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a JSON-compatible representation."""
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class InstallationHealthReport:
    """A strict report for the read-only doctor command."""

    schema_version: Literal[1]
    application_version: str
    python_version: str
    python_compatible: bool
    built_in_skills: dict[str, bool]
    install_root: str
    data_home: str
    data_home_access: DataHomeAccess
    managed_install_state: ManagedInstallState
    healthy: bool
    checks: tuple[HealthCheck, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "application_version": self.application_version,
            "python_version": self.python_version,
            "python_compatible": self.python_compatible,
            "built_in_skills": dict(self.built_in_skills),
            "install_root": self.install_root,
            "data_home": self.data_home,
            "data_home_access": self.data_home_access,
            "managed_install_state": self.managed_install_state,
            "healthy": self.healthy,
            "checks": [check.to_dict() for check in self.checks],
        }


def inspect_installation_health(
    *,
    install_root: Path,
    data_home: Path,
    application_version: str | None = None,
    python_version: tuple[int, int, int] | None = None,
    skills_root: Traversable | None = None,
) -> InstallationHealthReport:
    """Inspect an installation without changing the file system."""
    resolved_application_version = application_version or _application_version()
    resolved_python_version = python_version or (
        sys.version_info.major,
        sys.version_info.minor,
        sys.version_info.micro,
    )
    resolved_skills_root = skills_root or resources.files("roi_h").joinpath("_skills")

    python_compatible = resolved_python_version[:2] == SUPPORTED_PYTHON
    skill_presence = {
        name: _skill_is_installed(resolved_skills_root, name) for name in BUILT_IN_SKILLS
    }
    skill_details = cast("dict[str, JsonValue]", dict(skill_presence))
    data_home_access = _inspect_data_home(data_home)
    managed_state, managed_details = _inspect_managed_install(install_root)
    version_text = ".".join(str(part) for part in resolved_python_version)

    checks = (
        HealthCheck(
            code="application.version",
            status="pass" if resolved_application_version != "unknown" else "fail",
            message=(
                "The application version is available."
                if resolved_application_version != "unknown"
                else "The application version is not available."
            ),
            details={"version": resolved_application_version},
        ),
        HealthCheck(
            code="python.version",
            status="pass" if python_compatible else "fail",
            message=("Python 3.12 is in use." if python_compatible else "Python 3.12 is required."),
            details={
                "version": version_text,
                "required_major": SUPPORTED_PYTHON[0],
                "required_minor": SUPPORTED_PYTHON[1],
            },
        ),
        HealthCheck(
            code="skills.built_in",
            status="pass" if all(skill_presence.values()) else "fail",
            message=(
                "All built-in skills are installed."
                if all(skill_presence.values())
                else "One or more built-in skills are not installed."
            ),
            details={"skills": skill_details},
        ),
        HealthCheck(
            code="data_home.access",
            status=(
                "pass" if data_home_access in {"read_write", "available_for_creation"} else "fail"
            ),
            message=_data_home_message(data_home_access),
            details={"access": data_home_access},
        ),
        HealthCheck(
            code="install.managed_state",
            status="fail" if managed_state == "invalid" else "pass",
            message=_managed_install_message(managed_state),
            details=managed_details,
        ),
        HealthCheck(
            code="browser.launch",
            status="pending",
            message="The browser launch check is not configured in this doctor version.",
            details={"reason": "not_configured"},
        ),
    )

    return InstallationHealthReport(
        schema_version=SCHEMA_VERSION,
        application_version=resolved_application_version,
        python_version=version_text,
        python_compatible=python_compatible,
        built_in_skills=skill_presence,
        install_root=str(install_root),
        data_home=str(data_home),
        data_home_access=data_home_access,
        managed_install_state=managed_state,
        healthy=all(check.status != "fail" for check in checks),
        checks=checks,
    )


def _application_version() -> str:
    try:
        return metadata.version("roi-h")
    except metadata.PackageNotFoundError:
        return "unknown"


def _skill_is_installed(skills_root: Traversable, name: str) -> bool:
    skill_file = skills_root.joinpath(name).joinpath("SKILL.md")
    return skill_file.is_file()


def _inspect_data_home(data_home: Path) -> DataHomeAccess:
    if data_home.exists():
        if not data_home.is_dir():
            return "unavailable"
        readable = os.access(data_home, os.R_OK | os.X_OK)
        writable = os.access(data_home, os.W_OK | os.X_OK)
        if readable and writable:
            return "read_write"
        if readable:
            return "read_only"
        return "unavailable"

    parent = data_home.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if parent.is_dir() and os.access(parent, os.W_OK | os.X_OK):
        return "available_for_creation"
    return "unavailable"


def _inspect_managed_install(
    install_root: Path,
) -> tuple[ManagedInstallState, dict[str, JsonValue]]:
    state_path = install_root / "install-state.json"
    current_path = install_root / "current"
    state_present = state_path.is_file()
    current_present = current_path.exists() or current_path.is_symlink()

    if not state_present and not current_present:
        return "unmanaged", {"state": "unmanaged"}
    if not state_present or not current_present:
        return "invalid", {"state": "invalid", "reason": "managed_files_incomplete"}

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "invalid", {"state": "invalid", "reason": "state_unreadable"}

    if not isinstance(payload, dict):
        return "invalid", {"state": "invalid", "reason": "state_invalid"}
    schema_version = payload.get("schema_version")
    active_version = payload.get("active_version")
    if (
        schema_version != SCHEMA_VERSION
        or not isinstance(active_version, str)
        or not active_version
    ):
        return "invalid", {"state": "invalid", "reason": "state_invalid"}
    return "managed", {
        "state": "managed",
        "schema_version": schema_version,
        "active_version": active_version,
    }


def _data_home_message(access: DataHomeAccess) -> str:
    messages = {
        "read_write": "The data home is readable and writable.",
        "read_only": "The data home is read-only.",
        "available_for_creation": "The data home can be created when it is required.",
        "unavailable": "The data home is not available.",
    }
    return messages[access]


def _managed_install_message(state: ManagedInstallState) -> str:
    messages = {
        "managed": "The managed installation state is valid.",
        "unmanaged": "ROI-H is not running from a managed installation.",
        "invalid": "The managed installation state is invalid.",
    }
    return messages[state]
