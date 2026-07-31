"""Pydantic validation and JSON Schema sources for catalog operations."""

from __future__ import annotations

from functools import cache
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode

from roi_h.agent.contract import JSON_SCHEMA_DIALECT

type OperationModel = type[BaseModel]

_NULLABLE_BRANCHES = 2


class _ContractSchemaModel(BaseModel):
    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,  # noqa: FBT001, FBT002 - Pydantic interface.
        ref_template: str = "#/$defs/{model}",
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: JsonSchemaMode = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, Any]:
        schema = super().model_json_schema(
            by_alias,
            ref_template,
            schema_generator,
            mode,
            union_format=union_format,
        )
        return cast("dict[str, Any]", _normalize_schema(schema, root=True))


class InputArguments(_ContractSchemaModel):
    """Strict caller-supplied operation arguments."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={"$schema": JSON_SCHEMA_DIALECT},
    )


class CommonArguments(InputArguments):
    """Arguments accepted by the general operation family."""

    home: str | None = None
    db: str | None = None
    project: str | None = None
    environment: Literal["dev", "prod"] | None = None
    run_id: str | None = None
    name: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None
    skills: str | None = None
    version: str | None = None
    version_a: str | None = None
    version_b: str | None = None
    approval_id: str | None = None
    artifact_id: str | None = None
    plan_id: str | None = None
    full: bool = False
    arguments: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    source_path: str | None = None
    output: str | None = None
    mode: str | None = None
    new_name: str | None = None
    reason: str | None = None
    by: str | None = None
    repair: bool = False
    description: str | None = None
    summary: dict[str, Any] | None = None
    error: str | None = None
    require_artifacts: list[str] | None = None
    skill: str | None = None
    tool: str | None = None
    overwrite: bool = False
    from_run: str | None = None
    from_handoff: str | None = None
    goal: str | None = None
    notes: str | None = None
    skills_list: list[str] | None = None
    distill: bool = False
    dry_run: bool = False
    auto_approve: bool | None = None
    force: bool = False
    actor: str | None = None
    set_args: list[str] | None = None
    secret_value: str | None = None
    policy: dict[str, Any] | None = None
    target: str | None = None
    use: bool = False


class OperationResult(_ContractSchemaModel):
    """Handler result that permits contract-compatible extension fields."""

    model_config = ConfigDict(
        extra="allow",
        strict=True,
        json_schema_extra={"$schema": JSON_SCHEMA_DIALECT},
    )


class _EmptyInput(InputArguments):
    pass


class _SystemDescribeInput(InputArguments):
    operation: str | None = None


class _RunReadInput(InputArguments):
    home: str | None = None
    db: str | None = None
    run_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None
    after: str | None = None


class _SystemContextInput(InputArguments):
    home: str | None = None
    db: str | None = None
    project: str | None = None
    environment: Literal["dev", "prod"] | None = None


class _ProjectListInput(InputArguments):
    home: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None


class _ApprovalIdInput(CommonArguments):
    approval_id: str | None


class _ApprovalIdWithDisplayInput(_ApprovalIdInput):
    display_name: str | None = None


class _ArtifactExportInput(CommonArguments):
    artifact_id: str | None
    output: str | None


class _ArtifactPutInput(CommonArguments):
    source: str | None


class _AutomationCompareInput(CommonArguments):
    name: str | None
    version_a: str | None
    version_b: str | None


class _AutomationShipInput(CommonArguments):
    name: str | None
    version: str | None
    from_run: str | None


class _EnvironmentSetInput(CommonArguments):
    environment: Literal["dev", "prod"] | None


class _ErrorInput(CommonArguments):
    error: str | None


class _NameInput(CommonArguments):
    name: str | None


class _NameAndNewNameInput(CommonArguments):
    name: str | None
    new_name: str | None


class _OutputInput(CommonArguments):
    output: str | None


class _PlanIdInput(CommonArguments):
    plan_id: str | None


class _ProjectImportInput(CommonArguments):
    model_config = ConfigDict(
        json_schema_extra={
            "$schema": JSON_SCHEMA_DIALECT,
            "anyOf": [{"required": ["source"]}, {"required": ["plan_id"]}],
        }
    )

    @model_validator(mode="after")
    def _require_source_or_plan(self) -> _ProjectImportInput:
        if not self.model_fields_set.intersection({"source", "plan_id"}):
            msg = "source or plan_id is required"
            raise ValueError(msg)
        return self


class _RunInputAddInput(CommonArguments):
    model_config = ConfigDict(
        json_schema_extra={
            "$schema": JSON_SCHEMA_DIALECT,
            "required": ["name"],
            "oneOf": [
                {
                    "required": ["source"],
                    "properties": {"source": {"type": "string", "minLength": 1}},
                    "not": {
                        "anyOf": [
                            {"required": ["from_run"]},
                            {"required": ["source_path"]},
                        ]
                    },
                },
                {
                    "required": ["from_run", "source_path"],
                    "properties": {
                        "from_run": {"type": "string", "minLength": 1},
                        "source_path": {"type": "string", "minLength": 1},
                    },
                    "not": {"required": ["source"]},
                },
            ],
        }
    )

    source: str | None = None
    from_run: str | None = None
    source_path: str | None = None
    name: str | None

    @model_validator(mode="after")
    def _require_one_source(self) -> _RunInputAddInput:
        supplied = self.model_fields_set & {"source", "from_run", "source_path"}
        external = supplied == {"source"} and bool(self.source)
        prior_run = supplied == {"from_run", "source_path"} and bool(
            self.from_run and self.source_path
        )
        if not external and not prior_run:
            msg = "provide source or both from_run and source_path"
            raise ValueError(msg)
        return self


class _SkillDefineInput(CommonArguments):
    skill: str | None
    tool: str | None


class _TaskInput(CommonArguments):
    task_id: str | None
    after: str | None = None
    timeout_seconds: float = Field(default=0, ge=0)


class _GoalInput(CommonArguments):
    goal: str | None
    display_name: str | None = None


class _NameWithDisplayInput(_NameInput):
    display_name: str | None = None


class _ProjectCreateInput(_NameWithDisplayInput):
    use: bool = True
    log_retention: str = Field(default="7d", pattern=r"^(?:[1-9][0-9]{0,8}d|forever)$")


class _OutputWithDisplayInput(_OutputInput):
    display_name: str | None = None


class _PlanIdWithDisplayInput(_PlanIdInput):
    display_name: str | None = None


class _TaskListInput(CommonArguments):
    task_id: str | None = None
    after: str | None = None
    timeout_seconds: float = Field(default=0, ge=0)


class _ToolInvokeInput(_NameWithDisplayInput):
    approval_mode: Literal["required", "full"] = "required"
    filesystem_grants: list[
        Literal[
            "project:reference:read",
            "run:input:read",
            "run:work:read-write",
            "run:output:read-write",
            "run:tmp:read-write",
            "artifact:read",
            "automation:read",
        ]
    ] = Field(default_factory=list)


class _OkOutput(OperationResult):
    ok: bool = False


class _PageOutput(OperationResult):
    items: list[Any] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    snapshot: str = ""


class _SystemDescribeOutput(OperationResult):
    operation: str


class _SystemVersionOutput(OperationResult):
    version: str
    contract_version: str


class _SystemContextOutput(OperationResult):
    project: str | None
    environment: str | None
    health_warnings: list[str]
    recent_runs: list[Any]
    pending_approvals: list[Any]
    safe_next_actions: list[Any]


class _ProjectCreateOutput(_OkOutput):
    project: str
    project_id: str
    environment: str


class _ProjectDeleteApplyOutput(_OkOutput):
    deleted: str
    recoverable: bool


class _DoctorOutput(OperationResult):
    project: str
    environment: str
    checks: dict[str, Any]
    errors: list[str]


class _ProjectImportOutput(_OkOutput):
    project: str = ""
    project_id: str = ""
    files: int = 0
    plan_id: str = ""
    operation: str = ""
    effects: list[Any] = Field(default_factory=list)
    state_digest: str = ""
    expires_at: str = ""
    apply_operation: str = ""


class _ProjectImportVerifyOutput(_OkOutput):
    project: str
    project_id: str
    files: int


class _ProjectRenameOutput(_OkOutput):
    renamed: bool
    from_: str = Field(alias="from")
    to: str


class _ProjectShowOutput(OperationResult):
    project: str
    project_id: str
    environment: str


class _EnvironmentSetOutput(OperationResult):
    env: str = ""
    environment: str


class _RunStartOutput(OperationResult):
    run_id: str
    object_id: str
    status: str
    project: str
    environment: str


class _RunInputAddOutput(OperationResult):
    run_id: str
    path: str
    bytes: int
    source_run_id: str | None = None
    source_path: str | None = None


class _RunFilesOutput(OperationResult):
    run_id: str
    files: list[Any]
    artifacts: list[Any]


class _RunReconcileOutput(OperationResult):
    run_id: str
    ok: bool
    issues: list[Any]


class _ArtifactPutOutput(OperationResult):
    artifact_id: str
    run_id: str
    name: str
    uri: str
    sha256: str
    bytes: int


class _ArtifactExportOutput(OperationResult):
    artifact_id: str
    uri: str
    run_id: str
    bytes: int
    sha256: str


class _PhaseOutput(OperationResult):
    phase_id: str = ""
    name: str = ""
    status: str = ""


class _StoreBackupOutput(OperationResult):
    task: dict[str, Any]


class _StoreCheckOutput(OperationResult):
    ok: bool
    level: str
    status: dict[str, Any]
    checks: dict[str, Any]


class _StoreStatusOutput(OperationResult):
    ok: bool
    identity: str
    exists: bool
    layout_version: int


class _StoreRestoreApplyOutput(OperationResult):
    ok: bool
    changed: bool
    restored_from: str
    store: dict[str, Any]


class _ToolInvokeOutput(OperationResult):
    run_id: str
    status: str
    invocation_id: str


class _SecretSetOutput(_OkOutput):
    name: str
    environment: str
    provider: str


class _SecretDeleteOutput(_OkOutput):
    name: str
    action: Literal["deleted"]


class _SecretStatusOutput(OperationResult):
    name: str
    configured: bool
    available: bool
    status: str
    environment: str
    provider_error: str | None = None


class _TaskOutput(OperationResult):
    task_id: str
    state: str
    operation: str


_INPUT_MODELS: dict[str, OperationModel] = {
    "approval.approve": _ApprovalIdWithDisplayInput,
    "approval.reject": _ApprovalIdWithDisplayInput,
    "approval.show": _ApprovalIdInput,
    "artifact.export": _ArtifactExportInput,
    "artifact.put": _ArtifactPutInput,
    "automation.compare": _AutomationCompareInput,
    "automation.run": _NameInput,
    "automation.ship": _AutomationShipInput,
    "automation.show": _NameInput,
    "automation.verify": _NameInput,
    "environment.set": _EnvironmentSetInput,
    "phase.begin": _NameInput,
    "phase.fail": _ErrorInput,
    "phase.retry": _NameInput,
    "phase.skip": _NameInput,
    "project.create": _ProjectCreateInput,
    "project.delete.apply": _PlanIdWithDisplayInput,
    "project.delete.plan": _NameWithDisplayInput,
    "project.export": _OutputInput,
    "project.import": _ProjectImportInput,
    "project.import.verify": _ArtifactPutInput,
    "project.rename": _NameAndNewNameInput,
    "project.use": _NameInput,
    "retention.apply": _PlanIdInput,
    "retention.show": _PlanIdInput,
    "run.input.add": _RunInputAddInput,
    "run.reconcile": CommonArguments,
    "run.start": _GoalInput,
    "secret.delete": _NameInput,
    "secret.set": _NameInput,
    "secret.status": _NameInput,
    "skill.define": _SkillDefineInput,
    "skill.delete.apply": _PlanIdInput,
    "skill.delete.plan": _NameInput,
    "skill.promote": _NameInput,
    "skill.show": _NameInput,
    "skill.validate": _NameInput,
    "store.backup": _OutputWithDisplayInput,
    "store.restore.apply": _PlanIdInput,
    "store.restore.plan": _ArtifactPutInput,
    "support_bundle.create": _OutputInput,
    "task.cancel": _TaskInput,
    "task.events": _TaskInput,
    "task.list": _TaskListInput,
    "task.show": _TaskInput,
    "task.wait": _TaskInput,
    "tool.invoke": _ToolInvokeInput,
}

_OUTPUT_MODELS: dict[str, OperationModel] = {
    "system.describe": _SystemDescribeOutput,
    "system.version": _SystemVersionOutput,
    "system.context": _SystemContextOutput,
    "project.create": _ProjectCreateOutput,
    "project.delete.apply": _ProjectDeleteApplyOutput,
    "project.doctor": _DoctorOutput,
    "project.import": _ProjectImportOutput,
    "project.import.verify": _ProjectImportVerifyOutput,
    "project.rename": _ProjectRenameOutput,
    "project.show": _ProjectShowOutput,
    "project.paths": _ProjectShowOutput,
    "environment.doctor": _DoctorOutput,
    "environment.set": _EnvironmentSetOutput,
    "run.start": _RunStartOutput,
    "run.input.add": _RunInputAddOutput,
    "run.files": _RunFilesOutput,
    "run.reconcile": _RunReconcileOutput,
    "artifact.put": _ArtifactPutOutput,
    "artifact.export": _ArtifactExportOutput,
    "phase.begin": _PhaseOutput,
    "phase.end": _PhaseOutput,
    "phase.fail": _PhaseOutput,
    "phase.retry": _PhaseOutput,
    "phase.skip": _PhaseOutput,
    "store.backup": _StoreBackupOutput,
    "store.check": _StoreCheckOutput,
    "store.status": _StoreStatusOutput,
    "store.restore.apply": _StoreRestoreApplyOutput,
    "tool.invoke": _ToolInvokeOutput,
    "secret.list": _OkOutput,
    "secret.set": _SecretSetOutput,
    "secret.delete": _SecretDeleteOutput,
    "secret.status": _SecretStatusOutput,
    "task.show": _TaskOutput,
    "task.wait": _TaskOutput,
    "task.cancel": _TaskOutput,
}

_SPECIAL_INPUT_MODELS: dict[str, OperationModel] = {
    "system.describe": _SystemDescribeInput,
    "system.context": _SystemContextInput,
    "system.version": _EmptyInput,
    "project.list": _ProjectListInput,
    "run.list": _RunReadInput,
    "run.show": _RunReadInput,
    "run.status": _RunReadInput,
    "run.events": _RunReadInput,
    "run.trace": _RunReadInput,
}


@cache
def input_model(operation_id: str) -> OperationModel:
    """Return one operation-specific input model."""
    base = _SPECIAL_INPUT_MODELS.get(
        operation_id,
        _INPUT_MODELS.get(operation_id, CommonArguments),
    )
    return _operation_model(operation_id, "Input", base)


@cache
def output_model(operation_id: str, *, pagination: bool = False) -> OperationModel:
    """Return one operation-specific output model."""
    base = _OUTPUT_MODELS.get(operation_id, _PageOutput if pagination else _OkOutput)
    return _operation_model(operation_id, "Output", base)


def _operation_model(operation_id: str, suffix: str, base: OperationModel) -> OperationModel:
    name = "".join(part.title() for part in operation_id.replace(".", "_").split("_")) + suffix
    return cast("OperationModel", create_model(name, __base__=base))


def _normalize_schema(value: object, *, root: bool = False) -> object:
    if isinstance(value, list):
        return [_normalize_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {
        key: _normalize_schema(item)
        for key, item in value.items()
        if key not in {"default", "title"}
        and not (key == "additionalProperties" and item is True and not root)
        and not (key == "type" and "const" in value)
    }
    alternatives = normalized.get("anyOf")
    if isinstance(alternatives, list) and len(alternatives) == _NULLABLE_BRANCHES:
        non_null = next((item for item in alternatives if item != {"type": "null"}), None)
        if {str(item.get("type")) for item in alternatives if isinstance(item, dict)} & {"null"}:
            if isinstance(non_null, dict) and "enum" in non_null:
                return {"enum": [*non_null["enum"], None]}
            if isinstance(non_null, dict) and "type" in non_null:
                return {**non_null, "type": [non_null["type"], "null"]}
    return normalized


__all__ = [
    "CommonArguments",
    "InputArguments",
    "OperationModel",
    "OperationResult",
    "input_model",
    "output_model",
]
