"""Safe environment and health checks for isolated tool processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

_PORTABLE_ENV_NAMES = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "PYTHONPATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "VIRTUAL_ENV",
    }
)
_WINDOWS_RUNTIME_ENV_NAMES = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)
_PROBE_SOURCE = """
import json
import socket
import ssl

result = {}
try:
    probe_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_socket.close()
except Exception as exc:
    result["socket"] = {
        "ok": False,
        "exception_type": type(exc).__name__,
        "message": str(exc),
    }
else:
    result["socket"] = {"ok": True}

try:
    ssl.create_default_context()
except Exception as exc:
    result["tls"] = {
        "ok": False,
        "exception_type": type(exc).__name__,
        "message": str(exc),
    }
else:
    result["tls"] = {"ok": True}

print(json.dumps(result, sort_keys=True))
"""


@dataclass(frozen=True, slots=True)
class RuntimeBootstrapCheck:
    """One isolated-runtime bootstrap check."""

    code: str
    ok: bool
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible check."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RuntimeBootstrapReport:
    """Socket and TLS bootstrap health for the exact isolated environment."""

    healthy: bool
    checks: tuple[RuntimeBootstrapCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible report."""
        return {
            "healthy": self.healthy,
            "checks": [check.to_dict() for check in self.checks],
        }


def isolated_process_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the bounded base environment used by isolated tools."""
    values = os.environ if source is None else source
    allowed = set(_PORTABLE_ENV_NAMES)
    if os.name == "nt":
        allowed.update(_WINDOWS_RUNTIME_ENV_NAMES)
    return {key: value for key, value in values.items() if key.upper() in allowed}


def inspect_isolated_runtime_bootstrap(
    source: Mapping[str, str] | None = None,
) -> RuntimeBootstrapReport:
    """Probe socket and TLS setup without contacting a remote host."""
    environment = isolated_process_environment(source)
    try:
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _PROBE_SOURCE],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks = tuple(
            _failed_check(name, type(exc).__name__, str(exc))
            for name in ("socket", "tls")
        )
        return RuntimeBootstrapReport(healthy=False, checks=checks)

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        error = completed.stderr.strip() or completed.stdout.strip() or "probe returned no JSON"
        checks = tuple(
            _failed_check(name, "RuntimeProbeError", error)
            for name in ("socket", "tls")
        )
        return RuntimeBootstrapReport(healthy=False, checks=checks)

    checks = tuple(_check_from_payload(name, payload.get(name)) for name in ("socket", "tls"))
    return RuntimeBootstrapReport(
        healthy=completed.returncode == 0 and all(check.ok for check in checks),
        checks=checks,
    )


def _check_from_payload(name: str, payload: object) -> RuntimeBootstrapCheck:
    if isinstance(payload, dict) and payload.get("ok") is True:
        return RuntimeBootstrapCheck(
            code=f"runtime.{name}_bootstrap",
            ok=True,
            message=f"The isolated {name} runtime is available.",
            details={},
        )
    if isinstance(payload, dict):
        return _failed_check(
            name,
            str(payload.get("exception_type") or "RuntimeProbeError"),
            str(payload.get("message") or "bootstrap failed"),
        )
    return _failed_check(name, "RuntimeProbeError", "probe result is missing")


def _failed_check(
    name: str,
    exception_type: str,
    message: str,
) -> RuntimeBootstrapCheck:
    details: dict[str, Any] = {
        "exception_type": exception_type,
        "error": message[:1_000],
        "remediation": (
            "Update or reinstall ROI-H so isolated tools inherit the required "
            "Windows runtime variables. Do not reset Winsock unless socket tests "
            "outside ROI-H also fail."
            if os.name == "nt"
            else "Check the Python runtime and configured certificate paths."
        ),
    }
    if os.name == "nt":
        details["required_environment"] = ["SystemRoot", "WINDIR", "SYSTEMDRIVE"]
    return RuntimeBootstrapCheck(
        code=f"runtime.{name}_bootstrap",
        ok=False,
        message=f"The isolated {name} runtime cannot start.",
        details=details,
    )


__all__ = [
    "RuntimeBootstrapCheck",
    "RuntimeBootstrapReport",
    "inspect_isolated_runtime_bootstrap",
    "isolated_process_environment",
]
