"""JSON-friendly conversion helpers for harness payloads."""

from __future__ import annotations

from typing import Any, cast


def json_object(value: object) -> dict[str, Any]:
    converted = jsonable(value)
    if not isinstance(converted, dict):
        msg = f"expected JSON object payload, got {type(converted).__name__}"
        raise TypeError(msg)
    return cast("dict[str, Any]", converted)


def jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
