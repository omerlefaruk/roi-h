"""Typed operation catalog with a small routing interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from roi_h import __version__
from roi_h.agent.contract import (
    JSON_SCHEMA_DIALECT,
    CommandRequest,
    Effect,
    ExecutionMode,
    Idempotency,
    OperationManifest,
)
from roi_h.agent.read_operations import (
    approval_list,
    approval_show,
    artifact_list,
    artifact_show,
    automation_compare,
    automation_list,
    automation_show,
    diagnostic_list,
    list_runs,
    project_show,
    retention_show,
    run_events,
    run_trace,
    secret_list,
    secret_status,
    show_run,
    skill_list,
    skill_show,
    store_check,
    store_status,
    tool_list,
    tool_show,
)
from roi_h.agent.tasks import task_cancel, task_events, task_list, task_show, task_wait
from roi_h.agent.workflow_operations import (
    artifact_export,
    artifact_put,
    automation_run,
    automation_ship,
    environment_set,
    phase_begin,
    phase_end,
    phase_fail,
    phase_list,
    phase_retry,
    phase_skip,
    project_doctor,
    project_export,
    project_import,
    project_import_verify,
    project_rename,
    project_use,
    retention_apply,
    retention_plan,
    run_cancel,
    run_files,
    run_input_add,
    run_reconcile,
    secret_delete_operation,
    secret_set_operation,
    skill_define,
    skill_delete_apply,
    skill_delete_plan,
    skill_promote,
    store_compact_apply,
    store_compact_plan,
    store_migrate_apply,
    store_migrate_plan,
    store_restore_apply,
    store_restore_plan,
    support_bundle_create,
    system_doctor,
)
from roi_h.agent.write_operations import (
    approval_approve,
    approval_reject,
    project_create,
    project_delete_apply,
    project_delete_plan,
    run_start,
    store_backup,
    tool_invoke,
)
from roi_h.harness.workspace import Workspace, list_projects

OperationHandler = Callable[[CommandRequest], dict[str, Any]]


@dataclass(frozen=True)
class OperationDefinition:
    """One public manifest and its private handler."""

    manifest: OperationManifest
    handler: OperationHandler


class OperationCatalog:
    """Validate, describe, and route operations without owning domain rules."""

    def __init__(self) -> None:
        """Create an empty catalog."""
        self._operations: dict[str, OperationDefinition] = {}

    def register(self, definition: OperationDefinition) -> None:
        """Register one complete operation definition."""
        operation_id = definition.manifest.operation_id
        if operation_id in self._operations:
            msg = f"duplicate operation ID: {operation_id}"
            raise ValueError(msg)
        for schema_name, schema in (
            ("input", definition.manifest.input_schema),
            ("output", definition.manifest.output_schema),
        ):
            if schema.get("$schema") != JSON_SCHEMA_DIALECT:
                msg = f"{operation_id} {schema_name} schema must use JSON Schema 2020-12"
                raise ValueError(msg)
            Draft202012Validator.check_schema(schema)
        self._operations[operation_id] = definition

    def describe(self, operation_id: str | None = None) -> list[OperationManifest]:
        """Describe all operations or one selected operation."""
        if operation_id is not None:
            definition = self._operations.get(operation_id)
            if definition is None:
                msg = f"operation not found: {operation_id}"
                raise KeyError(msg)
            return [definition.manifest]
        return [self._operations[key].manifest for key in sorted(self._operations)]

    def execute(self, operation_id: str, request: CommandRequest) -> dict[str, Any]:
        """Run one operation handler."""
        definition = self._operations.get(operation_id)
        if definition is None:
            msg = f"operation not found: {operation_id}"
            raise KeyError(msg)
        return definition.handler(request)


def build_catalog() -> OperationCatalog:
    """Build the contract version 1.0 catalog."""
    catalog = OperationCatalog()
    catalog.register(
        _read_operation(
            "system.describe",
            "Describe all operations or one selected operation.",
            _system_describe,
            properties={"operation": {"type": ["string", "null"]}},
        )
    )
    for operation_id, description, handler in (
        ("run.list", "List durable runs.", list_runs),
        ("run.show", "Show one durable run.", show_run),
        ("run.status", "Show one durable run status.", show_run),
        ("run.events", "Read canonical ordered run events.", run_events),
        ("run.trace", "Read a bounded product trace.", run_trace),
    ):
        catalog.register(
            _read_operation(
                operation_id,
                description,
                handler,
                pagination=operation_id in {"run.list", "run.events"},
                properties={
                    "home": {"type": ["string", "null"]},
                    "run_id": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "cursor": {"type": ["string", "null"]},
                    "after": {"type": ["string", "null"]},
                },
            )
        )
    common_properties = {
        "home": {"type": ["string", "null"]},
        "project": {"type": ["string", "null"]},
        "environment": {"enum": ["dev", "prod", None]},
        "run_id": {"type": ["string", "null"]},
        "name": {"type": ["string", "null"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": {"type": ["string", "null"]},
        "skills": {"type": ["string", "null"]},
        "version": {"type": ["string", "null"]},
        "version_a": {"type": ["string", "null"]},
        "version_b": {"type": ["string", "null"]},
        "approval_id": {"type": ["string", "null"]},
        "artifact_id": {"type": ["string", "null"]},
        "plan_id": {"type": ["string", "null"]},
        "full": {"type": "boolean"},
        "arguments": {"type": "object"},
        "source": {"type": ["string", "null"]},
        "source_path": {"type": ["string", "null"]},
        "output": {"type": ["string", "null"]},
        "mode": {"type": ["string", "null"]},
        "new_name": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
        "by": {"type": ["string", "null"]},
        "repair": {"type": "boolean"},
        "description": {"type": ["string", "null"]},
        "summary": {"type": ["object", "null"]},
        "error": {"type": ["string", "null"]},
        "require_artifacts": {"type": ["array", "null"], "items": {"type": "string"}},
        "skill": {"type": ["string", "null"]},
        "tool": {"type": ["string", "null"]},
        "overwrite": {"type": "boolean"},
        "from_run": {"type": ["string", "null"]},
        "from_handoff": {"type": ["string", "null"]},
        "goal": {"type": ["string", "null"]},
        "notes": {"type": ["string", "null"]},
        "skills_list": {"type": ["array", "null"], "items": {"type": "string"}},
        "distill": {"type": "boolean"},
        "dry_run": {"type": "boolean"},
        "auto_approve": {"type": ["boolean", "null"]},
        "force": {"type": "boolean"},
        "actor": {"type": ["string", "null"]},
        "set_args": {"type": ["array", "null"], "items": {"type": "string"}},
        "secret_value": {"type": ["string", "null"]},
        "policy": {"type": ["object", "null"]},
        "target": {"type": ["string", "null"]},
        "use": {"type": "boolean"},
    }
    for operation_id, description, handler, paginated in (
        ("project.show", "Show one project.", project_show, False),
        ("project.paths", "Show logical project paths.", project_show, False),
        ("environment.show", "Show the selected environment.", project_show, False),
        ("environment.doctor", "Inspect the selected environment.", project_show, False),
        ("store.status", "Show store status.", store_status, False),
        ("store.check", "Check the selected store.", store_check, False),
        ("tool.list", "List tools and schemas.", tool_list, True),
        ("tool.show", "Show one tool and its schemas.", tool_show, False),
        ("approval.list", "List run approvals.", approval_list, True),
        ("approval.show", "Show one run approval.", approval_show, False),
        ("artifact.list", "List durable artifacts.", artifact_list, True),
        ("artifact.show", "Show one durable artifact.", artifact_show, False),
        ("skill.list", "List available skills.", skill_list, True),
        ("skill.show", "Show one available skill.", skill_show, False),
        ("skill.validate", "Validate one available skill.", skill_show, False),
        ("automation.list", "List immutable automations.", automation_list, True),
        ("automation.show", "Show one immutable automation.", automation_show, False),
        ("automation.verify", "Verify one immutable automation.", automation_show, False),
        ("automation.compare", "Compare two automation versions.", automation_compare, False),
        ("secret.list", "List configured secret names.", secret_list, True),
        ("secret.status", "Show names-only secret status.", secret_status, False),
        ("retention.show", "Show one retention plan.", retention_show, False),
        ("diagnostic.list", "List redacted diagnostics.", diagnostic_list, True),
        ("diagnostic.tail", "Read the redacted diagnostic tail.", diagnostic_list, True),
    ):
        catalog.register(
            _read_operation(
                operation_id,
                description,
                handler,
                pagination=paginated,
                properties=common_properties,
            )
        )
    for operation_id, description, handler, effect, idempotency, plan_rule in (
        (
            "project.create",
            "Create one project.",
            project_create,
            Effect.WRITE,
            Idempotency.SUPPORTED,
            "none",
        ),
        (
            "store.backup",
            "Create a consistent store backup as a durable task.",
            store_backup,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "run.start",
            "Start one durable run.",
            run_start,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "tool.invoke",
            "Invoke one tool through ActiveGraph authority.",
            tool_invoke,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "approval.approve",
            "Approve and execute one deferred invocation.",
            approval_approve,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "approval.reject",
            "Reject one deferred invocation without execution.",
            approval_reject,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "project.delete.plan",
            "Plan recoverable project deletion.",
            project_delete_plan,
            Effect.WRITE,
            Idempotency.NOT_APPLICABLE,
            "creates_plan",
        ),
        (
            "project.delete.apply",
            "Apply a reviewed project deletion plan.",
            project_delete_apply,
            Effect.DESTRUCTIVE,
            Idempotency.REQUIRED,
            "required",
        ),
    ):
        catalog.register(
            _operation(
                operation_id,
                description,
                handler,
                effect=effect,
                idempotency=idempotency,
                plan_rule=plan_rule,
                properties=common_properties
                | {
                    "display_name": {"type": ["string", "null"]},
                    "use": {"type": "boolean"},
                },
            )
        )
    for operation_id, description, handler, effect in (
        ("task.list", "List durable tasks.", task_list, Effect.READ),
        ("task.show", "Show one durable task.", task_show, Effect.READ),
        ("task.events", "Read resumable task events.", task_events, Effect.READ),
        ("task.wait", "Wait for or poll one durable task.", task_wait, Effect.READ),
        ("task.cancel", "Cancel one durable task.", task_cancel, Effect.WRITE),
    ):
        catalog.register(
            _operation(
                operation_id,
                description,
                handler,
                effect=effect,
                idempotency=Idempotency.NOT_APPLICABLE,
                pagination=operation_id in {"task.list", "task.events"},
                properties=common_properties
                | {
                    "task_id": {"type": ["string", "null"]},
                    "after": {"type": ["string", "null"]},
                    "timeout_seconds": {"type": "number", "minimum": 0},
                },
            )
        )
    catalog.register(
        _read_operation(
            "system.context",
            "Show the bounded selected context.",
            _system_context,
            properties={
                "home": {"type": ["string", "null"]},
                "project": {"type": ["string", "null"]},
                "environment": {"enum": ["dev", "prod", None]},
            },
        )
    )
    catalog.register(
        _read_operation(
            "system.version",
            "Show the installed ROI-H and contract versions.",
            _system_version,
        )
    )
    catalog.register(
        _read_operation(
            "project.list",
            "List projects without physical paths.",
            _project_list,
            pagination=True,
            properties={"home": {"type": ["string", "null"]}},
        )
    )
    for operation_id, description, handler in (
        ("system.doctor", "Check the selected installation and project.", system_doctor),
        ("project.doctor", "Check one project and its selected store.", project_doctor),
    ):
        catalog.register(
            _read_operation(
                operation_id,
                description,
                handler,
                properties=common_properties,
            )
        )
    for operation_id, description, handler, effect, idempotency, plan_rule in (
        (
            "project.use",
            "Select one active project.",
            project_use,
            Effect.WRITE,
            Idempotency.SUPPORTED,
            "none",
        ),
        (
            "project.export",
            "Export a portable project archive.",
            project_export,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "project.import.verify",
            "Verify a project archive.",
            project_import_verify,
            Effect.READ,
            Idempotency.NOT_APPLICABLE,
            "none",
        ),
        (
            "project.import",
            "Import a verified project archive.",
            project_import,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "project.rename",
            "Rename one project.",
            project_rename,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "environment.set",
            "Select a project environment.",
            environment_set,
            Effect.WRITE,
            Idempotency.SUPPORTED,
            "none",
        ),
        (
            "run.cancel",
            "Request cooperative run cancellation.",
            run_cancel,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "run.reconcile",
            "Reconcile durable run records.",
            run_reconcile,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "run.input.add",
            "Add one materialized run input.",
            run_input_add,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "run.files",
            "List one run's logical files.",
            run_files,
            Effect.READ,
            Idempotency.NOT_APPLICABLE,
            "none",
        ),
        (
            "phase.list",
            "List run phases.",
            phase_list,
            Effect.READ,
            Idempotency.NOT_APPLICABLE,
            "none",
        ),
        (
            "phase.begin",
            "Begin one run phase.",
            phase_begin,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "phase.end",
            "End the open run phase.",
            phase_end,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "phase.fail",
            "Fail the open run phase.",
            phase_fail,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "phase.skip",
            "Skip one run phase.",
            phase_skip,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "phase.retry",
            "Retry one run phase.",
            phase_retry,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "artifact.put",
            "Attach one file artifact.",
            artifact_put,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "artifact.export",
            "Export one artifact.",
            artifact_export,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "skill.define",
            "Define one project-owned skill tool.",
            skill_define,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "skill.promote",
            "Promote one project skill to user-shared storage.",
            skill_promote,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "skill.delete.plan",
            "Plan deletion of one user-shared skill.",
            skill_delete_plan,
            Effect.WRITE,
            Idempotency.NOT_APPLICABLE,
            "creates_plan",
        ),
        (
            "skill.delete.apply",
            "Apply a reviewed skill deletion plan.",
            skill_delete_apply,
            Effect.DESTRUCTIVE,
            Idempotency.REQUIRED,
            "required",
        ),
        (
            "automation.ship",
            "Ship one immutable automation.",
            automation_ship,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "automation.run",
            "Run one immutable automation.",
            automation_run,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "secret.set",
            "Set one secret through the secure input channel.",
            secret_set_operation,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "secret.delete",
            "Delete one secret.",
            secret_delete_operation,
            Effect.DESTRUCTIVE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "retention.plan",
            "Plan conservative retention.",
            retention_plan,
            Effect.WRITE,
            Idempotency.NOT_APPLICABLE,
            "creates_plan",
        ),
        (
            "retention.apply",
            "Apply one retention plan.",
            retention_apply,
            Effect.DESTRUCTIVE,
            Idempotency.REQUIRED,
            "required",
        ),
        (
            "store.restore.plan",
            "Plan a store restore.",
            store_restore_plan,
            Effect.WRITE,
            Idempotency.NOT_APPLICABLE,
            "creates_plan",
        ),
        (
            "store.restore.apply",
            "Apply a reviewed store restore.",
            store_restore_apply,
            Effect.DESTRUCTIVE,
            Idempotency.REQUIRED,
            "required",
        ),
        (
            "store.migrate.plan",
            "Plan a store migration.",
            store_migrate_plan,
            Effect.WRITE,
            Idempotency.NOT_APPLICABLE,
            "creates_plan",
        ),
        (
            "store.migrate.apply",
            "Apply a reviewed store migration.",
            store_migrate_apply,
            Effect.DESTRUCTIVE,
            Idempotency.REQUIRED,
            "required",
        ),
        (
            "store.compact.plan",
            "Plan store compaction.",
            store_compact_plan,
            Effect.WRITE,
            Idempotency.NOT_APPLICABLE,
            "creates_plan",
        ),
        (
            "store.compact.apply",
            "Apply reviewed store compaction.",
            store_compact_apply,
            Effect.DESTRUCTIVE,
            Idempotency.REQUIRED,
            "required",
        ),
        (
            "support_bundle.create",
            "Create one bounded redacted support bundle.",
            support_bundle_create,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
    ):
        catalog.register(
            _operation(
                operation_id,
                description,
                handler,
                effect=effect,
                idempotency=idempotency,
                plan_rule=plan_rule,
                properties=common_properties,
                secret_input_paths=["arguments.secret_value"]
                if operation_id == "secret.set"
                else [],
            )
        )
    return catalog


def _read_operation(
    operation_id: str,
    description: str,
    handler: OperationHandler,
    *,
    pagination: bool = False,
    properties: dict[str, Any] | None = None,
) -> OperationDefinition:
    return _operation(
        operation_id,
        description,
        handler,
        effect=Effect.READ,
        idempotency=Idempotency.NOT_APPLICABLE,
        pagination=pagination,
        properties=properties,
    )


def _operation(  # noqa: PLR0913 - Descriptor fields stay explicit at registration.
    operation_id: str,
    description: str,
    handler: OperationHandler,
    *,
    effect: Effect,
    idempotency: Idempotency,
    plan_rule: str = "none",
    pagination: bool = False,
    properties: dict[str, Any] | None = None,
    secret_input_paths: list[str] | None = None,
) -> OperationDefinition:
    return OperationDefinition(
        manifest=OperationManifest(
            operation_id=operation_id,
            description=description,
            input_schema=_object_schema(properties or {}),
            output_schema=_object_schema({}, allow_additional=True),
            effect=effect,
            idempotency=idempotency,
            approval_rule="none",
            plan_rule=plan_rule,
            secret_input_paths=secret_input_paths or [],
            filesystem_requirements=[],
            network_requirements=[],
            pagination=pagination,
            execution_mode=ExecutionMode.SYNC,
            timeout_seconds=30,
        ),
        handler=handler,
    )


def _object_schema(
    properties: dict[str, Any],
    *,
    allow_additional: bool = False,
) -> dict[str, Any]:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": properties,
        "additionalProperties": allow_additional,
    }


def _system_version(_request: CommandRequest) -> dict[str, Any]:
    return {"version": __version__, "contract_version": "1.0"}


def _system_describe(_request: CommandRequest) -> dict[str, Any]:
    return {"operation": "system.describe"}


def _system_context(request: CommandRequest) -> dict[str, Any]:
    arguments = request.arguments
    items = list_projects(arguments.get("home"))
    if not items:
        return {
            "project": None,
            "environment": None,
            "health_warnings": ["No project is available."],
            "recent_runs": [],
            "pending_approvals": [],
            "safe_next_actions": [{"operation": "project.create"}],
        }
    workspace = Workspace.open(
        arguments.get("home"),
        project=arguments.get("project"),
        env=arguments.get("environment"),
    )
    return {
        "project": workspace.project,
        "project_id": workspace.project_id,
        "environment": workspace.env,
        "health_warnings": [],
        "recent_runs": [],
        "pending_approvals": [],
        "safe_next_actions": [
            {"operation": "tool.list"},
            {"operation": "run.list"},
        ],
    }


def _project_list(request: CommandRequest) -> dict[str, Any]:
    items = list_projects(request.arguments.get("home"))
    safe_items = [{key: value for key, value in item.items() if key != "path"} for item in items]
    return {"items": safe_items, "count": len(safe_items)}


__all__ = [
    "OperationCatalog",
    "OperationDefinition",
    "OperationHandler",
    "build_catalog",
]
