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


def main() -> int:
    """Execute one skill script request from stdin."""
    try:
        request = json.loads(sys.stdin.read())
        script = Path(str(request["script"])).expanduser().resolve()
        payload = request.get("args") or {}
        module = _import_script(script)
        input_model = module.Input
        output_model = module.Output
        run = module.run
        arguments = input_model.model_validate(payload)
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(captured_stdout),
            contextlib.redirect_stderr(captured_stderr),
        ):
            raw = run(arguments)
        if not isinstance(raw, output_model):
            raw = output_model.model_validate(
                raw.model_dump() if isinstance(raw, BaseModel) else raw
            )
        result = raw.model_dump(mode="json")
        response = {
            "ok": True,
            "output": result,
            "stdout": captured_stdout.getvalue()[-20_000:],
            "stderr": captured_stderr.getvalue()[-20_000:],
        }
    except Exception as exc:  # noqa: BLE001 - serialized for the parent
        response = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "exception_type": type(exc).__name__,
        }
    sys.stdout.write(json.dumps(response, sort_keys=True, default=str))
    return 0 if response["ok"] else 1


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
