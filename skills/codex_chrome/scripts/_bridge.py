"""Deterministic, file-backed fake Codex Chrome provider lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1
_BRIDGE_VERSION = "fake-1.0"
_EXTENSION_VERSION = "fake-1.0"
_CAPABILITIES = (
    "session.start",
    "session.attach",
    "session.status",
    "session.stop",
)
_FAILURE_MODES = {"missing", "incompatible", "unavailable"}


class FakeBridge:
    """Claim one named binding and expose a deterministic fake session."""

    def start(self, profile_binding: str, mode: str) -> dict[str, Any]:  # noqa: C901, PLR0911
        """Start or attach to one exclusive profile binding."""
        event_id = self._event_id("start", profile_binding)
        failure = self._provider_failure(profile_binding, event_id)
        if failure is not None:
            return failure
        run_id = self._run_id()
        root = self._state_root()
        if run_id is None or root is None:
            return self._failed(
                profile_binding,
                event_id,
                "provider.unavailable",
                "The fake provider has no complete ROI-H run scope.",
                retryable=False,
                remediation=(("codex_chrome.start", "Run the provider from an active ROI-H run."),),
            )
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self._lock_path(root, profile_binding)
        if lock_path.exists():
            claim, error = self._read_claim(lock_path)
            if error is not None:
                return self._failed(
                    profile_binding,
                    event_id,
                    "provider.incompatible",
                    error,
                    remediation=(
                        ("codex_chrome.stop", "Reconcile the provider binding before retry."),
                    ),
                )
            if claim is None:
                return self._failed(
                    profile_binding,
                    event_id,
                    "provider.incompatible",
                    "The provider claim is incomplete.",
                    remediation=(
                        ("codex_chrome.stop", "Reconcile the provider binding before retry."),
                    ),
                )
            if claim["owner_run_id"] == run_id:
                return self._success(claim, "active", event_id)
            return self._failed(
                profile_binding,
                event_id,
                "profile.in_use",
                "The profile binding is owned by another active ROI-H run.",
                retryable=True,
                remediation=(
                    ("codex_chrome.status", "Wait for the owning run to release the binding."),
                ),
            )

        for other_path in sorted(root.glob("*.lock")):
            claim, error = self._read_claim(other_path)
            if error is not None:
                return self._failed(
                    profile_binding,
                    event_id,
                    "provider.incompatible",
                    error,
                    remediation=(
                        ("codex_chrome.stop", "Reconcile the provider binding before retry."),
                    ),
                )
            if claim is not None and claim["owner_run_id"] == run_id:
                return self._failed(
                    profile_binding,
                    event_id,
                    "session.already_active",
                    "This ROI-H run already owns a Codex Chrome session.",
                    remediation=(
                        ("codex_chrome.stop", "Stop the active session before starting another."),
                    ),
                )

        ownership = "attached" if mode == "attach" else "started"
        handshake = self._handshake(profile_binding)
        claim = {
            "schema_version": _SCHEMA_VERSION,
            "profile_binding": profile_binding,
            "owner_run_id": run_id,
            "ownership": ownership,
            "session_id": self._opaque_id("session", f"{run_id}:{profile_binding}"),
            "tab_id": self._opaque_id("tab", f"{run_id}:{profile_binding}"),
            "handshake": handshake,
        }
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return self.start(profile_binding, mode)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(claim, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise
        return self._success(claim, "active", event_id)

    def status(self, profile_binding: str) -> dict[str, Any]:
        """Return the active run-owned session or a typed missing result."""
        event_id = self._event_id("status", profile_binding)
        failure = self._provider_failure(profile_binding, event_id)
        if failure is not None:
            return failure
        run_id = self._run_id()
        root = self._state_root()
        if run_id is None or root is None:
            return self._failed(
                profile_binding,
                event_id,
                "provider.unavailable",
                "The fake provider has no complete ROI-H run scope.",
                remediation=(("codex_chrome.start", "Run the provider from an active ROI-H run."),),
            )
        lock_path = self._lock_path(root, profile_binding)
        if not lock_path.is_file():
            return self._failed(
                profile_binding,
                event_id,
                "session.missing",
                "No Codex Chrome session is active for this run and binding.",
                remediation=(("codex_chrome.start", "Start the named profile binding first."),),
            )
        claim, error = self._read_claim(lock_path)
        if error is not None or claim is None:
            return self._failed(
                profile_binding,
                event_id,
                "provider.incompatible",
                error or "The provider claim is invalid.",
                remediation=(
                    ("codex_chrome.stop", "Reconcile the provider binding before retry."),
                ),
            )
        if claim["owner_run_id"] != run_id:
            return self._failed(
                profile_binding,
                event_id,
                "profile.in_use",
                "The profile binding is owned by another active ROI-H run.",
                retryable=True,
                remediation=(
                    ("codex_chrome.status", "Wait for the owning run to release the binding."),
                ),
            )
        return self._success(claim, "active", event_id)

    def stop(self, profile_binding: str) -> dict[str, Any]:
        """Close a started profile or detach from an attached profile."""
        event_id = self._event_id("stop", profile_binding)
        failure = self._provider_failure(profile_binding, event_id)
        if failure is not None:
            return failure
        run_id = self._run_id()
        root = self._state_root()
        if run_id is None or root is None:
            return self._failed(
                profile_binding,
                event_id,
                "provider.unavailable",
                "The fake provider has no complete ROI-H run scope.",
                remediation=(("codex_chrome.start", "Run the provider from an active ROI-H run."),),
            )
        lock_path = self._lock_path(root, profile_binding)
        if not lock_path.is_file():
            return self._failed(
                profile_binding,
                event_id,
                "session.missing",
                "No Codex Chrome session is active for this run and binding.",
                remediation=(("codex_chrome.start", "Start the named profile binding first."),),
            )
        claim, error = self._read_claim(lock_path)
        if error is not None or claim is None:
            return self._failed(
                profile_binding,
                event_id,
                "provider.incompatible",
                error or "The provider claim is invalid.",
                remediation=(
                    ("codex_chrome.stop", "Reconcile the provider binding before retry."),
                ),
            )
        if claim["owner_run_id"] != run_id:
            return self._failed(
                profile_binding,
                event_id,
                "profile.in_use",
                "Only the owning ROI-H run can stop this profile binding.",
                retryable=True,
                remediation=(
                    ("codex_chrome.status", "Wait for the owning run to release the binding."),
                ),
            )
        lock_path.unlink(missing_ok=True)
        status = "closed" if claim["ownership"] == "started" else "detached"
        return self._success(claim, status, event_id)

    def _provider_failure(
        self,
        profile_binding: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        mode = os.environ.get("ROI_H_CODEX_CHROME_FAKE_MODE", "ready").strip().lower()
        if mode not in _FAILURE_MODES:
            return None
        messages = {
            "missing": "The Codex Chrome provider is not installed.",
            "incompatible": "The Codex Chrome provider handshake is incompatible.",
            "unavailable": "The Codex Chrome provider is unavailable.",
        }
        remediation = {
            "missing": ("codex_chrome.start", "Install and enable the Codex Chrome provider."),
            "incompatible": ("codex_chrome.start", "Install a compatible provider version."),
            "unavailable": ("codex_chrome.start", "Retry after the provider becomes available."),
        }[mode]
        return self._failed(
            profile_binding,
            event_id,
            f"provider.{mode}",
            messages[mode],
            retryable=mode == "unavailable",
            remediation=(remediation,),
        )

    def _state_root(self) -> Path | None:
        home = os.environ.get("ROI_H_HOME")
        project = os.environ.get("ROI_H_PROJECT")
        environment = os.environ.get("ROI_H_ENV")
        if not home or not project or environment not in {"dev", "prod"}:
            return None
        return (
            Path(home).expanduser().resolve()
            / "projects"
            / project
            / "environments"
            / environment
            / "runtime"
            / "codex_chrome"
        )

    def _run_id(self) -> str | None:
        value = os.environ.get("ROI_H_RUN_ID", "").strip()
        return value or None

    def _lock_path(self, root: Path, profile_binding: str) -> Path:
        return root / f"{profile_binding}.lock"

    def _read_claim(self, path: Path) -> tuple[dict[str, Any] | None, str | None]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, "The provider claim is not valid JSON."
        if not isinstance(raw, dict) or raw.get("schema_version") != _SCHEMA_VERSION:
            return None, "The provider claim schema is not supported."
        required = {
            "profile_binding",
            "owner_run_id",
            "ownership",
            "session_id",
            "tab_id",
            "handshake",
        }
        if not required.issubset(raw):
            return None, "The provider claim is incomplete."
        if raw["ownership"] not in {"started", "attached"}:
            return None, "The provider claim ownership is not supported."
        return raw, None

    def _handshake(self, profile_binding: str) -> dict[str, Any]:
        return {
            "bridge_version": _BRIDGE_VERSION,
            "extension_version": _EXTENSION_VERSION,
            "profile_identity": self._opaque_id(
                "profile",
                f"{os.environ.get('ROI_H_PROJECT', '')}:{os.environ.get('ROI_H_ENV', '')}:"
                f"{profile_binding}",
            ),
            "capabilities": list(_CAPABILITIES),
        }

    def _event_id(self, action: str, profile_binding: str) -> str:
        key = os.environ.get("ROI_H_IDEMPOTENCY_KEY") or self._run_id() or "unscoped"
        return self._opaque_id("event", f"{key}:{action}:{profile_binding}")

    @staticmethod
    def _opaque_id(prefix: str, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return f"cx_{prefix}_{digest}"

    @staticmethod
    def _success(claim: dict[str, Any], status: str, event_id: str) -> dict[str, Any]:
        return {
            "ok": True,
            "status": status,
            "profile_binding": claim["profile_binding"],
            "ownership": claim["ownership"],
            "session_id": claim["session_id"],
            "tab_id": claim["tab_id"],
            "handshake": claim["handshake"],
            "provider_event_id": event_id,
            "error": None,
        }

    @staticmethod
    def _failed(  # noqa: PLR0913
        profile_binding: str,
        event_id: str,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        remediation: tuple[tuple[str, str], ...] = (),
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "missing" if code == "session.missing" else "failed",
            "profile_binding": profile_binding,
            "provider_event_id": event_id,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "remediation": [
                    {"operation": operation, "reason": reason} for operation, reason in remediation
                ],
            },
        }
