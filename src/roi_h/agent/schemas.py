"""Operation-specific JSON Schema contracts for the agent interface."""

from __future__ import annotations

from typing import Any

from roi_h.agent.contract import JSON_SCHEMA_DIALECT

_PAGE_OPERATIONS = frozenset(
    {
        "approval.list",
        "artifact.list",
        "automation.list",
        "diagnostic.list",
        "diagnostic.tail",
        "project.list",
        "run.events",
        "run.list",
        "skill.list",
        "task.events",
        "task.list",
        "tool.list",
    }
)

_REQUIRED_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "approval.approve": ("approval_id",),
    "approval.reject": ("approval_id",),
    "approval.show": ("approval_id",),
    "artifact.export": ("artifact_id", "output"),
    "artifact.put": ("source",),
    "automation.compare": ("name", "version_a", "version_b"),
    "automation.run": ("name",),
    "automation.ship": ("name", "version", "from_run"),
    "automation.show": ("name",),
    "automation.verify": ("name",),
    "environment.set": ("environment",),
    "phase.begin": ("name",),
    "phase.fail": ("error",),
    "phase.retry": ("name",),
    "phase.skip": ("name",),
    "project.create": ("name",),
    "project.delete.apply": ("plan_id",),
    "project.delete.plan": ("name",),
    "project.export": ("output",),
    "project.import.verify": ("source",),
    "project.rename": ("name", "new_name"),
    "project.use": ("name",),
    "retention.apply": ("plan_id",),
    "retention.show": ("plan_id",),
    "run.input.add": ("source", "name"),
    "run.reconcile": (),
    "run.start": ("goal",),
    "secret.delete": ("name",),
    "secret.set": ("name",),
    "secret.status": ("name",),
    "skill.define": ("skill", "tool"),
    "skill.delete.apply": ("plan_id",),
    "skill.delete.plan": ("name",),
    "skill.promote": ("name",),
    "skill.show": ("name",),
    "skill.validate": ("name",),
    "store.backup": ("output",),
    "store.restore.apply": ("plan_id",),
    "store.restore.plan": ("source",),
    "support_bundle.create": ("output",),
    "task.cancel": ("task_id",),
    "task.events": ("task_id",),
    "task.show": ("task_id",),
    "task.wait": ("task_id",),
    "tool.invoke": ("name",),
}

_REQUIRED_ALTERNATIVES: dict[str, tuple[tuple[str, ...], ...]] = {
    "project.import": (("source",), ("plan_id",)),
}

_PAGE_PROPERTIES = {
    "items": {"type": "array", "items": {}},
    "next_cursor": {"type": ["string", "null"]},
    "has_more": {"type": "boolean"},
    "snapshot": {"type": "string"},
}

_OUTPUT_PROPERTIES: dict[str, dict[str, Any]] = {
    "system.describe": {"operation": {"type": "string"}},
    "system.version": {
        "version": {"type": "string"},
        "contract_version": {"type": "string"},
    },
    "system.context": {
        "project": {"type": ["string", "null"]},
        "environment": {"type": ["string", "null"]},
        "health_warnings": {"type": "array", "items": {"type": "string"}},
        "recent_runs": {"type": "array", "items": {}},
        "pending_approvals": {"type": "array", "items": {}},
        "safe_next_actions": {"type": "array", "items": {}},
    },
    "project.create": {
        "ok": {"type": "boolean"},
        "project": {"type": "string"},
        "project_id": {"type": "string"},
        "environment": {"type": "string"},
    },
    "project.delete.apply": {
        "ok": {"type": "boolean"},
        "deleted": {"type": "string"},
        "recoverable": {"type": "boolean"},
    },
    "project.doctor": {
        "project": {"type": "string"},
        "environment": {"type": "string"},
        "checks": {"type": "object"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "project.import": {
        "ok": {"type": "boolean"},
        "project": {"type": "string"},
        "project_id": {"type": "string"},
        "files": {"type": "integer"},
        "plan_id": {"type": "string"},
        "operation": {"type": "string"},
        "effects": {"type": "array", "items": {}},
        "state_digest": {"type": "string"},
        "expires_at": {"type": "string"},
        "apply_operation": {"type": "string"},
    },
    "project.import.verify": {
        "ok": {"type": "boolean"},
        "project": {"type": "string"},
        "project_id": {"type": "string"},
        "files": {"type": "integer"},
    },
    "project.rename": {
        "ok": {"type": "boolean"},
        "renamed": {"type": "boolean"},
        "from": {"type": "string"},
        "to": {"type": "string"},
    },
    "project.show": {
        "project": {"type": "string"},
        "project_id": {"type": "string"},
        "environment": {"type": "string"},
    },
    "project.paths": {
        "project": {"type": "string"},
        "project_id": {"type": "string"},
        "environment": {"type": "string"},
    },
    "environment.doctor": {
        "project": {"type": "string"},
        "environment": {"type": "string"},
        "checks": {"type": "object"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "environment.set": {
        "env": {"type": "string"},
        "environment": {"type": "string"},
    },
    "run.start": {
        "run_id": {"type": "string"},
        "object_id": {"type": "string"},
        "status": {"type": "string"},
        "project": {"type": "string"},
        "environment": {"type": "string"},
    },
    "run.input.add": {
        "run_id": {"type": "string"},
        "path": {"type": "string"},
        "bytes": {"type": "integer"},
    },
    "run.files": {
        "run_id": {"type": "string"},
        "files": {"type": "array", "items": {}},
        "artifacts": {"type": "array", "items": {}},
    },
    "run.reconcile": {
        "run_id": {"type": "string"},
        "ok": {"type": "boolean"},
        "issues": {"type": "array", "items": {}},
    },
    "artifact.put": {
        "artifact_id": {"type": "string"},
        "run_id": {"type": "string"},
        "name": {"type": "string"},
        "uri": {"type": "string"},
        "sha256": {"type": "string"},
        "bytes": {"type": "integer"},
    },
    "artifact.export": {
        "artifact_id": {"type": "string"},
        "uri": {"type": "string"},
        "run_id": {"type": "string"},
        "bytes": {"type": "integer"},
        "sha256": {"type": "string"},
    },
    "phase.begin": {
        "phase_id": {"type": "string"},
        "name": {"type": "string"},
        "status": {"type": "string"},
    },
    "phase.end": {
        "phase_id": {"type": "string"},
        "name": {"type": "string"},
        "status": {"type": "string"},
    },
    "phase.fail": {
        "phase_id": {"type": "string"},
        "name": {"type": "string"},
        "status": {"type": "string"},
    },
    "phase.retry": {
        "phase_id": {"type": "string"},
        "name": {"type": "string"},
        "status": {"type": "string"},
    },
    "phase.skip": {
        "phase_id": {"type": "string"},
        "name": {"type": "string"},
        "status": {"type": "string"},
    },
    "store.backup": {"task": {"type": "object"}},
    "store.check": {
        "ok": {"type": "boolean"},
        "level": {"type": "string"},
        "status": {"type": "object"},
        "checks": {"type": "object"},
    },
    "store.status": {
        "ok": {"type": "boolean"},
        "identity": {"type": "string"},
        "exists": {"type": "boolean"},
        "layout_version": {"type": "integer"},
    },
    "store.restore.apply": {
        "ok": {"type": "boolean"},
        "changed": {"type": "boolean"},
        "restored_from": {"type": "string"},
        "store": {"type": "object"},
    },
    "tool.invoke": {
        "run_id": {"type": "string"},
        "status": {"type": "string"},
        "invocation_id": {"type": "string"},
    },
    "secret.set": {
        "ok": {"type": "boolean"},
        "name": {"type": "string"},
        "environment": {"type": "string"},
        "provider": {"type": "string"},
    },
    "secret.delete": {
        "ok": {"type": "boolean"},
        "name": {"type": "string"},
        "action": {"const": "deleted"},
    },
    "secret.status": {
        "name": {"type": "string"},
        "configured": {"type": "boolean"},
        "available": {"type": "boolean"},
        "status": {"type": "string"},
        "environment": {"type": "string"},
        "provider_error": {"type": ["string", "null"]},
    },
    "task.show": {
        "task_id": {"type": "string"},
        "state": {"type": "string"},
        "operation": {"type": "string"},
    },
    "task.wait": {
        "task_id": {"type": "string"},
        "state": {"type": "string"},
        "operation": {"type": "string"},
    },
    "task.cancel": {
        "task_id": {"type": "string"},
        "state": {"type": "string"},
        "operation": {"type": "string"},
    },
}

_OUTPUT_REQUIRED: dict[str, tuple[str, ...]] = {
    "system.describe": ("operation",),
    "system.version": ("version", "contract_version"),
    "system.context": (
        "project",
        "environment",
        "health_warnings",
        "recent_runs",
        "pending_approvals",
        "safe_next_actions",
    ),
    "project.create": ("project", "project_id", "environment"),
    "project.delete.apply": ("deleted", "recoverable"),
    "project.doctor": ("project", "environment", "checks", "errors"),
    "project.import.verify": ("project", "project_id", "files"),
    "project.rename": ("renamed", "from", "to"),
    "project.show": ("project", "project_id", "environment"),
    "project.paths": ("project", "project_id", "environment"),
    "environment.doctor": ("project", "environment", "checks", "errors"),
    "environment.set": ("environment",),
    "run.start": ("run_id", "object_id", "status", "project", "environment"),
    "run.input.add": ("run_id", "path", "bytes"),
    "run.files": ("run_id", "files", "artifacts"),
    "run.reconcile": ("run_id", "ok", "issues"),
    "artifact.put": ("artifact_id", "run_id", "name", "uri", "sha256", "bytes"),
    "artifact.export": ("artifact_id", "uri", "run_id", "bytes", "sha256"),
    "store.backup": ("task",),
    "store.check": ("ok", "level", "status", "checks"),
    "store.status": ("ok", "identity", "exists", "layout_version"),
    "store.restore.apply": ("ok", "changed", "restored_from", "store"),
    "tool.invoke": ("run_id", "status", "invocation_id"),
    "secret.set": ("name", "environment", "provider"),
    "secret.delete": ("name", "action"),
    "secret.status": ("name", "configured", "available", "status", "environment"),
    "task.show": ("task_id", "state", "operation"),
    "task.wait": ("task_id", "state", "operation"),
    "task.cancel": ("task_id", "state", "operation"),
}


def input_schema(operation_id: str, properties: dict[str, Any]) -> dict[str, Any]:
    """Build one strict input schema with operation-specific required fields."""
    schema: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    required = _REQUIRED_ARGUMENTS.get(operation_id, ())
    if required:
        schema["required"] = list(required)
    alternatives = _REQUIRED_ALTERNATIVES.get(operation_id, ())
    if alternatives:
        schema["anyOf"] = [{"required": list(option)} for option in alternatives]
    return schema


def output_schema(operation_id: str) -> dict[str, Any]:
    """Build one typed result schema for an operation."""
    properties = dict(_PAGE_PROPERTIES) if operation_id in _PAGE_OPERATIONS else {}
    properties.update(_OUTPUT_PROPERTIES.get(operation_id, {}))
    if not properties:
        properties = {"ok": {"type": "boolean"}}
    schema: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    required = _OUTPUT_REQUIRED.get(operation_id, ())
    if required:
        schema["required"] = list(required)
    return schema


__all__ = ["input_schema", "output_schema"]
