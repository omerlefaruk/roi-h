"""Operation-specific Pydantic models for contract validation and JSON Schema."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from roi_h.agent.contract import JSON_SCHEMA_DIALECT


class OperationModel(BaseModel):
    """Strict caller-supplied operation arguments."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={"$schema": JSON_SCHEMA_DIALECT},
    )


class OperationOutput(BaseModel):
    """Compatible operation output that permits additive minor-version fields."""

    model_config = ConfigDict(
        extra="allow",
        strict=True,
        json_schema_extra={"$schema": JSON_SCHEMA_DIALECT},
    )


class EmptyArguments(OperationModel):
    pass


class CommonArguments(OperationModel):
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
    display_name: str | None = None
    task_id: str | None = None
    after: str | None = None
    timeout_seconds: float = Field(default=0, ge=0)
    phase_plan: list[str | dict[str, Any]] | None = None


class SystemDescribeArguments(OperationModel):
    operation: str | None = None


class ContextArguments(OperationModel):
    home: str | None = None
    db: str | None = None
    project: str | None = None
    environment: Literal["dev", "prod"] | None = None


class ProjectListArguments(OperationModel):
    home: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None


class RunReadArguments(OperationModel):
    home: str | None = None
    db: str | None = None
    run_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None
    after: str | None = None


class ApprovalArguments(CommonArguments):
    approval_id: str


class ArtifactExportArguments(CommonArguments):
    artifact_id: str
    output: str


class SourceArguments(CommonArguments):
    source: str


class AutomationCompareArguments(CommonArguments):
    name: str
    version_a: str
    version_b: str


class NameArguments(CommonArguments):
    name: str


class AutomationShipArguments(CommonArguments):
    name: str
    version: str
    from_run: str


class EnvironmentSetArguments(CommonArguments):
    environment: Literal["dev", "prod"]


class ErrorArguments(CommonArguments):
    error: str


class ProjectRenameArguments(CommonArguments):
    name: str
    new_name: str


class PlanArguments(CommonArguments):
    plan_id: str


class ProjectExportArguments(CommonArguments):
    output: str


class ProjectImportArguments(CommonArguments):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "$schema": JSON_SCHEMA_DIALECT,
            "anyOf": [{"required": ["source"]}, {"required": ["plan_id"]}],
        },
    )

    @model_validator(mode="after")
    def require_source_or_plan(self) -> ProjectImportArguments:
        if not self.source and not self.plan_id:
            msg = "source or plan_id is required"
            raise ValueError(msg)
        return self


class RunInputArguments(CommonArguments):
    source: str
    name: str


class RunStartArguments(CommonArguments):
    goal: str


class SkillDefineArguments(CommonArguments):
    skill: str
    tool: str


class TaskArguments(CommonArguments):
    task_id: str


class ToolInvokeArguments(CommonArguments):
    name: str


class GenericOutput(OperationOutput):
    """Output for operations whose compatible fields remain domain-specific."""

    ok: bool = False


class PageOutput(OperationOutput):
    items: list[Any] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    snapshot: str = ""


class SystemDescribeOutput(OperationOutput):
    operation: str


class SystemVersionOutput(OperationOutput):
    version: str
    contract_version: str


class SystemContextOutput(OperationOutput):
    project: str | None
    environment: str | None
    health_warnings: list[str]
    recent_runs: list[Any]
    pending_approvals: list[Any]
    safe_next_actions: list[Any]


class ProjectIdentityOutput(OperationOutput):
    project: str
    project_id: str
    environment: str


class ProjectCreateOutput(ProjectIdentityOutput):
    ok: bool = False


class ProjectDeleteOutput(OperationOutput):
    deleted: str
    recoverable: bool
    ok: bool = False


class DoctorOutput(OperationOutput):
    project: str
    environment: str
    checks: dict[str, Any]
    errors: list[str]


class ProjectImportOutput(OperationOutput):
    ok: bool = False
    project: str = ""
    project_id: str = ""
    files: int = 0
    plan_id: str = ""
    operation: str = ""
    effects: list[Any] = Field(default_factory=list)
    state_digest: str = ""
    expires_at: str = ""
    apply_operation: str = ""


class ProjectImportVerifyOutput(OperationOutput):
    project: str
    project_id: str
    files: int
    ok: bool = False


class ProjectRenameOutput(OperationOutput):
    renamed: bool
    from_: str = Field(alias="from")
    to: str
    ok: bool = False


class EnvironmentSetOutput(OperationOutput):
    environment: str
    env: str = ""


class PhaseOutput(OperationOutput):
    phase_id: str = ""
    name: str = ""
    status: str = ""


class RunStartOutput(OperationOutput):
    run_id: str
    object_id: str
    status: str
    project: str
    environment: str


class RunInputOutput(OperationOutput):
    run_id: str
    path: str
    bytes: int


class RunFilesOutput(OperationOutput):
    run_id: str
    files: list[Any]
    artifacts: list[Any]


class RunReconcileOutput(OperationOutput):
    run_id: str
    ok: bool
    issues: list[Any]


class ArtifactPutOutput(OperationOutput):
    artifact_id: str
    run_id: str
    name: str
    uri: str
    sha256: str
    bytes: int


class ArtifactExportOutput(OperationOutput):
    artifact_id: str
    uri: str
    run_id: str
    bytes: int
    sha256: str


class StoreBackupOutput(OperationOutput):
    task: dict[str, Any]


class StoreCheckOutput(OperationOutput):
    ok: bool
    level: str
    status: dict[str, Any]
    checks: dict[str, Any]


class StoreStatusOutput(OperationOutput):
    ok: bool
    identity: str
    exists: bool
    layout_version: int


class StoreRestoreOutput(OperationOutput):
    ok: bool
    changed: bool
    restored_from: str
    store: dict[str, Any]


class ToolInvokeOutput(OperationOutput):
    run_id: str
    status: str
    invocation_id: str


class SecretSetOutput(OperationOutput):
    name: str
    environment: str
    provider: str
    ok: bool = False


class SecretDeleteOutput(OperationOutput):
    name: str
    action: Literal["deleted"]
    ok: bool = False


class SecretStatusOutput(OperationOutput):
    name: str
    configured: bool
    available: bool
    status: str
    environment: str
    provider_error: str | None = None


class TaskOutput(OperationOutput):
    task_id: str
    state: str
    operation: str


_PAGED = {
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

_INPUT_MODELS: dict[str, type[OperationModel]] = {
    "system.describe": SystemDescribeArguments,
    "system.version": EmptyArguments,
    "system.context": ContextArguments,
    "project.list": ProjectListArguments,
    **dict.fromkeys(
        ("run.list", "run.show", "run.status", "run.events", "run.trace"),
        RunReadArguments,
    ),
    **dict.fromkeys(
        ("approval.approve", "approval.reject", "approval.show"),
        ApprovalArguments,
    ),
    "artifact.export": ArtifactExportArguments,
    "artifact.put": SourceArguments,
    "automation.compare": AutomationCompareArguments,
    "automation.run": NameArguments,
    "automation.ship": AutomationShipArguments,
    "automation.show": NameArguments,
    "automation.verify": NameArguments,
    "environment.set": EnvironmentSetArguments,
    "phase.begin": NameArguments,
    "phase.fail": ErrorArguments,
    "phase.retry": NameArguments,
    "phase.skip": NameArguments,
    "project.create": NameArguments,
    "project.delete.apply": PlanArguments,
    "project.delete.plan": NameArguments,
    "project.export": ProjectExportArguments,
    "project.import": ProjectImportArguments,
    "project.import.verify": SourceArguments,
    "project.rename": ProjectRenameArguments,
    "project.use": NameArguments,
    "retention.apply": PlanArguments,
    "retention.show": PlanArguments,
    "run.input.add": RunInputArguments,
    "run.start": RunStartArguments,
    "secret.delete": NameArguments,
    "secret.set": NameArguments,
    "secret.status": NameArguments,
    "skill.define": SkillDefineArguments,
    "skill.delete.apply": PlanArguments,
    "skill.delete.plan": NameArguments,
    "skill.promote": NameArguments,
    "skill.show": NameArguments,
    "skill.validate": NameArguments,
    "store.backup": ProjectExportArguments,
    "store.restore.apply": PlanArguments,
    "store.restore.plan": SourceArguments,
    "support_bundle.create": ProjectExportArguments,
    **dict.fromkeys(
        ("task.cancel", "task.events", "task.show", "task.wait"),
        TaskArguments,
    ),
    "tool.invoke": ToolInvokeArguments,
}

_OUTPUT_MODELS: dict[str, type[OperationOutput]] = {
    **dict.fromkeys(_PAGED, PageOutput),
    "system.describe": SystemDescribeOutput,
    "system.version": SystemVersionOutput,
    "system.context": SystemContextOutput,
    "project.create": ProjectCreateOutput,
    "project.delete.apply": ProjectDeleteOutput,
    "project.doctor": DoctorOutput,
    "project.import": ProjectImportOutput,
    "project.import.verify": ProjectImportVerifyOutput,
    "project.rename": ProjectRenameOutput,
    "project.show": ProjectIdentityOutput,
    "project.paths": ProjectIdentityOutput,
    "environment.doctor": DoctorOutput,
    "environment.set": EnvironmentSetOutput,
    **dict.fromkeys(
        ("phase.begin", "phase.end", "phase.fail", "phase.retry", "phase.skip"),
        PhaseOutput,
    ),
    "run.start": RunStartOutput,
    "run.input.add": RunInputOutput,
    "run.files": RunFilesOutput,
    "run.reconcile": RunReconcileOutput,
    "artifact.put": ArtifactPutOutput,
    "artifact.export": ArtifactExportOutput,
    "store.backup": StoreBackupOutput,
    "store.check": StoreCheckOutput,
    "store.status": StoreStatusOutput,
    "store.restore.apply": StoreRestoreOutput,
    "tool.invoke": ToolInvokeOutput,
    "secret.set": SecretSetOutput,
    "secret.delete": SecretDeleteOutput,
    "secret.status": SecretStatusOutput,
    **dict.fromkeys(("task.show", "task.wait", "task.cancel"), TaskOutput),
}


def operation_models(
    operation_id: str,
) -> tuple[type[OperationModel], type[OperationOutput]]:
    """Return the validation models for one public operation."""
    return (
        _INPUT_MODELS.get(operation_id, CommonArguments),
        _OUTPUT_MODELS.get(operation_id, GenericOutput),
    )


__all__ = [
    "GenericOutput",
    "OperationModel",
    "OperationOutput",
    "operation_models",
]
