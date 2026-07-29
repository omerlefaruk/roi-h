"""Bounded failure diagnostics used only when ActiveGraph cannot record state."""

from __future__ import annotations

import json
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from roi_h.harness.workspace import resolve_home

DiagnosticLevel = Literal["debug", "info", "warning", "error"]
_MAX_STRING = 4_096
_MAX_COLLECTION = 100
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "environment",
        "env",
        "password",
        "secret",
        "storage_state",
        "token",
    }
)


@dataclass(frozen=True)
class DiagnosticRecord:
    """Versioned, redacted operational failure record."""

    code: str
    message: str
    component: str
    level: DiagnosticLevel = "error"
    diagnostic_id: str = field(default_factory=lambda: f"diag_{uuid.uuid4().hex}")
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: int = 1
    project_id: str | None = None
    project: str | None = None
    environment: str | None = None
    run_id: str | None = None
    invocation_id: str | None = None
    exception_type: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class DiagnosticSink:
    """Failure-isolated rotated JSONL sink under the data home."""

    def __init__(
        self,
        home: str | Path | None = None,
        *,
        max_bytes: int = 52_428_800,
        max_files: int = 5,
        known_secrets: dict[str, str] | None = None,
    ) -> None:
        """Configure one home-scoped rotated sink."""
        self.home = resolve_home(home)
        self.path = self.home / "diagnostics" / "roi-h.jsonl"
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.known_secrets = dict(known_secrets or {})

    def emit(self, record: DiagnosticRecord) -> None:
        """Best-effort emit; diagnostics never mask the original failure."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._rotate()
            raw = _redact(asdict(record), home=self.home, secrets=self.known_secrets)
            line = json.dumps(raw, sort_keys=True, default=lambda _value: "<unserializable>")
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
            self.path.chmod(0o600)
        except Exception:  # noqa: BLE001
            return

    def capture_exception(
        self,
        context: dict[str, Any],
        exception: BaseException,
    ) -> str:
        """Redact and emit a bounded exception, returning its diagnostic id."""
        details = dict(context.get("details") or {})
        details["traceback"] = "".join(
            traceback.format_exception(type(exception), exception, exception.__traceback__)
        )[-16_384:]
        record = DiagnosticRecord(
            code=str(context.get("code") or "internal.error"),
            message=str(context.get("message") or "ROI-H could not complete the operation."),
            component=str(context.get("component") or "unknown"),
            level="error",
            project_id=context.get("project_id"),
            project=context.get("project"),
            environment=context.get("environment"),
            run_id=context.get("run_id"),
            invocation_id=context.get("invocation_id"),
            exception_type=type(exception).__name__,
            details=details,
        )
        self.emit(record)
        return record.diagnostic_id

    def read(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Read a bounded tail of persisted diagnostic records."""
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        items: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 10_000)) :]:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                items.append(raw)
        return items

    def _rotate(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        for index in range(self.max_files - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            destination = self.path.with_name(f"{self.path.name}.{index + 1}")
            if source.exists():
                if index + 1 >= self.max_files:
                    source.unlink()
                else:
                    source.replace(destination)
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))


def _redact(value: Any, *, home: Path, secrets: dict[str, str], key: str = "") -> Any:
    if key.casefold() in _SENSITIVE_KEYS:
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(child_key): _redact(
                child,
                home=home,
                secrets=secrets,
                key=str(child_key),
            )
            for child_key, child in list(value.items())[:_MAX_COLLECTION]
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _redact(child, home=home, secrets=secrets, key=key)
            for child in list(value)[:_MAX_COLLECTION]
        ]
    if isinstance(value, str):
        result = value.replace(str(home), "<ROI_H_HOME>")
        for name, secret in secrets.items():
            if secret:
                result = result.replace(secret, f"{{{{secret.{name}}}}}")
        return result[:_MAX_STRING]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


__all__ = ["DiagnosticRecord", "DiagnosticSink"]
