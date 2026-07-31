"""ActiveGraph-backed runner for modular Python phase automations."""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from activegraph import Event, Graph

from roi_h.harness.activegraph_runtime import ROIHRuntime
from roi_h.harness.atomicfs import atomic_write_text, hash_file
from roi_h.harness.automation_source import (
    PhaseResult,
    PhaseSpec,
    SourceSnapshot,
    snapshot_source,
    source_tree_digest,
)
from roi_h.harness.control import cancellation_request
from roi_h.harness.records import ArtifactRecord
from roi_h.harness.run_storage import ArtifactAttachment, RunStorage
from roi_h.harness.runtime_environment import isolated_process_environment
from roi_h.harness.secrets import get_secret
from roi_h.harness.workspace import Workspace

_WORKER_RESPONSE_LIMIT = 1_200_000
_MAX_ARTIFACT_BYTES = 100_000_000
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"']+")
_POSIX_PATH = re.compile(r"(?<![:/A-Za-z0-9_])/(?:[^/\s\"']+/)*[^/\s\"']+")


@dataclass(frozen=True)
class PhaseExecution:
    """One completed phase subprocess result before graph materialization."""

    phase: PhaseSpec
    attempt_id: str
    ok: bool
    result: PhaseResult | None
    error: dict[str, Any] | None
    stdout: str
    stderr: str
    output_dir: Path
    secret_values: tuple[str, ...]


@dataclass(frozen=True)
class PhaseJob:
    """Inputs for one isolated phase subprocess."""

    workspace: Workspace
    snapshot: SourceSnapshot
    work_root: Path
    input_dir: Path
    reference_dir: Path
    phase: PhaseSpec
    dependencies: dict[str, dict[str, str]]
    run_id: str
    attempt_id: str


def run_source(
    workspace: Workspace,
    source_root: str | Path,
    *,
    run_id: str,
    goal: str | None = None,
    actor: str = "ai",
    automation_version: str | None = None,
    package_digest: str | None = None,
    expected_source_digest: str | None = None,
    lease_held: bool = False,
    run_reserved: bool = False,
) -> dict[str, Any]:
    """Snapshot and run one modular automation under one ActiveGraph run."""
    storage = RunStorage(workspace)
    if not run_reserved:
        storage.reserve(run_id)
    paths = storage.activate(run_id, lease_held=lease_held)
    snapshot = snapshot_source(source_root, paths.work / "source")
    if expected_source_digest is not None and snapshot.source_digest != expected_source_digest:
        storage.finalize(run_id, status="failed")
        msg = "production source does not match the verified package source digest"
        raise ValueError(msg)
    runtime = _runtime(workspace, run_id)
    manifest = snapshot.manifest
    inputs = _input_evidence(paths.input)
    run = runtime.graph.add_object(
        "rpa.run",
        {
            "goal": goal or f"run {manifest.name}",
            "status": "open",
            "actor": actor,
            "env": workspace.env,
            "automation_name": manifest.name,
            "automation_version": automation_version,
            "package_digest": package_digest,
            "source_digest": snapshot.source_digest,
            "phase_plan": [
                phase.model_dump(mode="json", by_alias=True) for phase in manifest.phases
            ],
            "inputs": inputs,
        },
    )
    if inputs:
        _emit(
            runtime,
            "run.inputs_materialized",
            {"run_id": run_id, "inputs": inputs},
            actor=actor,
        )
    _emit(
        runtime,
        "source.snapshotted",
        {
            "run_id": run_id,
            "automation": manifest.name,
            "source_digest": snapshot.source_digest,
            "files": snapshot.files,
        },
        actor=actor,
    )

    phase_by_id = {phase.phase_id: phase for phase in manifest.phases}
    order = {phase_id: index + 1 for index, phase_id in enumerate(manifest.ordered_phase_ids())}
    states = dict.fromkeys(phase_by_id, "pending")
    results: dict[str, dict[str, Any]] = {}
    phase_objects: dict[str, str] = {}
    failure_seen = False

    while any(state == "pending" for state in states.values()):
        if not _source_intact(snapshot):
            for phase_id, state in list(states.items()):
                if state == "pending":
                    states[phase_id] = "failed"
                    _record_unstarted_phase(
                        runtime,
                        phase_by_id[phase_id],
                        order[phase_id],
                        "failed",
                        "frozen automation source changed",
                    )
            failure_seen = True
            break
        if cancellation_request(workspace, run_id):
            for phase_id, state in list(states.items()):
                if state == "pending":
                    states[phase_id] = "cancelled"
                    _record_unstarted_phase(
                        runtime,
                        phase_by_id[phase_id],
                        order[phase_id],
                        "cancelled",
                        "run cancellation requested",
                    )
            failure_seen = True
            break

        for phase_id, state in list(states.items()):
            if state != "pending":
                continue
            dependency_states = [states[item] for item in phase_by_id[phase_id].needs]
            if any(item in {"failed", "blocked", "cancelled"} for item in dependency_states):
                states[phase_id] = "blocked"
                failure_seen = True
                _record_unstarted_phase(
                    runtime,
                    phase_by_id[phase_id],
                    order[phase_id],
                    "blocked",
                    "a dependency did not succeed",
                )

        ready = [
            phase
            for phase in manifest.phases
            if states[phase.phase_id] == "pending"
            and all(states[item] == "done" for item in phase.needs)
        ]
        if not ready:
            if any(state == "pending" for state in states.values()):
                msg = "automation scheduler cannot advance"
                raise RuntimeError(msg)
            break
        wave = _select_wave(ready, manifest.max_parallel)
        dependencies = _dependency_paths(results)
        jobs: list[PhaseJob] = []
        for phase in wave:
            states[phase.phase_id] = "running"
            attempt_id = f"attempt_{uuid.uuid4().hex[:16]}"
            diagnostics = _diagnostic_references(phase.phase_id, attempt_id)
            phase_object = runtime.graph.add_object(
                "rpa.phase",
                {
                    "run_id": run_id,
                    "name": phase.phase_id,
                    "index": order[phase.phase_id],
                    "status": "open",
                    "role": phase.role,
                    "needs": list(phase.needs),
                    "source_digest": snapshot.source_digest,
                    "attempt": 1,
                    "attempt_id": attempt_id,
                    "diagnostics": diagnostics,
                    "artifact_names": [],
                    "summary": {},
                },
            )
            phase_objects[phase.phase_id] = phase_object.id
            _emit(
                runtime,
                "phase.started",
                {
                    "run_id": run_id,
                    "phase_id": phase.phase_id,
                    "phase_object_id": phase_object.id,
                    "attempt_id": attempt_id,
                    "source_digest": snapshot.source_digest,
                    "needs": list(phase.needs),
                    "diagnostics": diagnostics,
                },
                actor="runtime",
            )
            jobs.append(
                PhaseJob(
                    workspace=workspace,
                    snapshot=snapshot,
                    work_root=paths.work,
                    input_dir=paths.input,
                    reference_dir=workspace.reference,
                    phase=phase,
                    dependencies=dependencies,
                    run_id=run_id,
                    attempt_id=attempt_id,
                )
            )
        with ThreadPoolExecutor(max_workers=len(wave)) as executor:
            futures = [executor.submit(_execute_phase, job) for job in jobs]
            executions = [future.result() for future in futures]
        if not _source_intact(snapshot):
            executions = [
                replace(
                    execution,
                    ok=False,
                    result=None,
                    error={
                        "code": "source.integrity_failed",
                        "message": "frozen automation source changed during phase execution",
                    },
                )
                for execution in executions
            ]

        for raw_execution in executions:
            execution = _sanitize_execution(
                raw_execution,
                physical_roots=(workspace.root, paths.root, snapshot.root),
            )
            phase_id = execution.phase.phase_id
            phase_object_id = phase_objects[phase_id]
            _write_diagnostics(paths.diagnostics, execution)
            if not execution.ok or execution.result is None:
                states[phase_id] = "failed"
                failure_seen = True
                error = execution.error or {
                    "code": "phase.execution_failed",
                    "message": "phase worker failed without an error result",
                }
                runtime.graph.patch_object(
                    phase_object_id,
                    {"status": "failed", "error": error, "attempt_id": execution.attempt_id},
                )
                _emit(
                    runtime,
                    "phase.failed",
                    {
                        "run_id": run_id,
                        "phase_id": phase_id,
                        "phase_object_id": phase_object_id,
                        "attempt_id": execution.attempt_id,
                        "source_digest": snapshot.source_digest,
                        "error": error,
                    },
                    actor="runtime",
                )
                results[phase_id] = {"status": "failed", "error": error, "artifacts": {}}
                continue

            try:
                attachments = _attach_phase_artifacts(
                    runtime,
                    storage,
                    run_id,
                    phase_object_id,
                    execution,
                )
            except (OSError, ValueError) as exc:
                states[phase_id] = "failed"
                failure_seen = True
                error = {
                    "code": "phase.artifact_failed",
                    "type": type(exc).__name__,
                    "message": _redact_text(
                        str(exc)[:4_000],
                        execution.secret_values,
                        (workspace.root, paths.root, snapshot.root),
                    ),
                }
                runtime.graph.patch_object(
                    phase_object_id,
                    {"status": "failed", "error": error, "attempt_id": execution.attempt_id},
                )
                _emit(
                    runtime,
                    "phase.failed",
                    {
                        "run_id": run_id,
                        "phase_id": phase_id,
                        "phase_object_id": phase_object_id,
                        "attempt_id": execution.attempt_id,
                        "source_digest": snapshot.source_digest,
                        "error": error,
                    },
                    actor="runtime",
                )
                results[phase_id] = {"status": "failed", "error": error, "artifacts": {}}
                continue
            states[phase_id] = "done"
            result = {
                "status": "done",
                "attempt_id": execution.attempt_id,
                "summary": execution.result.summary,
                "artifacts": {
                    name: attachment.to_dict(include_physical=True)
                    for name, attachment in attachments.items()
                },
            }
            results[phase_id] = result
            runtime.graph.patch_object(
                phase_object_id,
                {
                    "status": "done",
                    "attempt_id": execution.attempt_id,
                    "summary": execution.result.summary,
                    "artifact_names": list(attachments),
                },
            )
            _emit(
                runtime,
                "phase.succeeded",
                {
                    "run_id": run_id,
                    "phase_id": phase_id,
                    "phase_object_id": phase_object_id,
                    "attempt_id": execution.attempt_id,
                    "source_digest": snapshot.source_digest,
                    "artifacts": [item.artifact_id for item in attachments.values()],
                    "summary": execution.result.summary,
                },
                actor="runtime",
            )

    verification_ok = _source_intact(snapshot) and all(
        states[phase.phase_id] == "done" for phase in manifest.phases if phase.role == "verify"
    )
    status = "completed" if not failure_seen and verification_ok else "failed"
    runtime.graph.patch_object(
        run.id,
        {"status": status, "completed_at": datetime.now(UTC).isoformat()},
    )
    _emit(
        runtime,
        f"run.{status}",
        {
            "run_id": run_id,
            "automation": manifest.name,
            "source_digest": snapshot.source_digest,
            "verification_ok": verification_ok,
        },
        actor="runtime",
    )
    storage.finalize(run_id, status=status)
    return {
        "ok": status == "completed",
        "run_id": run_id,
        "environment": workspace.env,
        "automation": manifest.name,
        "source_digest": snapshot.source_digest,
        "status": status,
        "verification_ok": verification_ok,
        "phase_states": states,
        "phases": results,
        "runtime": runtime,
    }


def _runtime(workspace: Workspace, run_id: str) -> ROIHRuntime:
    graph = Graph(run_id=run_id)
    runtime = ROIHRuntime(graph, persist_to=str(workspace.db))
    runtime.run_until_idle()
    return runtime


def _select_wave(ready: list[PhaseSpec], max_parallel: int) -> list[PhaseSpec]:
    first = ready[0]
    if not first.parallel_safe or max_parallel == 1:
        return [first]
    return [phase for phase in ready if phase.parallel_safe][:max_parallel]


def _dependency_paths(results: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    return {
        phase_id: {
            name: str(item["physical_path"])
            for name, item in dict(result.get("artifacts") or {}).items()
        }
        for phase_id, result in results.items()
        if result.get("status") == "done"
    }


def _input_evidence(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    evidence: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest, size = hash_file(path)
        evidence.append(
            {
                "path": f"run://input/{path.relative_to(root).as_posix()}",
                "sha256": digest,
                "bytes": size,
            }
        )
    return evidence


def _source_intact(snapshot: SourceSnapshot) -> bool:
    try:
        observed, _ = source_tree_digest(snapshot.root)
    except (OSError, ValueError):
        return False
    return observed == snapshot.source_digest


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(stat.S_IREAD | (stat.S_IEXEC if path.is_dir() else 0))
    root.chmod(stat.S_IREAD | stat.S_IEXEC)


def _make_tree_writable(root: Path) -> None:
    root.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        path.chmod(stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if path.is_dir() else 0))


def _sanitize_execution(
    execution: PhaseExecution,
    *,
    physical_roots: tuple[Path, ...],
) -> PhaseExecution:
    secrets = tuple(value for value in execution.secret_values if value)
    stdout = _redact_text(execution.stdout, secrets, physical_roots)
    stderr = _redact_text(execution.stderr, secrets, physical_roots)
    error = _redact_value(execution.error, secrets, physical_roots)
    leaked_roots = [execution.output_dir, execution.output_dir.parent / "work"]
    if any(_tree_contains_secret(root, secrets) for root in leaked_roots):
        for root in leaked_roots:
            if root.is_dir():
                _make_tree_writable(root)
                shutil.rmtree(root)
                root.mkdir(parents=True, exist_ok=True)
        return replace(
            execution,
            ok=False,
            result=None,
            error={
                "code": "phase.secret_leak",
                "message": "phase output contained a declared secret and was removed",
            },
            stdout=stdout,
            stderr=stderr,
        )
    result = execution.result
    if result is not None:
        try:
            summary = _sanitize_durable_value(result.summary, secrets, physical_roots)
            _validate_artifact_metadata(result, secrets, physical_roots)
            result = result.model_copy(update={"summary": summary})
        except ValueError as exc:
            return replace(
                execution,
                ok=False,
                result=None,
                error={"code": "phase.unsafe_result", "message": str(exc)},
                stdout=stdout,
                stderr=stderr,
            )
    return replace(
        execution,
        result=result,
        error=error if isinstance(error, dict) else None,
        stdout=stdout,
        stderr=stderr,
    )


def _sanitize_durable_value(
    value: Any,
    secrets: tuple[str, ...],
    physical_roots: tuple[Path, ...],
) -> Any:
    if isinstance(value, str):
        redacted = _replace_secrets(value, secrets)
        if _contains_physical_path(redacted, physical_roots):
            msg = "phase result contains a physical path"
            raise ValueError(msg)
        return redacted
    if isinstance(value, list):
        return [_sanitize_durable_value(item, secrets, physical_roots) for item in value]
    if isinstance(value, dict):
        return {
            _sanitize_durable_value(str(key), secrets, physical_roots): _sanitize_durable_value(
                item, secrets, physical_roots
            )
            for key, item in value.items()
        }
    return value


def _validate_artifact_metadata(
    result: PhaseResult,
    secrets: tuple[str, ...],
    physical_roots: tuple[Path, ...],
) -> None:
    for name, relative in result.artifacts.items():
        if any(secret in value for secret in secrets for value in (name, relative)) or any(
            _contains_physical_path(value, physical_roots) for value in (name, relative)
        ):
            msg = "phase artifact metadata contains protected data"
            raise ValueError(msg)


def _redact_value(
    value: Any,
    secrets: tuple[str, ...],
    physical_roots: tuple[Path, ...],
) -> Any:
    if isinstance(value, str):
        return _redact_text(value, secrets, physical_roots)
    if isinstance(value, list):
        return [_redact_value(item, secrets, physical_roots) for item in value]
    if isinstance(value, dict):
        return {
            _redact_text(str(key), secrets, physical_roots): _redact_value(
                item, secrets, physical_roots
            )
            for key, item in value.items()
        }
    return value


def _replace_secrets(text: str, secrets: tuple[str, ...]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


def _contains_physical_path(text: str, physical_roots: tuple[Path, ...]) -> bool:
    if any(
        form and form in text for root in physical_roots for form in {str(root), root.as_posix()}
    ):
        return True
    return _WINDOWS_PATH.search(text) is not None or _POSIX_PATH.search(text) is not None


def _redact_text(
    text: str,
    secrets: tuple[str, ...],
    physical_roots: tuple[Path, ...],
) -> str:
    redacted = _replace_secrets(text, secrets)
    for root in physical_roots:
        for form in {str(root), root.as_posix()}:
            if form:
                redacted = redacted.replace(form, "[physical-path]")
    redacted = _WINDOWS_PATH.sub("[physical-path]", redacted)
    return _POSIX_PATH.sub("[physical-path]", redacted)


def _file_contains_secret(path: Path, secrets: tuple[str, ...]) -> bool:
    needles = [secret.encode("utf-8") for secret in secrets if secret]
    if not needles:
        return False
    overlap = max(len(needle) for needle in needles) - 1
    tail = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            block = tail + chunk
            if any(needle in block for needle in needles):
                return True
            tail = block[-overlap:] if overlap else b""
    return False


def _tree_contains_secret(root: Path, secrets: tuple[str, ...]) -> bool:
    if not root.is_dir() or not secrets:
        return False
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if any(secret in relative for secret in secrets):
            return True
        if path.is_file() and _file_contains_secret(path, secrets):
            return True
    return False


def _execute_phase(job: PhaseJob) -> PhaseExecution:
    workspace = job.workspace
    snapshot = job.snapshot
    phase = job.phase
    attempt_id = job.attempt_id
    attempt_root = job.work_root / "phases" / phase.phase_id / attempt_id
    attempt_source = attempt_root / "source"
    work_dir = attempt_root / "work"
    output_dir = attempt_root / "output"
    secret_environment: dict[str, str] = {}
    secret_values: list[str] = []
    environment = isolated_process_environment()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        _copy_phase_source(snapshot, attempt_source)
    except (OSError, RuntimeError, ValueError) as exc:
        return PhaseExecution(
            phase=phase,
            attempt_id=attempt_id,
            ok=False,
            result=None,
            error={
                "code": "source.integrity_failed",
                "type": type(exc).__name__,
                "message": str(exc)[:4_000],
            },
            stdout="",
            stderr="",
            output_dir=output_dir,
            secret_values=(),
        )
    for index, name in enumerate(snapshot.manifest.required_secrets):
        try:
            value = get_secret(workspace, name)
        except (OSError, RuntimeError, ValueError) as exc:
            return PhaseExecution(
                phase=phase,
                attempt_id=attempt_id,
                ok=False,
                result=None,
                error={
                    "code": "secret.provider_failed",
                    "type": type(exc).__name__,
                    "message": str(exc)[:4_000],
                },
                stdout="",
                stderr="",
                output_dir=output_dir,
                secret_values=tuple(secret_values),
            )
        if value is None:
            return PhaseExecution(
                phase=phase,
                attempt_id=attempt_id,
                ok=False,
                result=None,
                error={"code": "secret.missing", "message": f"required secret is missing: {name}"},
                stdout="",
                stderr="",
                output_dir=output_dir,
                secret_values=tuple(secret_values),
            )
        environment_name = f"ROI_H_PHASE_SECRET_{index}"
        environment[environment_name] = value
        secret_environment[name] = environment_name
        secret_values.append(value)
    request = {
        "run_id": job.run_id,
        "phase_id": phase.phase_id,
        "attempt_id": attempt_id,
        "environment": workspace.env,
        "source_root": str(attempt_source),
        "input_dir": str(job.input_dir),
        "reference_dir": str(job.reference_dir),
        "work_dir": str(work_dir),
        "output_dir": str(output_dir),
        "dependencies": {name: job.dependencies.get(name, {}) for name in phase.needs},
        "secret_environment": secret_environment,
        "module": phase.module,
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "roi_h.harness.automation_worker"],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
            cwd=attempt_source,
            env=environment,
            timeout=phase.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return PhaseExecution(
            phase=phase,
            attempt_id=attempt_id,
            ok=False,
            result=None,
            error={
                "code": "phase.timeout",
                "message": f"phase exceeded {phase.timeout_seconds:g} seconds",
            },
            stdout=(exc.stdout or "")[:64_000] if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "")[:64_000] if isinstance(exc.stderr, str) else "",
            output_dir=output_dir,
            secret_values=tuple(secret_values),
        )
    except OSError as exc:
        return PhaseExecution(
            phase=phase,
            attempt_id=attempt_id,
            ok=False,
            result=None,
            error={
                "code": "phase.launch_failed",
                "type": type(exc).__name__,
                "message": str(exc)[:4_000],
            },
            stdout="",
            stderr="",
            output_dir=output_dir,
            secret_values=tuple(secret_values),
        )
    try:
        observed_digest, _ = source_tree_digest(attempt_source)
    except (OSError, ValueError):
        observed_digest = ""
    if observed_digest != snapshot.source_digest:
        return PhaseExecution(
            phase=phase,
            attempt_id=attempt_id,
            ok=False,
            result=None,
            error={
                "code": "source.integrity_failed",
                "message": "phase changed its isolated source copy",
            },
            stdout="",
            stderr="",
            output_dir=output_dir,
            secret_values=tuple(secret_values),
        )
    raw = completed.stdout[: _WORKER_RESPONSE_LIMIT + 1]
    if len(raw) > _WORKER_RESPONSE_LIMIT:
        response: dict[str, Any] = {
            "ok": False,
            "error": {"code": "phase.worker_protocol_failed", "message": "response is too large"},
        }
    else:
        try:
            parsed = json.loads(raw)
            response = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            response = {
                "ok": False,
                "error": {
                    "code": "phase.worker_protocol_failed",
                    "message": "worker returned invalid JSON",
                },
            }
    ok = completed.returncode == 0 and response.get("ok") is True
    result: PhaseResult | None = None
    if ok:
        try:
            result = PhaseResult.model_validate(response.get("result") or {})
        except ValueError as exc:
            ok = False
            response = {
                "error": {
                    "code": "phase.worker_protocol_failed",
                    "message": f"invalid phase result: {exc}",
                }
            }
    return PhaseExecution(
        phase=phase,
        attempt_id=attempt_id,
        ok=ok,
        result=result,
        error=None if ok else dict(response.get("error") or {}),
        stdout=str(response.get("stdout") or ""),
        stderr=str(response.get("stderr") or completed.stderr or "")[:64_000],
        output_dir=output_dir,
        secret_values=tuple(secret_values),
    )


def _copy_phase_source(snapshot: SourceSnapshot, destination: Path) -> None:
    if not _source_intact(snapshot):
        msg = "frozen automation source changed before phase execution"
        raise RuntimeError(msg)
    shutil.copytree(snapshot.root, destination)
    copied_digest, _ = source_tree_digest(destination)
    if copied_digest != snapshot.source_digest:
        msg = "phase source copy does not match the frozen source"
        raise RuntimeError(msg)
    _make_tree_read_only(destination)


def _attach_phase_artifacts(
    runtime: ROIHRuntime,
    storage: RunStorage,
    run_id: str,
    phase_object_id: str,
    execution: PhaseExecution,
) -> dict[str, ArtifactAttachment]:
    attachments: dict[str, ArtifactAttachment] = {}
    if execution.result is None:
        msg = "successful phase execution has no result"
        raise ValueError(msg)
    phase_id = execution.phase.phase_id
    sources: dict[str, Path] = {}
    for name, relative in execution.result.artifacts.items():
        source = (execution.output_dir / relative).resolve()
        if not source.is_relative_to(execution.output_dir.resolve()) or not source.is_file():
            msg = f"phase artifact does not exist inside its output directory: {relative!r}"
            raise FileNotFoundError(msg)
        if source.stat().st_size > _MAX_ARTIFACT_BYTES:
            msg = f"phase artifact is larger than {_MAX_ARTIFACT_BYTES} bytes: {name!r}"
            raise ValueError(msg)
        if _file_contains_secret(source, execution.secret_values):
            _make_tree_writable(execution.output_dir)
            shutil.rmtree(execution.output_dir)
            execution.output_dir.mkdir(parents=True, exist_ok=True)
            msg = f"phase artifact contains a declared secret: {name!r}"
            raise ValueError(msg)
        sources[name] = source
    for name, source in sources.items():
        attachment = storage.attach(run_id, source, name=name)
        attachments[name] = attachment
        record = ArtifactRecord(
            artifact_id=attachment.artifact_id,
            run_id=run_id,
            name=attachment.name,
            uri=attachment.uri,
            bytes=attachment.bytes,
            sha256=attachment.sha256,
            media_type=attachment.media_type,
            source=attachment.source,
            created_at=attachment.created_at,
            phase=phase_id,
            phase_id=phase_object_id,
        )
        runtime.graph.add_object("rpa.artifact", record.to_graph())
        _emit(
            runtime,
            "artifact.attached",
            {
                "run_id": run_id,
                "phase_id": phase_id,
                "artifact_id": attachment.artifact_id,
                "name": name,
                "sha256": attachment.sha256,
                "bytes": attachment.bytes,
            },
            actor="runtime",
        )
    return attachments


def _record_unstarted_phase(
    runtime: ROIHRuntime,
    phase: PhaseSpec,
    index: int,
    status: str,
    reason: str,
) -> None:
    obj = runtime.graph.add_object(
        "rpa.phase",
        {
            "run_id": runtime.run_id,
            "name": phase.phase_id,
            "index": index,
            "status": status,
            "role": phase.role,
            "needs": list(phase.needs),
            "artifact_names": [],
            "summary": {},
            "error": reason,
        },
    )
    _emit(
        runtime,
        f"phase.{status}",
        {
            "run_id": runtime.run_id,
            "phase_id": phase.phase_id,
            "phase_object_id": obj.id,
            "reason": reason,
        },
        actor="runtime",
    )


def _write_diagnostics(root: Path, execution: PhaseExecution) -> None:
    root.mkdir(parents=True, exist_ok=True)
    prefix = f"{execution.phase.phase_id}-{execution.attempt_id}"
    atomic_write_text(root / f"{prefix}.stdout.log", execution.stdout, mode=0o600)
    atomic_write_text(root / f"{prefix}.stderr.log", execution.stderr, mode=0o600)


def _diagnostic_references(phase_id: str, attempt_id: str) -> dict[str, str]:
    prefix = f"{phase_id}-{attempt_id}"
    return {
        "stdout": f"run://diagnostics/{prefix}.stdout.log",
        "stderr": f"run://diagnostics/{prefix}.stderr.log",
    }


def _emit(
    runtime: ROIHRuntime,
    event_type: str,
    payload: dict[str, Any],
    *,
    actor: str,
) -> None:
    runtime.graph.emit(
        Event(
            id=runtime.graph.ids.event(),
            type=event_type,
            payload=payload,
            actor=actor,
            frame_id=runtime.frame.id if runtime.frame else None,
            caused_by=None,
            timestamp=runtime.graph.clock.now(),
        )
    )


__all__ = ["run_source"]
