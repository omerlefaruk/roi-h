"""ActiveGraph-native tool invocation with an isolated process adapter.

ActiveGraph owns scheduling, budgets, authority decisions, approval events,
tool events, persistence, and replay. ROI-H contributes only the concrete
tool invoker that executes a skill script in a scoped subprocess and a
projection from canonical tool responses to ``rpa.step`` objects.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from activegraph import Behavior, Event, Runtime
from activegraph.tools import Tool, ToolContext, ToolError
from activegraph.tools.cache import CachedToolResponse, canonicalize_args, hash_tool_call
from pydantic import BaseModel

from roi_h.harness.diagnostics import DiagnosticRecord, DiagnosticSink
from roi_h.harness.domain import (
    ExecutionFailure,
    FailureKind,
    InvocationIdentity,
    StepResult,
    StepStatus,
)
from roi_h.harness.graph_access import phase_tags
from roi_h.harness.jsonutil import json_object
from roi_h.harness.loader import SkillCatalog, SkillTool
from roi_h.harness.logical_paths import (
    PathScope,
    materialize_tool_payload,
    normalize_tool_output,
)
from roi_h.harness.records import InvocationRecord, StepRecord
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.runtime_environment import isolated_process_environment
from roi_h.harness.secrets import (
    get_secret,
    redact_secret_values,
    resolve_secret_refs,
    secret_names_in_refs,
)
from roi_h.harness.worker import WORKER_PROTOCOL_LIMIT
from roi_h.harness.workspace import Workspace
from roi_h.installation import managed_browser_root

_APPROVAL_BEHAVIOR = "roi_h.request_invocation_approval"
_EXECUTE_BEHAVIOR = "roi_h.execute_invocation"


class ToolPolicyError(RuntimeError):
    """Raised when an invocation violates the published execution policy."""


class SkillWorkerError(RuntimeError):
    """Raised with the isolated worker stage that failed."""

    def __init__(self, message: str, *, stage: str) -> None:
        """Store the failed worker stage."""
        super().__init__(message)
        self.stage = stage


class IsolatedSkillInvoker:
    """ActiveGraph tool-invoker adapter backed by ROI-H skill subprocesses."""

    def __init__(self, catalog: SkillCatalog, workspace: Workspace) -> None:
        """Bind the concrete skill catalog and execution workspace."""
        self._catalog = catalog
        self._workspace = workspace

    def invoke(
        self,
        tool: Tool,
        args: Any,
        ctx: ToolContext,
    ) -> CachedToolResponse:
        skill, separator, tool_id = tool.name.partition(".")
        if not separator:
            code = "tool.execution_error"
            raise ToolError(
                code,
                f"ROI-H skill tool must use skill.tool naming: {tool.name!r}",
            )
        skill_tool = self._catalog.resolve(skill, tool_id)
        _enforce_policy(skill_tool, self._workspace)
        payload = args.model_dump(mode="json") if isinstance(args, BaseModel) else json_object(args)
        resolved = resolve_secret_refs(payload, self._workspace)
        worker_payload = resolved if isinstance(resolved, dict) else payload
        run_id = _run_id_from_key(ctx.idempotency_key)
        path_scope = PathScope(self._workspace, run_id=run_id)
        worker_payload = materialize_tool_payload(
            worker_payload,
            scope=path_scope,
            capabilities=skill_tool.filesystem_roots,
            effect=skill_tool.effect,
        )
        started = time.monotonic()
        try:
            output = _run_worker(
                skill_tool,
                worker_payload,
                workspace=self._workspace,
                run_id=run_id,
                idempotency_key=ctx.idempotency_key,
            )
        except subprocess.TimeoutExpired as exc:
            message = f"{skill_tool.name} exceeded {skill_tool.timeout_seconds:g}s"
            code = "tool.timeout"
            raise ToolError(
                code,
                message,
                payload_extras={"tool": skill_tool.name},
            ) from exc
        except SkillWorkerError as exc:
            reason = "tool.invalid_output" if exc.stage == "output" else "tool.invalid_input"
            if exc.stage not in {"contract", "input", "output"}:
                reason = "tool.execution_error"
            raise ToolError(
                reason,
                str(exc),
                payload_extras={"tool": skill_tool.name, "stage": exc.stage},
            ) from exc
        except ToolError:
            raise
        except Exception as exc:
            code = "tool.execution_error"
            raise ToolError(
                code,
                f"{type(exc).__name__}: {exc}",
                payload_extras={
                    "tool": skill_tool.name,
                    "exception_type": type(exc).__name__,
                },
            ) from exc
        normalized_output = normalize_tool_output(output, scope=path_scope)
        return CachedToolResponse(
            output=json_object(redact_secret_values(normalized_output, self._workspace)),
            error=None,
            latency_seconds=max(0.0, time.monotonic() - started),
            cost_usd=Decimal(0),
        )


def build_invocation_behaviors(
    catalog: SkillCatalog,
    workspace: Workspace,
    invoker: IsolatedSkillInvoker,
) -> tuple[Behavior, Behavior]:
    """Build the two ActiveGraph behaviors for approval and execution."""

    def request_approval(event: Event, _graph: Any, ctx: Any) -> None:
        record = event.payload.get("invocation")
        if not isinstance(record, dict):
            return
        ctx.propose_object(
            "rpa.invocation",
            record,
            reason=str(event.payload.get("reason") or "tool invocation requires approval"),
        )

    def execute(event: Event, graph: Any, ctx: Any) -> None:
        raw = event.payload.get("object")
        if not isinstance(raw, dict) or raw.get("type") != "rpa.invocation":
            return
        data = raw.get("data")
        if not isinstance(data, dict) or data.get("status") != "scheduled":
            return

        invocation_object_id = str(raw["id"])
        runtime: Runtime = ctx._runtime  # noqa: SLF001
        tool = runtime.get_tool(str(data["name"]))
        skill_tool = catalog.resolve(str(data["skill"]), str(data["tool"]))
        runtime.budget.consume("max_tool_calls")
        started_at = _now()
        started = time.monotonic()
        graph.patch_object(
            invocation_object_id,
            {"status": "running", "started_at": started_at},
        )

        args = dict(data.get("args") or {})
        args_hash = hash_tool_call(tool_name=tool.name, args=args)
        requested = graph.emit(
            "tool.requested",
            {
                "behavior": _EXECUTE_BEHAVIOR,
                "tool": tool.name,
                "args_hash": args_hash,
                "args": canonicalize_args(args),
                "call_id": data["invocation_id"],
                "cache_hit": False,
                "deterministic": tool.deterministic,
                "idempotency_key": data["idempotency_key"],
            },
        )

        output: dict[str, Any] = {}
        failure: ExecutionFailure | None = None
        latency = 0.0
        try:
            validated_args: Any = (
                tool.input_schema.model_validate(args) if tool.input_schema is not None else args
            )
            tool_context = ToolContext(
                behavior_name=_EXECUTE_BEHAVIOR,
                event_id=event.id,
                frame=ctx.frame,
                idempotency_key=str(data["idempotency_key"]),
                timeout_seconds=tool.timeout_seconds,
                external_io_mode="runtime_recorded",
            )
            response = invoker.invoke(tool, validated_args, tool_context)
            latency = response.latency_seconds
            validated_output: Any = response.output
            if tool.output_schema is not None:
                validated_output = tool.output_schema.model_validate(response.output).model_dump(
                    mode="json"
                )
            output = json_object(validated_output)
        except Exception as exc:  # noqa: BLE001 — record every adapter failure.
            failure = _failure_from_exception(exc)
            failure = failure.model_copy(
                update={"message": str(redact_secret_values(failure.message, workspace))}
            )
            if failure.kind != "validation" or failure.exception_type == "LogicalPathError":
                diagnostic_id = _emit_invocation_failure_diagnostic(
                    workspace,
                    skill_tool=skill_tool,
                    run_id=str(data["run_id"]),
                    invocation_id=str(data["invocation_id"]),
                    failure=failure,
                )
                failure = failure.model_copy(
                    update={
                        "details": {
                            **failure.details,
                            "diagnostic_id": diagnostic_id,
                        }
                    }
                )
            latency = max(0.0, time.monotonic() - started)

        graph.emit(
            "tool.responded",
            {
                "behavior": _EXECUTE_BEHAVIOR,
                "tool": tool.name,
                "args_hash": args_hash,
                "output": output if failure is None else None,
                "error": failure.model_dump(mode="json") if failure else None,
                "cache_hit": False,
                "latency_seconds": latency,
                "cost_usd": "0",
                "deterministic": tool.deterministic,
                "idempotency_key": data["idempotency_key"],
                "tool_request_event_id": requested.id,
            },
        )

        completed_at = _now()
        identity = InvocationIdentity(
            invocation_id=str(data["invocation_id"]),
            idempotency_key=str(data["idempotency_key"]),
            attempt=int(data.get("attempt") or 1),
        )
        step = _record_step(
            graph,
            skill_tool=skill_tool,
            run_id=str(data["run_id"]),
            args=args,
            output=output,
            status="ok" if failure is None else "error",
            error=failure.message if failure else None,
            failure=failure,
            identity=identity,
            approval_id=str(data["approval_id"]) if data.get("approval_id") else None,
            phase=str(data["phase"]) if data.get("phase") else None,
            phase_id=str(data["phase_id"]) if data.get("phase_id") else None,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=latency,
        )
        graph.patch_object(
            invocation_object_id,
            {
                "status": "succeeded" if failure is None else "failed",
                "step_id": step.id,
                "error": failure.message if failure else None,
                "completed_at": completed_at,
                "duration_seconds": latency,
            },
        )

    return (
        Behavior(
            name=_APPROVAL_BEHAVIOR,
            fn=request_approval,
            on=["rpa.approval.requested"],
        ),
        Behavior(
            name=_EXECUTE_BEHAVIOR,
            fn=execute,
            on=["object.created"],
            creates=["rpa.step"],
        ),
    )


def submit_invocation(
    runtime: Runtime,
    skill_tool: SkillTool,
    payload: dict[str, Any],
    *,
    workspace: Workspace,
    actor: str,
    identity: InvocationIdentity,
    approval_id: str | None = None,
) -> StepResult:
    """Persist one invocation; ActiveGraph schedules its execution behavior."""
    _enforce_policy(skill_tool, workspace)
    prior = _find_completed_attempt(runtime, identity)
    if prior is not None:
        return _step_result(prior)

    args_json = json_object(redact_secret_values(payload, workspace))
    phase_name, phase_id = phase_tags(runtime)
    record = InvocationRecord(
        run_id=runtime.run_id,
        invocation_id=identity.invocation_id,
        idempotency_key=identity.idempotency_key,
        attempt=identity.attempt,
        skill=skill_tool.skill,
        tool=skill_tool.tool_id,
        name=skill_tool.name,
        scope=skill_tool.scope,
        args=args_json,
        actor=actor,
        effect=skill_tool.effect,
        idempotency=skill_tool.idempotency,
        approval_id=approval_id,
        phase=phase_name,
        phase_id=phase_id,
    )
    invocation = runtime.graph.add_object(
        "rpa.invocation",
        record.to_graph(),
        actor=actor,
    )
    runtime.run_until_idle()
    updated = runtime.graph.get_object(invocation.id)
    if updated is not None and updated.data.get("step_id"):
        step = runtime.graph.get_object(str(updated.data["step_id"]))
        if step is not None:
            return _step_result(step)
    return _unknown_outcome(runtime, invocation.id, skill_tool, args_json, identity)


def request_invocation_approval(
    runtime: Runtime,
    skill_tool: SkillTool,
    payload: dict[str, Any],
    *,
    workspace: Workspace,
    actor: str,
    identity: InvocationIdentity,
    reason: str,
) -> StepResult:
    """Route an invocation through ActiveGraph's durable approval queue."""
    _enforce_policy(skill_tool, workspace)
    args_json = json_object(redact_secret_values(payload, workspace))
    phase_name, phase_id = phase_tags(runtime)
    record = InvocationRecord(
        run_id=runtime.run_id,
        invocation_id=identity.invocation_id,
        idempotency_key=identity.idempotency_key,
        attempt=identity.attempt,
        skill=skill_tool.skill,
        tool=skill_tool.tool_id,
        name=skill_tool.name,
        scope=skill_tool.scope,
        args=args_json,
        actor=actor,
        effect=skill_tool.effect,
        idempotency=skill_tool.idempotency,
        phase=phase_name,
        phase_id=phase_id,
    )
    _emit_runtime_event(
        runtime,
        "rpa.approval.requested",
        {"invocation": record.to_graph(), "reason": reason},
        actor=actor,
    )
    runtime.run_until_idle()
    approval = next(
        (
            item
            for item in runtime.pending_approvals()
            if item.data.get("invocation_id") == identity.invocation_id
        ),
        None,
    )
    if approval is None:
        msg = f"ActiveGraph did not materialize approval for {identity.invocation_id}"
        raise RuntimeError(msg)
    return StepResult(
        step_id=approval.id,
        run_id=runtime.run_id,
        skill=skill_tool.skill,
        tool=skill_tool.tool_id,
        scope=skill_tool.scope,
        args=args_json,
        output={},
        status="pending_approval",
        approval_id=approval.id,
        phase=phase_name,
        phase_id=phase_id,
        invocation_id=identity.invocation_id,
        idempotency_key=identity.idempotency_key,
        attempt=identity.attempt,
    )


def recover_incomplete_invocations(runtime: Runtime) -> list[dict[str, Any]]:
    """Classify crash-interrupted attempts without replaying external writes."""
    recovered: list[dict[str, Any]] = []
    for invocation in runtime.graph.objects(type="rpa.invocation"):
        if invocation.data.get("status") != "running":
            continue
        updates = {
            "status": "outcome_unknown",
            "error": "process stopped before a durable outcome; reconcile before retry",
            "completed_at": _now(),
        }
        runtime.graph.patch_object(invocation.id, updates, actor="roi_h.recovery")
        recovered.append(
            {
                "id": invocation.id,
                "from": "running",
                "to": "outcome_unknown",
                "effect": invocation.data.get("effect"),
            }
        )
    return recovered


def rejected_step(
    runtime: Runtime,
    skill_tool: SkillTool,
    payload: dict[str, Any],
    *,
    workspace: Workspace,
    identity: InvocationIdentity,
    exc: Exception,
) -> StepResult:
    """Record a pre-dispatch rejection without creating an invocation object."""
    failure = _failure_from_exception(exc)
    failure = failure.model_copy(
        update={"message": str(redact_secret_values(failure.message, workspace))}
    )
    phase_name, phase_id = phase_tags(runtime)
    step = _record_step(
        runtime.graph,
        skill_tool=skill_tool,
        run_id=runtime.run_id,
        args=json_object(redact_secret_values(payload, workspace)),
        output={},
        status="error",
        error=failure.message,
        failure=failure,
        identity=identity,
        approval_id=None,
        phase=phase_name,
        phase_id=phase_id,
    )
    return _step_result(step)


def _unknown_outcome(
    runtime: Runtime,
    invocation_object_id: str,
    skill_tool: SkillTool,
    args: dict[str, Any],
    identity: InvocationIdentity,
) -> StepResult:
    runtime.graph.patch_object(
        invocation_object_id,
        {
            "status": "outcome_unknown",
            "error": "ActiveGraph behavior ended without a durable step outcome",
            "completed_at": _now(),
        },
        actor=_EXECUTE_BEHAVIOR,
    )
    failure = ExecutionFailure(
        kind="unknown",
        message="tool outcome is unknown; reconcile before retry",
        retryable=False,
        details={"invocation_object_id": invocation_object_id},
    )
    phase_name, phase_id = phase_tags(runtime)
    step = _record_step(
        runtime.graph,
        skill_tool=skill_tool,
        run_id=runtime.run_id,
        args=args,
        output={},
        status="error",
        error=failure.message,
        failure=failure,
        identity=identity,
        approval_id=None,
        phase=phase_name,
        phase_id=phase_id,
    )
    return _step_result(step)


def _run_worker(
    skill_tool: SkillTool,
    payload: dict[str, Any],
    *,
    workspace: Workspace,
    run_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    paths = RunStorage(workspace).prepare(run_id)
    env = _worker_environment(
        skill_tool,
        payload,
        workspace=workspace,
        run_id=run_id,
        idempotency_key=idempotency_key,
    )
    request = json.dumps(
        {
            "operation": "invoke",
            "script": str(skill_tool.script_path),
            "skill_root": str(skill_tool.script_path.parent.parent),
            "expected_sha256": skill_tool.script_sha256,
            "expected_tree_sha256": skill_tool.skill_tree_sha256,
            "reject_bytecode": skill_tool.reject_bytecode,
            "args": payload,
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "roi_h.harness.worker"],
        input=request,
        text=True,
        capture_output=True,
        check=False,
        cwd=paths.work,
        env=env,
        timeout=skill_tool.timeout_seconds,
    )
    if len(completed.stdout) > WORKER_PROTOCOL_LIMIT:
        msg = f"isolated worker response is too large: {skill_tool.name}"
        raise RuntimeError(msg)
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        msg = f"isolated worker returned invalid JSON (exit={completed.returncode})"
        raise RuntimeError(msg) from exc
    if bool(response.get("ok")) != (completed.returncode == 0):
        msg = f"isolated worker response does not match exit {completed.returncode}"
        raise RuntimeError(msg)
    if not response.get("ok"):
        raise SkillWorkerError(
            str(response.get("error") or "isolated worker failed"),
            stage=str(response.get("stage") or "unknown"),
        )
    output = response.get("output")
    if not isinstance(output, dict):
        msg = f"{skill_tool.name} output must be an object"
        raise TypeError(msg)
    return output


def _worker_environment(
    skill_tool: SkillTool,
    payload: dict[str, Any],
    *,
    workspace: Workspace,
    run_id: str,
    idempotency_key: str,
) -> dict[str, str]:
    paths = RunStorage(workspace).prepare(run_id)
    env = isolated_process_environment()
    browser_environment = {
        "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_SKIP_BROWSER_GC",
        "ROI_H_BROWSER",
        "ROI_H_BROWSER_HEADED",
        "ROI_H_BROWSER_SLOW_MO",
        "ROI_H_BROWSER_CONNECT_TIMEOUT_MS",
    }
    env.update(
        {key: value for key, value in os.environ.items() if key.upper() in browser_environment}
    )
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(managed_browser_root()))
    env.setdefault("PLAYWRIGHT_SKIP_BROWSER_GC", "1")
    env.update(
        {
            "ROI_H_HOME": str(workspace.root),
            "ROI_H_PROJECT": workspace.project,
            "ROI_H_ENV": workspace.env,
            "ROI_H_RUN_ID": run_id,
            "ROI_H_IDEMPOTENCY_KEY": idempotency_key,
            "ROI_H_DB": str(workspace.db),
            "ROI_H_EXTERNAL_IO_MODE": "runtime_recorded",
            "ROI_H_RUN_DIR": str(paths.work),
            "ROI_H_RUN_INPUT": str(paths.input),
            "ROI_H_RUN_OUTPUT": str(paths.output),
            "ROI_H_RUN_TMP": str(paths.tmp),
            "ROI_H_BROWSER_STATE": str(paths.runtime / "browser-session.json"),
        }
    )
    names = set(skill_tool.secret_names) | secret_names_in_refs(payload)
    for name in sorted(names):
        value = get_secret(workspace, name)
        if value is not None:
            env[f"ROI_H_SECRET_{name.upper()}"] = value
    return env


def _enforce_policy(skill_tool: SkillTool, workspace: Workspace) -> None:
    if workspace.env == "prod" and not skill_tool.allow_in_prod:
        msg = (
            f"production policy denies {skill_tool.name} "
            f"(effect={skill_tool.effect}); publish an explicitly allowed adapter"
        )
        raise ToolPolicyError(msg)


def _find_completed_attempt(
    runtime: Runtime,
    identity: InvocationIdentity,
) -> Any | None:
    for invocation in runtime.graph.objects(type="rpa.invocation"):
        if (
            invocation.data.get("idempotency_key") == identity.idempotency_key
            and int(invocation.data.get("attempt") or 1) == identity.attempt
            and invocation.data.get("status") in {"succeeded", "failed"}
            and invocation.data.get("step_id")
        ):
            return runtime.graph.get_object(str(invocation.data["step_id"]))
    return None


def _record_step(
    graph: Any,
    *,
    skill_tool: SkillTool,
    run_id: str,
    args: dict[str, Any],
    output: dict[str, Any],
    status: StepStatus,
    error: str | None,
    failure: ExecutionFailure | None,
    identity: InvocationIdentity,
    approval_id: str | None,
    phase: str | None,
    phase_id: str | None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_seconds: float | None = None,
) -> Any:
    record = StepRecord(
        run_id=run_id,
        skill=skill_tool.skill,
        tool=skill_tool.tool_id,
        name=skill_tool.name,
        scope=skill_tool.scope,
        args=args,
        output=output,
        status=status,
        error=error,
        failure=failure,
        approval_id=approval_id,
        phase=phase,
        phase_id=phase_id,
        invocation_id=identity.invocation_id,
        idempotency_key=identity.idempotency_key,
        attempt=identity.attempt,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration_seconds,
    )
    return graph.add_object("rpa.step", record.to_graph())


def _step_result(step: Any) -> StepResult:
    data = dict(step.data)
    return StepResult(
        step_id=step.id,
        run_id=str(data["run_id"]),
        skill=str(data["skill"]),
        tool=str(data["tool"]),
        scope=data.get("scope", "global"),
        args=dict(data.get("args") or {}),
        output=dict(data.get("output") or {}),
        status=data["status"],
        error=str(data["error"]) if data.get("error") is not None else None,
        failure=data.get("failure"),
        approval_id=(str(data["approval_id"]) if data.get("approval_id") else None),
        phase=str(data["phase"]) if data.get("phase") else None,
        phase_id=str(data["phase_id"]) if data.get("phase_id") else None,
        invocation_id=str(data["invocation_id"]),
        idempotency_key=str(data["idempotency_key"]),
        attempt=int(data.get("attempt") or 1),
    )


def _failure_from_exception(exc: Exception) -> ExecutionFailure:
    kind: FailureKind
    if isinstance(exc, ToolError):
        reason = exc.reason
        kind = cast(
            "FailureKind",
            {
                "tool.invalid_input": "validation",
                "tool.invalid_output": "validation",
                "tool.timeout": "timeout",
                "tool.execution_error": "internal",
            }.get(reason, "unknown"),
        )
        retryable = reason in {"tool.timeout", "tool.network_error"}
        return ExecutionFailure(
            kind=kind,
            message=str(exc),
            exception_type=type(exc).__name__,
            retryable=retryable,
            details={"reason": reason, **exc.payload_extras},
        )
    if isinstance(exc, ToolPolicyError):
        kind = "approval"
    elif isinstance(exc, SkillWorkerError):
        kind = "validation" if exc.stage in {"contract", "input", "output"} else "internal"
    elif isinstance(exc, (TypeError, ValueError, KeyError)):
        kind = "validation"
    elif isinstance(exc, TimeoutError):
        kind = "timeout"
    else:
        kind = "internal"
    return ExecutionFailure(
        kind=kind,
        message=f"{type(exc).__name__}: {exc}",
        exception_type=type(exc).__name__,
        retryable=isinstance(exc, TimeoutError),
        details={"stage": exc.stage} if isinstance(exc, SkillWorkerError) else {},
    )


def _emit_invocation_failure_diagnostic(
    workspace: Workspace,
    *,
    skill_tool: SkillTool,
    run_id: str,
    invocation_id: str,
    failure: ExecutionFailure,
) -> str:
    code = {
        "internal": "tool.runtime_failure",
        "timeout": "tool.timeout",
        "validation": "tool.output_contract_failure",
        "unknown": "tool.unknown_failure",
    }.get(failure.kind, "tool.execution_failure")
    record = DiagnosticRecord(
        code=code,
        message=f"{skill_tool.name} failed during isolated tool execution.",
        component="harness.invocation_runtime",
        project_id=workspace.project_id,
        project=workspace.project,
        environment=workspace.env,
        run_id=run_id,
        invocation_id=invocation_id,
        exception_type=failure.exception_type,
        details={
            "tool": skill_tool.name,
            "failure_kind": failure.kind,
            "retryable": failure.retryable,
            "error": failure.message,
            **failure.details,
        },
    )
    DiagnosticSink(workspace.root).emit(record)
    return record.diagnostic_id


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run_id_from_key(idempotency_key: str) -> str:
    parts = idempotency_key.split(":", 2)
    return parts[1] if len(parts) == 3 and parts[0] == "roi-h" else ""


def _emit_runtime_event(
    runtime: Runtime,
    event_type: str,
    payload: dict[str, Any],
    *,
    actor: str,
) -> Event:
    event = Event(
        id=runtime.graph.ids.event(),
        type=event_type,
        payload=payload,
        actor=actor,
        frame_id=runtime.frame.id if runtime.frame else None,
        caused_by=None,
        timestamp=runtime.graph.clock.now(),
    )
    runtime.graph.emit(event)
    return event
