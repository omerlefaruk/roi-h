"""Typed operation catalog with a small routing interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from roi_h import __version__
from roi_h.agent.contract import (
    JSON_SCHEMA_DIALECT,
    CommandRequest,
    Effect,
    ExecutionMode,
    Idempotency,
    OperationManifest,
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
        self._operations[operation_id] = definition

    def describe(self, operation_id: str | None = None) -> list[OperationManifest]:
        """Describe all operations or one selected operation."""
        if operation_id is not None:
            definition = self._operations.get(operation_id)
            if definition is None:
                msg = f"operation not found: {operation_id}"
                raise KeyError(msg)
            return [definition.manifest]
        return [
            self._operations[key].manifest
            for key in sorted(self._operations)
        ]

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
    return catalog


def _read_operation(
    operation_id: str,
    description: str,
    handler: OperationHandler,
    *,
    pagination: bool = False,
    properties: dict[str, Any] | None = None,
) -> OperationDefinition:
    return OperationDefinition(
        manifest=OperationManifest(
            operation_id=operation_id,
            description=description,
            input_schema=_object_schema(properties or {}),
            output_schema=_object_schema({}, allow_additional=True),
            effect=Effect.READ,
            idempotency=Idempotency.NOT_APPLICABLE,
            approval_rule="none",
            plan_rule="none",
            secret_input_paths=[],
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
    safe_items = [
        {
            key: value
            for key, value in item.items()
            if key != "path"
        }
        for item in items
    ]
    return {"items": safe_items, "count": len(safe_items)}


__all__ = [
    "OperationCatalog",
    "OperationDefinition",
    "OperationHandler",
    "build_catalog",
]
