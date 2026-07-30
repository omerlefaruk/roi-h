"""Isolated skill inspection and execution worker."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel

from roi_h.harness.skill_contract import (
    inspect_module,
    skill_tree_digest,
    strict_skill_model,
)

_MAX_CAPTURE_CHARS = 20_000
_MAX_ERROR_CHARS = 4_000


def main() -> int:
    """Inspect or execute one skill script request from standard input."""
    state = {"stage": "request"}
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    cleanup: Callable[[], contextlib.AbstractContextManager[None]] | None = None
    try:
        request = _request()
        operation = str(request.get("operation") or "invoke")
        temporary_path, temporary = _temporary_path(request.get("temporary"))
        state["stage"] = "integrity"
        script, source = _snapshot_skill(request, temporary_path)
        if operation == "inspect":
            cleanup = _guard_inspection(temporary_path)
        state["stage"] = "import"
        with (
            contextlib.redirect_stdout(captured_stdout),
            contextlib.redirect_stderr(captured_stderr),
        ):
            module = _import_script(script, source)
            if operation == "inspect":
                state["stage"] = "contract"
                metadata = inspect_module(
                    module,
                    skill=str(request.get("skill") or script.parent.parent.name),
                    default_tool_id=script.stem,
                    source=source.decode("utf-8"),
                    trusted=False,
                )
                response: dict[str, Any] = {
                    "ok": True,
                    "metadata": metadata.model_dump(mode="json"),
                }
            elif operation == "invoke":
                response = _invoke(module, request.get("args") or {}, state=state)
            else:
                response = _unknown_operation(operation)
    except Exception as exc:  # noqa: BLE001 - serialized for the parent
        response = {
            "ok": False,
            "stage": state["stage"],
            "error": _bounded(f"{type(exc).__name__}: {exc}", _MAX_ERROR_CHARS),
            "exception_type": type(exc).__name__,
        }
    finally:
        if temporary is not None:
            if cleanup is None:
                temporary.cleanup()
            else:
                with cleanup():
                    temporary.cleanup()
    response["stdout"] = _bounded(captured_stdout.getvalue(), _MAX_CAPTURE_CHARS)
    response["stderr"] = _bounded(captured_stderr.getvalue(), _MAX_CAPTURE_CHARS)
    sys.stdout.write(json.dumps(response, sort_keys=True, default=str))
    return 0 if response["ok"] else 1


def _temporary_path(
    value: object,
) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    if value is None:
        owner = tempfile.TemporaryDirectory(prefix="roi-h-skill-")
        return Path(owner.name).resolve(), owner
    if not isinstance(value, str):
        msg = "temporary must be a path string"
        raise TypeError(msg)
    path = Path(value).resolve()
    if not path.is_dir():
        msg = "declared worker temporary directory does not exist"
        raise FileNotFoundError(msg)
    return path, None


def _request() -> dict[str, Any]:
    value = json.loads(sys.stdin.read())
    if not isinstance(value, dict):
        msg = "worker request must be an object"
        raise TypeError(msg)
    return value


def _snapshot_skill(request: dict[str, Any], temporary: Path) -> tuple[Path, bytes]:
    script = Path(str(request["script"])).expanduser().resolve()
    root = Path(str(request.get("skill_root") or script.parent.parent)).expanduser().resolve()
    if not script.is_relative_to(root) or not script.is_file():
        msg = "skill script is outside its declared root"
        raise ValueError(msg)
    expected_script = _required_digest(request.get("expected_sha256"), "expected_sha256")
    expected_tree = _required_digest(
        request.get("expected_tree_sha256"),
        "expected_tree_sha256",
    )
    reject_bytecode = request.get("reject_bytecode")
    if not isinstance(reject_bytecode, bool):
        msg = "reject_bytecode must be a boolean"
        raise TypeError(msg)
    if skill_tree_digest(root, reject_bytecode=reject_bytecode) != expected_tree:
        msg = "skill tree changed after inspection"
        raise RuntimeError(msg)

    snapshot = temporary / "skill"
    shutil.copytree(root, snapshot)
    if skill_tree_digest(snapshot, reject_bytecode=reject_bytecode) != expected_tree:
        msg = "skill tree changed while the worker copied it"
        raise RuntimeError(msg)
    snapshot_script = snapshot / script.relative_to(root)
    source = snapshot_script.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected_script:
        msg = "skill script changed after inspection"
        raise RuntimeError(msg)
    return snapshot_script, source


def _required_digest(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        msg = f"{name} must be a SHA-256 digest"
        raise TypeError(msg)
    return value


def _unknown_operation(operation: str) -> dict[str, Any]:
    msg = f"unknown worker operation: {operation}"
    raise ValueError(msg)


def _invoke(module: ModuleType, payload: object, *, state: dict[str, str]) -> dict[str, Any]:
    state["stage"] = "contract"
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
        msg = "skill tool must define Input(BaseModel), Output(BaseModel), and run(args)"
        raise TypeError(msg)
    strict_input = strict_skill_model(input_model)
    strict_output = strict_skill_model(output_model)
    state["stage"] = "input"
    arguments = strict_input.model_validate(payload)
    state["stage"] = "run"
    raw = run(arguments)
    state["stage"] = "output"
    if not isinstance(raw, strict_output):
        raw = strict_output.model_validate(raw.model_dump() if isinstance(raw, BaseModel) else raw)
    return {"ok": True, "output": raw.model_dump(mode="json")}


def _guard_inspection(
    snapshot_root: Path,
) -> Callable[[], contextlib.AbstractContextManager[None]]:
    cleanup_state = threading.local()
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    read_roots = tuple(
        path.resolve()
        for path in {snapshot_root, Path(sys.prefix), Path(sys.base_prefix)}
        if path.exists()
    )
    denied_events = {
        "ctypes.dlopen",
        "shutil.copyfile",
        "subprocess.Popen",
    }
    read_only_os_events = {"os.listdir", "os.scandir"}

    def deny_effect(event: str, args: tuple[object, ...]) -> None:
        if getattr(cleanup_state, "allowed", False):
            return
        if event == "open":
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            writes = isinstance(mode, str) and any(token in mode for token in "wax+")
            writes = writes or (isinstance(flags, int) and bool(flags & write_flags))
            if writes:
                msg = "skill inspection cannot write files"
                raise PermissionError(msg)
            if args and not _read_allowed(args[0], read_roots):
                msg = "skill inspection cannot read files outside its runtime"
                raise PermissionError(msg)
        if event in read_only_os_events:
            if args and not _read_allowed(args[0], read_roots):
                msg = "skill inspection cannot inspect directories outside its runtime"
                raise PermissionError(msg)
            return
        if event in denied_events or event.startswith(("os.", "socket.")):
            msg = f"skill inspection cannot perform {event}"
            raise PermissionError(msg)

    @contextlib.contextmanager
    def allow_cleanup() -> Iterator[None]:
        cleanup_state.allowed = True
        try:
            yield
        finally:
            cleanup_state.allowed = False

    sys.addaudithook(deny_effect)
    return allow_cleanup


def _read_allowed(value: object, roots: tuple[Path, ...]) -> bool:
    if isinstance(value, int):
        return True
    try:
        path = Path(os.fsdecode(value)).expanduser().resolve()  # type: ignore[arg-type]
    except (OSError, TypeError, ValueError):
        return False
    return path == Path(os.devnull) or any(path.is_relative_to(root) for root in roots)


def _import_script(script: Path, source: bytes) -> ModuleType:
    digest = hashlib.sha256(source).hexdigest()[:16]
    module = ModuleType(f"roi_h_worker_{digest}")
    module.__file__ = str(script)
    module.__package__ = ""
    sys.modules[module.__name__] = module
    sys.dont_write_bytecode = True
    exec(compile(source, str(script), "exec"), module.__dict__)  # noqa: S102
    return module


def _bounded(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[-limit:]


if __name__ == "__main__":
    raise SystemExit(main())
