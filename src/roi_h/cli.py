"""Small command-line boundary for the typed ROI-H agent interface."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from roi_h import __version__
from roi_h.agent.cli import main as agent_main
from roi_h.agent_instructions import (
    INSTRUCTIONS_VERSION,
    MANAGED_INSTRUCTIONS,
    install_agent_instructions,
    instruction_paths,
)
from roi_h.harness.guidance_skills import default_guidance_root
from roi_h.harness.workspace import resolve_home
from roi_h.installation import default_install_root, inspect_installation_health


def main(argv: Sequence[str] | None = None) -> int:
    """Run the supported ROI-H command-line interface."""
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw[:1] == ["agent"]:
        return agent_main(raw[1:])
    parser = _parser()
    args = parser.parse_args(raw)
    try:
        result = args.handler(args)
    except (
        FileExistsError,
        FileNotFoundError,
        TypeError,
        ValueError,
        OSError,
        RuntimeError,
    ) as exc:
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    if result is not None:
        _emit(result)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roi-h",
        description="ROI-H typed interface for log-based modular automations.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    version = sub.add_parser("version", help="Show the installed version.")
    version.add_argument("--output", choices=["text", "json"], default="text")
    version.set_defaults(handler=_version)

    doctor = sub.add_parser("doctor", help="Check the installed application and data home.")
    doctor.add_argument("--home", default=None)
    doctor.add_argument("--install-root", default=str(default_install_root()))
    doctor.add_argument("--output", choices=["json"], default="json")
    doctor.set_defaults(handler=_doctor)

    update = sub.add_parser("update", help="Run the external installation updater.")
    update.add_argument("--home", default=None)
    update.add_argument("--install-root", default=str(default_install_root()))
    update.add_argument("--output", choices=["json"], default="json")
    update.set_defaults(handler=_update)

    instructions = sub.add_parser("instructions", help="Show or install AI instructions.")
    instructions.add_argument("--install", action="store_true")
    instructions.add_argument("--user-home", default=None)
    instructions.add_argument("--output", choices=["text", "json"], default="text")
    instructions.set_defaults(handler=_instructions)
    return parser


def _version(args: argparse.Namespace) -> dict[str, Any] | None:
    identity = {"name": "roi-h", "version": __version__}
    if args.output == "json":
        return identity
    sys.stdout.write(f"roi-h {__version__}\n")
    return None


def _doctor(args: argparse.Namespace) -> dict[str, Any]:
    report = inspect_installation_health(
        install_root=Path(args.install_root).expanduser().resolve(),
        data_home=resolve_home(args.home),
        application_version=__version__,
        skills_root=default_guidance_root(),
    )
    return {**report.to_dict(), "ok": report.healthy}


def _update(args: argparse.Namespace) -> dict[str, Any]:
    install_root = Path(args.install_root).expanduser().resolve()
    data_home = resolve_home(args.home)
    helper_name = "update.ps1" if os.name == "nt" else "update.sh"
    helper = install_root / "installer" / helper_name
    if not helper.is_file():
        msg = "This installation has no external updater helper. Run the one-line installer again."
        raise RuntimeError(msg)
    command = (
        ["pwsh.exe", "-NoProfile", "-NonInteractive", "-File", str(helper)]
        if os.name == "nt"
        else [str(helper)]
    )
    environment = os.environ.copy()
    environment["ROI_H_INSTALL_ROOT"] = str(install_root)
    environment["ROI_H_HOME"] = str(data_home)
    completed = subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or "The external updater helper failed.")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        msg = "The external updater helper returned no result."
        raise RuntimeError(msg)
    value = json.loads(lines[-1])
    if not isinstance(value, dict):
        msg = "The external updater helper returned an invalid result."
        raise TypeError(msg)
    return cast("dict[str, Any]", value)


def _instructions(args: argparse.Namespace) -> dict[str, Any] | None:
    user_home = Path(args.user_home).expanduser().resolve() if args.user_home else None
    if not args.install:
        if args.output == "json":
            return {
                "ok": True,
                "version": INSTRUCTIONS_VERSION,
                "instructions": MANAGED_INSTRUCTIONS,
                "files": [str(path) for path in instruction_paths(user_home)],
            }
        sys.stdout.write(f"{MANAGED_INSTRUCTIONS}\n")
        return None
    files = install_agent_instructions(user_home)
    result = {
        "ok": True,
        "version": INSTRUCTIONS_VERSION,
        "changed": any(changed for _, changed in files),
        "files": [{"path": str(path), "changed": changed} for path, changed in files],
    }
    if args.output == "json":
        return result
    for path, changed in files:
        status = "updated" if changed else "unchanged"
        sys.stdout.write(f"{status}: {path}\n")
    return None


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True) + "\n")


__all__ = ["main"]
