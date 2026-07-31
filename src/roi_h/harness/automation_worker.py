"""Subprocess entry point for one modular automation phase."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roi_h.harness.automation_source import PhaseResult

_PROTOCOL_LIMIT = 1_000_000
_DIAGNOSTIC_LIMIT = 64_000


@dataclass(frozen=True)
class PhaseContext:
    """Small runtime context supplied to AI-authored phase modules."""

    run_id: str
    phase_id: str
    attempt_id: str
    environment: str
    source_root: Path
    input_dir: Path
    reference_dir: Path
    work_dir: Path
    output_dir: Path
    dependencies: dict[str, dict[str, Path]]
    _secret_environment: dict[str, str]

    def secret(self, name: str) -> str:
        """Return one declared secret without exposing it in the worker request."""
        environment_name = self._secret_environment.get(name)
        if environment_name is None:
            msg = f"phase did not declare secret {name!r}"
            raise KeyError(msg)
        value = os.environ.get(environment_name)
        if value is None:
            msg = f"declared phase secret is unavailable: {name}"
            raise KeyError(msg)
        return value

    def output_path(self, relative: str) -> Path:
        """Resolve one output path and keep it inside the phase output directory."""
        target = (self.output_dir / relative).resolve()
        if not target.is_relative_to(self.output_dir.resolve()):
            msg = f"phase output path escapes its directory: {relative!r}"
            raise ValueError(msg)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


def _context(request: dict[str, Any]) -> PhaseContext:
    dependencies = {
        str(phase): {str(name): Path(str(path)) for name, path in dict(files).items()}
        for phase, files in dict(request.get("dependencies") or {}).items()
    }
    return PhaseContext(
        run_id=str(request["run_id"]),
        phase_id=str(request["phase_id"]),
        attempt_id=str(request["attempt_id"]),
        environment=str(request["environment"]),
        source_root=Path(str(request["source_root"])).resolve(),
        input_dir=Path(str(request["input_dir"])).resolve(),
        reference_dir=Path(str(request["reference_dir"])).resolve(),
        work_dir=Path(str(request["work_dir"])).resolve(),
        output_dir=Path(str(request["output_dir"])).resolve(),
        dependencies=dependencies,
        _secret_environment={
            str(name): str(environment_name)
            for name, environment_name in dict(request.get("secret_environment") or {}).items()
        },
    )


def _phase_run(module_name: str) -> Any:
    module = importlib.import_module(module_name)
    run = getattr(module, "run", None)
    if not callable(run):
        msg = f"phase module has no callable run(context): {module_name}"
        raise TypeError(msg)
    return run


def _decode_request(raw: str) -> dict[str, Any]:
    if len(raw) > _PROTOCOL_LIMIT:
        msg = "phase worker request is too large"
        raise ValueError(msg)
    request = json.loads(raw)
    if not isinstance(request, dict):
        msg = "phase worker request must be an object"
        raise TypeError(msg)
    return request


def execute(request: dict[str, Any]) -> dict[str, Any]:
    """Import and run one phase while reserving stdout for the worker protocol."""
    context = _context(request)
    context.work_dir.mkdir(parents=True, exist_ok=True)
    context.output_dir.mkdir(parents=True, exist_ok=True)
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        sys.path.insert(0, str(context.source_root))
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            run = _phase_run(str(request["module"]))
            raw_result = run(context)
            result = PhaseResult.model_validate(raw_result or {})
        return {
            "ok": True,
            "result": result.model_dump(mode="json"),
            "stdout": stdout.getvalue()[:_DIAGNOSTIC_LIMIT],
            "stderr": stderr.getvalue()[:_DIAGNOSTIC_LIMIT],
        }
    except Exception as exc:  # noqa: BLE001 - worker returns a bounded structured failure
        return {
            "ok": False,
            "error": {
                "code": "phase.execution_failed",
                "type": type(exc).__name__,
                "message": str(exc)[:4_000],
                "traceback": traceback.format_exc()[-_DIAGNOSTIC_LIMIT:],
            },
            "stdout": stdout.getvalue()[:_DIAGNOSTIC_LIMIT],
            "stderr": stderr.getvalue()[:_DIAGNOSTIC_LIMIT],
        }
    finally:
        if sys.path and sys.path[0] == str(context.source_root):
            sys.path.pop(0)


def main() -> int:
    """Read one JSON request and write one bounded JSON response."""
    try:
        raw = sys.stdin.read(_PROTOCOL_LIMIT + 1)
        response = execute(_decode_request(raw))
    except Exception as exc:  # noqa: BLE001 - protocol errors must remain structured
        response = {
            "ok": False,
            "error": {
                "code": "phase.worker_protocol_failed",
                "type": type(exc).__name__,
                "message": str(exc)[:4_000],
            },
            "stdout": "",
            "stderr": "",
        }
    sys.stdout.write(json.dumps(response, sort_keys=True))
    sys.stdout.write("\n")
    return 0 if response.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
