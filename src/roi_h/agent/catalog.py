"""Typed operation catalog with a small routing interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from roi_h import __version__
from roi_h.agent.contract import (
    CommandRequest,
    Effect,
    ExecutionMode,
    Idempotency,
    OperationManifest,
)
from roi_h.agent.operation_models import OperationModel, input_model, output_model
from roi_h.agent.read_operations import (
    artifact_list,
    artifact_show,
    automation_compare,
    automation_list,
    automation_show,
    automation_source_list,
    automation_source_show,
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
)
from roi_h.agent.tasks import task_cancel, task_events, task_list, task_show, task_wait
from roi_h.agent.workflow_operations import (
    artifact_export,
    automation_dev_run,
    automation_run,
    automation_ship,
    automation_source_put,
    environment_doctor,
    environment_set,
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
    secret_delete_operation,
    secret_set_operation,
    store_restore_apply,
    store_restore_plan,
    support_bundle_create,
    system_doctor,
)
from roi_h.agent.write_operations import (
    project_create,
    project_delete_apply,
    project_delete_plan,
    store_backup,
)
from roi_h.harness.workspace import Workspace, list_projects
from roi_h.observer.activegraph_adapter import ActiveGraphProjectionAdapter

OperationHandler = Callable[[CommandRequest], dict[str, Any]]


@dataclass(frozen=True)
class OperationDefinition:
    """One public manifest, its validation models, and its private handler."""

    manifest: OperationManifest
    handler: OperationHandler
    input_model: OperationModel
    output_model: OperationModel


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
        for schema_name, model, schema in (
            ("input", definition.input_model, definition.manifest.input_schema),
            ("output", definition.output_model, definition.manifest.output_schema),
        ):
            if schema != model.model_json_schema():
                msg = f"{operation_id} {schema_name} schema must come from its Pydantic model"
                raise ValueError(msg)
        self._operations[operation_id] = definition

    def definition(self, operation_id: str) -> OperationDefinition:
        """Return one complete operation definition."""
        definition = self._operations.get(operation_id)
        if definition is None:
            msg = f"operation not found: {operation_id}"
            raise KeyError(msg)
        return definition

    def describe(self, operation_id: str | None = None) -> list[OperationManifest]:
        """Describe all operations or one selected operation."""
        if operation_id is not None:
            return [self.definition(operation_id).manifest]
        return [self._operations[key].manifest for key in sorted(self._operations)]

    def execute(self, operation_id: str, request: CommandRequest) -> dict[str, Any]:
        """Run one operation handler."""
        return self.definition(operation_id).handler(request)


def build_catalog() -> OperationCatalog:
    """Build the contract version 1.0 catalog."""
    catalog = OperationCatalog()
    catalog.register(
        _read_operation(
            "system.describe",
            "Describe all operations or one selected operation.",
            _system_describe,
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
            )
        )
    for operation_id, description, handler, paginated in (
        ("project.show", "Show one project.", project_show, False),
        ("project.paths", "Show logical project paths.", project_show, False),
        ("environment.show", "Show the selected environment.", project_show, False),
        ("environment.doctor", "Inspect the selected environment.", environment_doctor, False),
        ("store.status", "Show store status.", store_status, False),
        ("store.check", "Check the selected store.", store_check, False),
        ("artifact.list", "List durable artifacts.", artifact_list, True),
        ("artifact.show", "Show one durable artifact.", artifact_show, False),
        ("skill.list", "List available skills.", skill_list, True),
        ("skill.show", "Show one available skill.", skill_show, False),
        ("skill.validate", "Validate one available skill.", skill_show, False),
        (
            "automation.source.list",
            "List editable modular automation sources.",
            automation_source_list,
            True,
        ),
        (
            "automation.source.show",
            "Show one editable modular automation source.",
            automation_source_show,
            False,
        ),
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
            )
        )
    catalog.register(
        _read_operation(
            "system.context",
            "Show the bounded selected context.",
            _system_context,
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
            "artifact.export",
            "Export one artifact.",
            artifact_export,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "automation.source.put",
            "Create or replace one editable modular automation source.",
            automation_source_put,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
        ),
        (
            "automation.dev.run",
            "Run one frozen modular source in development.",
            automation_dev_run,
            Effect.WRITE,
            Idempotency.REQUIRED,
            "none",
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
) -> OperationDefinition:
    return _operation(
        operation_id,
        description,
        handler,
        effect=Effect.READ,
        idempotency=Idempotency.NOT_APPLICABLE,
        pagination=pagination,
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
    secret_input_paths: list[str] | None = None,
) -> OperationDefinition:
    operation_input = input_model(operation_id)
    operation_output = output_model(operation_id, pagination=pagination)
    return OperationDefinition(
        manifest=OperationManifest(
            operation_id=operation_id,
            description=description,
            input_schema=operation_input.model_json_schema(),
            output_schema=operation_output.model_json_schema(),
            effect=effect,
            idempotency=idempotency,
            approval_rule="none",
            plan_rule=plan_rule,
            secret_input_paths=secret_input_paths or [],
            filesystem_requirements=[],
            network_requirements=[],
            pagination=pagination,
            execution_mode=ExecutionMode.TASK
            if operation_id == "store.backup"
            else ExecutionMode.SYNC,
            timeout_seconds=(
                3600
                if operation_id == "store.backup"
                else 172800
                if operation_id in {"automation.dev.run", "automation.run"}
                else 30
            ),
        ),
        handler=handler,
        input_model=operation_input,
        output_model=operation_output,
    )


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
        db=arguments.get("db"),
    )
    recent_runs: list[dict[str, Any]] = []
    warnings: list[str] = []
    if workspace.db.is_file():
        adapter = ActiveGraphProjectionAdapter(workspace.db)
        recent_runs.extend(
            {
                "run_id": str(header["run_id"]),
                "created_at": str(header.get("created_at") or ""),
                "goal": str(header.get("goal") or header.get("label") or ""),
            }
            for header in adapter.list_run_headers(limit=10)
        )
    else:
        warnings.append("The selected environment has no run store yet.")
    return {
        "project": workspace.project,
        "project_id": workspace.project_id,
        "environment": workspace.env,
        "health_warnings": warnings,
        "recent_runs": recent_runs,
        "pending_approvals": [],
        "safe_next_actions": [
            {"operation": "skill.list"},
            {"operation": "automation.source.list"},
            {"operation": "run.list"},
        ],
    }


def _project_list(request: CommandRequest) -> dict[str, Any]:
    items = list_projects(request.arguments.get("home"))
    safe_items = [{key: value for key, value in item.items() if key != "path"} for item in items]
    limit = min(int(request.arguments.get("limit") or 50), 200)
    cursor = request.arguments.get("cursor")
    offset = 0
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor.startswith("offset:"):
            message = "cursor is invalid"
            raise ValueError(message)
        offset = int(cursor.removeprefix("offset:"))
    selected = safe_items[offset : offset + limit]
    has_more = offset + limit < len(safe_items)
    return {
        "items": selected,
        "count": len(safe_items),
        "next_cursor": f"offset:{offset + limit}" if has_more else None,
        "has_more": has_more,
        "snapshot": f"projects:{len(safe_items)}",
    }


__all__ = [
    "OperationCatalog",
    "OperationDefinition",
    "OperationHandler",
    "build_catalog",
]
