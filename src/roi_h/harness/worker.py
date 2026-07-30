"""Isolated skill-script worker.

The parent sends one JSON request over stdin. Tool stdout/stderr are captured so
stdout remains a single machine-readable response.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel

_CAPTURE_LIMIT = 20_000
WORKER_PROTOCOL_LIMIT = 1_000_000


class _BoundedBuffer(io.TextIOBase):
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._value = ""

    def write(self, value: str) -> int:
        self._value = (self._value + value[-self._limit :])[-self._limit :]
        return len(value)

    def getvalue(self) -> str:
        return self._value


def main() -> int:
    """Inspect or execute one skill script request from stdin."""
    response_path: Path | None = None
    try:
        request = json.loads(sys.stdin.read())
        response_path = Path(str(request["response_path"])).expanduser().resolve()
        script = Path(str(request["script"])).expanduser().resolve()
        action = str(request.get("action") or "run")
        captured_stdout = _BoundedBuffer(_CAPTURE_LIMIT)
        captured_stderr = _BoundedBuffer(_CAPTURE_LIMIT)
        with (
            contextlib.redirect_stdout(captured_stdout),
            contextlib.redirect_stderr(captured_stderr),
        ):
            module = _import_script(script)
            if action == "inspect":
                result = _inspect_module(module, script)
            elif action == "run":
                result = _run_module(module, request.get("args") or {})
            else:
                msg = f"unknown worker action: {action}"
                raise ValueError(msg)  # noqa: TRY301
        response = {
            "ok": True,
            "inspection" if action == "inspect" else "output": result,
            "stdout": captured_stdout.getvalue(),
            "stderr": captured_stderr.getvalue(),
        }
    except Exception as exc:  # noqa: BLE001 - serialized for the parent
        response = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "exception_type": type(exc).__name__,
        }
    if response_path is None:
        return 1
    encoded, within_limit = _encode_response(response)
    response_path.write_text(encoded, encoding="utf-8")
    return 0 if response["ok"] and within_limit else 1


def _encode_response(response: dict[str, object]) -> tuple[str, bool]:
    chunks: list[str] = []
    size = 0
    for chunk in json.JSONEncoder(sort_keys=True, default=str).iterencode(response):
        size += len(chunk)
        if size > WORKER_PROTOCOL_LIMIT:
            return (
                json.dumps(
                    {
                        "ok": False,
                        "error": f"worker response exceeds {WORKER_PROTOCOL_LIMIT} characters",
                        "exception_type": "WorkerResponseTooLarge",
                    },
                    sort_keys=True,
                ),
                False,
            )
        chunks.append(chunk)
    return "".join(chunks), True


def _inspect_module(module: ModuleType, script: Path) -> dict[str, object]:
    input_model = getattr(module, "Input", None)
    output_model = getattr(module, "Output", None)
    run = getattr(module, "run", None)
    if not (
        isinstance(input_model, type)
        and issubclass(input_model, BaseModel)
        and isinstance(output_model, type)
        and issubclass(output_model, BaseModel)
        and callable(run)
    ):
        msg = (
            f"{script} must define Input(BaseModel), Output(BaseModel), "
            "and run(args: Input) -> Output"
        )
        raise TypeError(msg)
    result: dict[str, object] = {
        "DETERMINISTIC": bool(getattr(module, "DETERMINISTIC", False)),
        "REQUIRES_APPROVAL": bool(getattr(module, "REQUIRES_APPROVAL", False)),
        "SECRET_NAMES": [str(item) for item in getattr(module, "SECRET_NAMES", ())],
        "NETWORK_HOSTS": [str(item) for item in getattr(module, "NETWORK_HOSTS", ())],
        "FILESYSTEM_ROOTS": [str(item) for item in getattr(module, "FILESYSTEM_ROOTS", ())],
        "Input": input_model.model_json_schema(),
        "Output": output_model.model_json_schema(),
    }
    for name in (
        "TOOL_ID",
        "DESCRIPTION",
        "TOOL_EFFECT",
        "IDEMPOTENCY",
        "ALLOW_IN_PROD",
        "TIMEOUT_SECONDS",
    ):
        if hasattr(module, name):
            result[name] = getattr(module, name)
    return result


def _run_module(module: ModuleType, payload: object) -> dict[str, object]:
    input_model = module.Input
    output_model = module.Output
    arguments = input_model.model_validate(payload)
    raw = module.run(arguments)
    if not isinstance(raw, output_model):
        raw = output_model.model_validate(raw.model_dump() if isinstance(raw, BaseModel) else raw)
    result = raw.model_dump(mode="json")
    if not isinstance(result, dict):
        msg = "skill output must be an object"
        raise TypeError(msg)
    return result


def _import_script(script: Path) -> ModuleType:
    if not script.is_file():
        msg = f"skill script not found: {script}"
        raise FileNotFoundError(msg)
    digest = hashlib.sha256(str(script).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(f"roi_h_worker_{digest}", script)
    if spec is None or spec.loader is None:
        msg = f"cannot import skill script: {script}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    raise SystemExit(main())
