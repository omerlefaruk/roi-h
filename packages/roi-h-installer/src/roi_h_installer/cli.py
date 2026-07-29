"""Noninteractive command-line adapter for the ROI-H installer."""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Never

from pydantic import ValidationError

import roi_h_installer
from roi_h_installer import (
    ErrorCategory,
    InstallationState,
    InstallerError,
    InstallerErrorCode,
    InstallerFailure,
    InstallOperation,
    InstallRequest,
    InstallResult,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class InstallerUsageError(ValueError):
    """Invalid installer command syntax."""


class _MachineParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise InstallerUsageError(message)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one installer command and emit one JSON result."""
    parser = _MachineParser(prog="roi-h-installer")
    subcommands = parser.add_subparsers(dest="command", required=True)
    inspect_command = subcommands.add_parser("inspect")
    inspect_command.add_argument("--output", choices=["json"], required=True)
    for command_name in ("install", "update"):
        change_command = subcommands.add_parser(command_name)
        change_command.add_argument("--release-description", type=Path, required=True)
        change_command.add_argument("--install-root", type=Path, required=True)
        change_command.add_argument("--data-home", type=Path, required=True)
        change_command.add_argument("--channel", default="stable")
        change_command.add_argument("--version", default=None)
        change_command.add_argument("--output", choices=["json"], required=True)
    result: InstallationState | InstallResult
    try:
        args = parser.parse_args(argv)
    except InstallerUsageError:
        failure = InstallerFailure(
            code=InstallerErrorCode.INSTALL_OPERATION_NOT_SUPPORTED,
            category=ErrorCategory.VALIDATION,
            message="The installer command syntax is invalid.",
            retryable=False,
            diagnostic_id=None,
            recovery_action="Use the inspect or install command with the required options.",
        )
        sys.stdout.write(failure.model_dump_json() + "\n")
        return 2

    try:
        if args.command == "inspect":
            result = roi_h_installer.inspect()
        else:
            operation = (
                InstallOperation.INSTALL if args.command == "install" else InstallOperation.UPDATE
            )
            request = InstallRequest(
                request_id=f"req_{args.command}_{secrets.token_hex(16)}",
                operation=operation,
                install_root=args.install_root,
                data_home=args.data_home,
                channel=args.channel,
                version=args.version,
                release_description=args.release_description,
            )
            install_plan = roi_h_installer.plan(request)
            result = roi_h_installer.apply(install_plan)
    except InstallerError as exc:
        sys.stdout.write(exc.failure.model_dump_json() + "\n")
        return 1
    except ValidationError as exc:
        invalid_field = exc.errors()[0]["loc"][0]
        if invalid_field == "channel":
            code = InstallerErrorCode.RELEASE_CHANNEL_NOT_FOUND
            message = "The installer command contains an invalid release channel."
            recovery_action = "Use a release channel such as stable."
        else:
            code = InstallerErrorCode.RELEASE_VERSION_NOT_FOUND
            message = "The installer command contains an invalid release version."
            recovery_action = "Use an exact release version such as 1.2.3."
        failure = InstallerFailure(
            code=code,
            category=ErrorCategory.VALIDATION,
            message=message,
            retryable=False,
            diagnostic_id=None,
            recovery_action=recovery_action,
        )
        sys.stdout.write(failure.model_dump_json() + "\n")
        return 2
    except KeyboardInterrupt:
        failure = InstallerFailure(
            code=InstallerErrorCode.INSTALL_OPERATION_NOT_SUPPORTED,
            category=ErrorCategory.INTERNAL,
            message="The installer command was interrupted.",
            retryable=True,
            diagnostic_id=None,
            recovery_action="Inspect the installation state before you retry the command.",
        )
        sys.stdout.write(failure.model_dump_json() + "\n")
        return 130
    sys.stdout.write(result.model_dump_json() + "\n")
    return 0


__all__ = ["main"]
