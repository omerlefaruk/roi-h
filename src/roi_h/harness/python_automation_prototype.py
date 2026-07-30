"""PROTOTYPE: run one verified Python automation through the existing harness.

This module is throwaway evidence for Wayfinder issue #6. It is not a public API.
Package Python is trusted code. This prototype is not a Python sandbox.
"""

# ruff: noqa: EM101, EM102, TRY003, UP046

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from types import ModuleType
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from roi_h.harness.application import RunSession
from roi_h.harness.atomicfs import hash_file, verify_package
from roi_h.harness.domain import InvocationIdentity, PhasePlanEntry
from roi_h.harness.graph_access import patch_run, run_object
from roi_h.harness.logical_paths import LogicalPath
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.secrets import redact_secret_values

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)
_CALL_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class Operation(Generic[InputT, OutputT]):
    """A typed operation used by automation code."""

    name: str
    input_model: type[InputT]
    output_model: type[OutputT]


class ArtifactPutInput(BaseModel):
    """Attach one run output as a durable artifact."""

    source: str
    name: str


class ArtifactPutOutput(BaseModel):
    """Typed artifact identity returned to automation code."""

    artifact_id: str
    name: str
    uri: str
    sha256: str
    bytes: int


ARTIFACT_PUT = Operation("artifact.put", ArtifactPutInput, ArtifactPutOutput)


class ApprovalRequired(BaseException):
    """Pause the entry point until the operator resolves an approval."""

    def __init__(self, approval_id: str, operation: str) -> None:
        """Keep only the durable approval identity and requested operation."""
        super().__init__(approval_id)
        self.approval_id = approval_id
        self.operation = operation


class PrototypeCrash(BaseException):
    """Simulate process loss after a durable operation result."""


@dataclass(frozen=True)
class VerifiedPythonAutomation:
    """One verified package entry point and its digest-bound execution contract."""

    package_digest: str
    phases: tuple[dict[str, Any], ...]
    operations: dict[str, dict[str, Any]]
    entrypoint: Callable[[AutomationContext], None]

    def run(
        self,
        session: RunSession,
        *,
        crash_after: str | None = None,
    ) -> dict[str, Any]:
        context = AutomationContext(self, session, crash_after=crash_after)
        try:
            self.entrypoint(context)
            context.finish()
        except (ApprovalRequired, PrototypeCrash):
            raise
        except Exception:
            run = run_object(session.runtime)
            _finish_run(
                session,
                "cancelled" if run and run.data.get("status") == "cancel_requested" else "failed",
            )
            raise
        _finish_run(session, "completed")
        return {
            "ok": True,
            "run_id": session.runtime.run_id,
            "package_digest": self.package_digest,
            "phases": session.list_phases(),
        }


class AutomationContext:
    """Two-method automation interface: ``phase`` and typed ``call``."""

    def __init__(
        self,
        automation: VerifiedPythonAutomation,
        session: RunSession,
        *,
        crash_after: str | None,
    ) -> None:
        """Bind one verified automation to one durable run."""
        self._automation = automation
        self._session = session
        self._crash_after = crash_after
        self._phase_index = 0
        self._phase_name: str | None = None

    def phase(
        self,
        name: str,
        body: Callable[[], Mapping[str, Any] | None],
    ) -> None:
        """Run, resume, or skip one manifest-declared phase."""
        self._assert_not_cancelled()
        if self._phase_index >= len(self._automation.phases):
            raise RuntimeError(
                f"automation declared no phase at position {self._phase_index}: {name}"
            )
        declared = self._automation.phases[self._phase_index]
        if name != declared["name"]:
            raise RuntimeError(
                f"automation phase order changed: expected {declared['name']!r}, observed {name!r}"
            )

        prior = [item for item in self._session.list_phases() if item["name"] == name]
        if prior and prior[-1]["status"] == "done":
            self._phase_index += 1
            return

        status = prior[-1]["status"] if prior else None
        if status == "open":
            current = self._session.status().get("current_phase")
            if current != name:
                raise RuntimeError(f"open phase mismatch: expected {name!r}, observed {current!r}")
        elif status in {"failed", "skipped"}:
            self._session.retry_phase(
                name,
                description=str(declared.get("description") or ""),
                require_artifacts=list(declared.get("require_artifacts") or []),
            )
        else:
            self._session.begin_phase(
                name,
                description=str(declared.get("description") or ""),
                require_artifacts=list(declared.get("require_artifacts") or []),
            )

        self._phase_name = name
        try:
            summary = body()
        except (ApprovalRequired, PrototypeCrash):
            raise
        except Exception as exc:
            error = redact_secret_values(f"{type(exc).__name__}: {exc}", self._session.workspace)
            self._session.fail_phase(error=str(error))
            raise
        else:
            redacted = redact_secret_values(dict(summary or {}), self._session.workspace)
            self._session.end_phase(summary=dict(redacted) if isinstance(redacted, dict) else {})
            self._phase_index += 1
        finally:
            self._phase_name = None

    def call(
        self,
        operation: Operation[InputT, OutputT],
        request: InputT,
        *,
        call_id: str,
    ) -> OutputT:
        """Call one allowlisted operation with a restart-stable identity."""
        self._assert_not_cancelled()
        if self._phase_name is None:
            raise RuntimeError("operation calls must run inside a phase")
        if not _CALL_ID.fullmatch(call_id):
            raise ValueError(f"invalid automation call id: {call_id!r}")
        declared = self._automation.operations.get(operation.name)
        if declared is None:
            raise PermissionError(f"operation is not in the package allowlist: {operation.name}")
        _assert_schema(operation.input_model, str(declared["input_schema_digest"]))
        _assert_schema(operation.output_model, str(declared["output_schema_digest"]))
        arguments = operation.input_model.model_validate(request).model_dump(mode="json")

        if operation.name == ARTIFACT_PUT.name:
            result = self._put_artifact(ArtifactPutInput.model_validate(arguments))
        else:
            result = self._invoke(operation, arguments, call_id=call_id)

        if self._crash_after == call_id:
            raise PrototypeCrash(call_id)
        return operation.output_model.model_validate(result)

    def finish(self) -> None:
        """Reject an entry point that omitted a declared phase."""
        if self._phase_index != len(self._automation.phases):
            missing = self._automation.phases[self._phase_index :]
            names = [item["name"] for item in missing]
            raise RuntimeError(f"automation omitted declared phases: {names}")

    def _assert_not_cancelled(self) -> None:
        run = run_object(self._session.runtime)
        if run is not None and run.data.get("status") == "cancel_requested":
            raise RuntimeError("run cancellation requested")

    def _invoke(
        self,
        operation: Operation[InputT, OutputT],
        arguments: dict[str, Any],
        *,
        call_id: str,
    ) -> dict[str, Any]:
        phase_name = self._phase_name
        if phase_name is None:
            raise RuntimeError("operation call has no active phase")
        identity = _identity(
            self._session.runtime.run_id,
            self._automation.package_digest,
            phase_name,
            call_id,
        )
        pending = next(
            (
                item
                for item in self._session.runtime.pending_approvals()
                if item.data.get("invocation_id") == identity.invocation_id
            ),
            None,
        )
        if pending is not None:
            raise ApprovalRequired(pending.id, operation.name)
        prior = [
            item
            for item in self._session.runtime.graph.objects(type="rpa.invocation")
            if item.data.get("invocation_id") == identity.invocation_id
        ]
        if prior:
            data = prior[-1].data
            if data.get("name") != operation.name or data.get("args") != arguments:
                raise RuntimeError(f"automation call changed during replay: {call_id}")
            if data.get("status") in {"running", "scheduled", "outcome_unknown"}:
                raise RuntimeError(
                    f"automation call needs reconciliation before replay: {call_id} "
                    f"({data.get('status')})"
                )
            step_id = data.get("step_id")
            step = self._session.runtime.graph.get_object(str(step_id)) if step_id else None
            if step is None:
                raise RuntimeError(f"automation call has no durable result: {call_id}")
            if step.data.get("status") != "ok":
                raise RuntimeError(str(step.data.get("error") or "operation failed"))
            return dict(step.data.get("output") or {})

        skill, separator, tool = operation.name.partition(".")
        if not separator:
            raise ValueError(f"invalid operation name: {operation.name!r}")
        step = self._session.invoke(
            skill,
            tool,
            arguments,
            actor="automation",
            force=False,
            identity=identity,
        )
        if step.status == "pending_approval" and step.approval_id:
            raise ApprovalRequired(step.approval_id, operation.name)
        if step.status != "ok":
            raise RuntimeError(step.error or f"operation did not complete: {operation.name}")
        return dict(step.output)

    def _put_artifact(self, request: ArtifactPutInput) -> dict[str, Any]:
        source = LogicalPath.parse(request.source)
        if source.scheme != "run" or source.root != "output":
            raise PermissionError("artifact.put accepts only run://output content")
        existing = next(
            (
                item
                for item in self._session.runtime.graph.objects(type="rpa.artifact")
                if item.data.get("name") == request.name
            ),
            None,
        )
        if existing is not None:
            if (
                existing.data.get("source") != request.source
                or existing.data.get("phase") != self._phase_name
            ):
                raise RuntimeError(f"artifact call changed during replay: {request.name}")
            return {
                "artifact_id": existing.data["artifact_id"],
                "name": existing.data["name"],
                "uri": existing.data["uri"],
                "sha256": existing.data["sha256"],
                "bytes": existing.data["bytes"],
            }
        return ArtifactPutOutput.model_validate(
            self._session.put_artifact(request.source, name=request.name)
        ).model_dump(mode="json")


def verify_python_package(package: Path, expected_digest: str) -> dict[str, Any]:
    """Verify caller-supplied package identity and package bytes before import."""
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise TypeError("automation manifest must be an object")
    observed = str(manifest.get("package_digest") or "")
    if observed != expected_digest:
        raise ValueError(
            f"channel package digest mismatch: expected {expected_digest}, observed {observed}"
        )
    verify_package(package, manifest)
    return manifest


def load_python_automation(
    package: Path,
    expected_digest: str,
    session: RunSession,
) -> VerifiedPythonAutomation:
    """Verify, bind, and only then import one ``automation.py:run`` entry point."""
    manifest = verify_python_package(package, expected_digest)
    if manifest.get("schema_version") != 2:
        raise ValueError("unsupported Python automation manifest schema")
    run = next(iter(session.runtime.graph.objects(type="rpa.run")), None)
    if run is None:
        raise RuntimeError("Python automation requires a started run")
    binding = {
        "name": run.data.get("automation_name"),
        "version": run.data.get("automation_version"),
        "package_digest": run.data.get("package_digest"),
    }
    expected_binding = {
        "name": manifest.get("name"),
        "version": manifest.get("version"),
        "package_digest": expected_digest,
    }
    if binding != expected_binding:
        raise ValueError(f"run package identity mismatch: {binding} != {expected_binding}")
    if manifest.get("runtime") != runtime_manifest():
        raise ValueError("automation runtime version changed since publication")
    if manifest.get("runtime_api") != "roi-h.python-automation/1":
        raise ValueError("unsupported Python automation runtime API")
    if manifest.get("entrypoint") != "automation.py:run":
        raise ValueError("prototype requires entrypoint automation.py:run")

    operations = {
        str(item["name"]): dict(item)
        for item in manifest.get("operations") or []
        if isinstance(item, dict) and item.get("name")
    }
    if len(operations) != len(manifest.get("operations") or []):
        raise ValueError("automation operation names must be unique")
    for name, declared in operations.items():
        observed = operation_manifest_entry(session, name)
        if observed != declared:
            raise ValueError(f"operation contract changed since publication: {name}")

    phase_entries = tuple(
        PhasePlanEntry.model_validate(item) for item in manifest.get("phases") or []
    )
    phases = tuple(item.model_dump(mode="json") for item in phase_entries)
    if not phases or len({item["name"] for item in phases}) != len(phases):
        raise ValueError("automation phases must be non-empty and unique")
    run_phases = tuple(
        PhasePlanEntry.model_validate(item).model_dump(mode="json")
        for item in run.data.get("phase_plan") or []
    )
    if run_phases != phases:
        raise ValueError("run phase plan does not match the automation package")

    module = _import_module(package / "automation.py", expected_digest)
    entrypoint = getattr(module, "run", None)
    if not callable(entrypoint):
        raise TypeError("automation.py must define run(context)")
    return VerifiedPythonAutomation(
        package_digest=expected_digest,
        phases=phases,
        operations=operations,
        entrypoint=entrypoint,
    )


def operation_manifest_entry(session: RunSession, name: str) -> dict[str, Any]:
    """Return the digest-bound schema and policy contract for one operation."""
    if name == ARTIFACT_PUT.name:
        return {
            "name": name,
            "input_schema_digest": schema_digest(ARTIFACT_PUT.input_model),
            "output_schema_digest": schema_digest(ARTIFACT_PUT.output_model),
            "policy_digest": _json_digest(
                {"effect": "write", "allow_in_prod": True, "filesystem_roots": ["run:output"]}
            ),
            "implementation_digest": hash_file(Path(__file__))[0],
        }
    tool = session.catalog.get(name)
    policy = {
        "effect": tool.effect,
        "idempotency": tool.idempotency,
        "requires_approval": tool.requires_approval,
        "allow_in_prod": tool.allow_in_prod,
        "deterministic": tool.deterministic,
        "secret_names": list(tool.secret_names),
        "network_hosts": list(tool.network_hosts),
        "filesystem_roots": list(tool.filesystem_roots),
        "timeout_seconds": tool.timeout_seconds,
    }
    return {
        "name": name,
        "input_schema_digest": schema_digest(tool.input_model),
        "output_schema_digest": schema_digest(tool.output_model),
        "policy_digest": _json_digest(policy),
        "implementation_digest": hash_file(tool.script_path)[0],
    }


def runtime_manifest() -> dict[str, str]:
    """Return exact runtime versions bound by the prototype package."""
    return {
        "python": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "roi_h": version("roi-h"),
        "activegraph": version("activegraph"),
    }


def schema_digest(model: type[BaseModel]) -> str:
    """Hash schema behavior without cosmetic Pydantic titles."""
    def strip_titles(value: object) -> object:
        if isinstance(value, dict):
            return {key: strip_titles(item) for key, item in value.items() if key != "title"}
        if isinstance(value, list):
            return [strip_titles(item) for item in value]
        return value

    return _json_digest(strip_titles(model.model_json_schema()))


def _assert_schema(model: type[BaseModel], expected: str) -> None:
    observed = schema_digest(model)
    if observed != expected:
        raise TypeError(
            f"operation model schema mismatch: expected {expected}, observed {observed}"
        )


def _identity(run_id: str, package_digest: str, phase: str, call_id: str) -> InvocationIdentity:
    token = hashlib.sha256(f"{package_digest}\0{phase}\0{call_id}".encode()).hexdigest()
    invocation_id = f"inv_{token[:24]}"
    return InvocationIdentity(
        invocation_id=invocation_id,
        idempotency_key=f"roi-h:{run_id}:{invocation_id}",
    )


def _json_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _finish_run(session: RunSession, status: str) -> None:
    patch_run(
        session.runtime,
        {"status": status, "completed_at": datetime.now(UTC).isoformat()},
    )
    RunStorage(session.workspace).finalize(session.runtime.run_id, status=status)


def _import_module(path: Path, digest: str) -> ModuleType:
    module_name = f"roi_h_python_automation_prototype_{digest.removeprefix('sha256:')[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import automation entry point: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    prior = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = prior
    return module
