"""The observer's only installed-version-specific ActiveGraph SQLite adapter."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


class ActiveGraphProjectionAdapter:
    """Read-only run discovery and event projection for ActiveGraph 1.10."""

    def __init__(self, database: Path) -> None:
        """Bind one existing store without creating schema or directories."""
        if not database.is_file():
            msg = f"ActiveGraph store not found: {database}"
            raise FileNotFoundError(msg)
        self.database = database.resolve()

    def list_run_headers(self, *, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        """Return bounded run-registry rows newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, created_at, goal, label
                FROM runs
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self) -> str:
        """Return an opaque projection watermark."""
        with self._connect() as connection:
            row = connection.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM events").fetchone()
        return f"event:{int(row['seq'])}"

    def list_events(
        self,
        run_id: str,
        *,
        limit: int,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        """Return canonical events after one sequence."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT seq, type, payload, timestamp
                FROM events
                WHERE run_id = ? AND seq > ?
                ORDER BY seq
                LIMIT ?
                """,
                (run_id, after_sequence, limit),
            ).fetchall()
        return [
            {
                "event_id": f"event:{int(row['seq'])}",
                "sequence": int(row["seq"]),
                "type": str(row["type"]),
                "timestamp": str(row["timestamp"]),
                "data": _parse_payload(str(row["payload"] or "{}")),
            }
            for row in rows
        ]

    def run_header(self, run_id: str) -> dict[str, Any] | None:
        """Return one run-registry row."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT created_at, goal, label FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def project_run(self, run_id: str) -> dict[str, Any]:
        """Replay object creation and patches in canonical store-sequence order."""
        objects: dict[str, dict[str, Any]] = {}
        first_timestamp = ""
        last_timestamp = ""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT type, payload, timestamp
                FROM events
                WHERE run_id = ? AND type IN ('object.created', 'patch.applied')
                ORDER BY seq
                """,
                (run_id,),
            )
            for row in rows:
                timestamp = str(row["timestamp"] or "")
                first_timestamp = first_timestamp or timestamp
                last_timestamp = timestamp or last_timestamp
                payload = _parse_payload(str(row["payload"] or "{}"))
                if row["type"] == "object.created":
                    raw_object = payload.get("object")
                    if not isinstance(raw_object, dict):
                        continue
                    object_id = str(raw_object.get("id") or "")
                    data = raw_object.get("data")
                    if not object_id or not isinstance(data, dict):
                        continue
                    objects[object_id] = {
                        "id": object_id,
                        "type": str(raw_object.get("type") or ""),
                        "data": dict(data),
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                    continue
                patch = payload.get("patch")
                if not isinstance(patch, dict):
                    continue
                target = str(patch.get("target") or "")
                value = patch.get("value")
                if target in objects and isinstance(value, dict):
                    objects[target]["data"].update(value)
                    objects[target]["updated_at"] = timestamp
        return {
            "run_id": run_id,
            "objects": list(objects.values()),
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"{self.database.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection


def _parse_payload(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


__all__ = ["ActiveGraphProjectionAdapter"]
