"""JSON Schema validation at the public dispatcher seam."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Literal

import pytest
from pydantic import ValidationError, create_model

from roi_h.agent.catalog import OperationCatalog, OperationDefinition, build_catalog
from roi_h.agent.contract import CommandRequest, Effect, Idempotency, OperationManifest
from roi_h.agent.dispatcher import Dispatcher
from roi_h.agent.operation_models import InputArguments, OperationResult
from roi_h.harness.automation_source import put_source, source_tree_digest
from roi_h.harness.workspace import Workspace, create_project


def test_public_operation_schemas_describe_required_arguments_and_results() -> None:
    catalog = build_catalog()
    source_put = catalog.describe("automation.source.put")[0]
    assert source_put.input_schema["required"] == ["name", "manifest", "files"]
    assert "phases" in source_put.input_schema["$defs"]["AutomationSourceManifest"]["properties"]

    dev_run = catalog.describe("automation.dev.run")[0]
    assert dev_run.input_schema["required"] == ["name"]
    assert dev_run.timeout_seconds == 172800
    assert set(dev_run.output_schema["required"]) == {
        "ok",
        "run_id",
        "environment",
        "automation",
        "source_digest",
        "status",
        "verification_ok",
        "phase_states",
        "phases",
    }

    source_output = source_put.output_schema
    assert set(source_output["required"]) == {
        "ok",
        "name",
        "source_digest",
        "files",
        "phase_plan",
    }
    for operation_id in (
        "automation.source.list",
        "automation.source.show",
        "automation.list",
        "automation.show",
        "automation.compare",
    ):
        schema = catalog.describe(operation_id)[0].output_schema
        assert schema.get("additionalProperties") is False
        assert schema.get("required")

    run_input = catalog.describe("run.input.add")[0]
    assert any(
        option["required"] == ["from_run", "source_path"]
        for option in run_input.input_schema["oneOf"]
    )

    project_create = catalog.definition("project.create").input_model
    defaults = project_create.model_validate({"name": "demo"})
    assert defaults.use is True
    assert defaults.log_retention == "7d"
    for invalid in ("0d", "1000000000d"):
        with pytest.raises(ValidationError):
            project_create.model_validate({"name": "demo", "log_retention": invalid})

    backup = catalog.describe("store.backup")[0]
    assert backup.execution_mode == "task"
    assert backup.input_schema["required"] == ["output"]
    assert backup.output_schema["required"] == ["task"]


def test_operation_models_publish_unchanged_contract_1_0_manifests() -> None:
    catalog = build_catalog()
    manifests = catalog.describe()
    for manifest in manifests:
        definition = catalog.definition(manifest.operation_id)
        assert manifest.input_schema == definition.input_model.model_json_schema()
        assert manifest.output_schema == definition.output_model.model_json_schema()
    payload = [manifest.model_dump(mode="json") for manifest in manifests]
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fingerprint == "0539c8a8ce7d8a51a72dbf8a0a7463ac748122ed62895d994da5963003f424ce"


def test_dispatcher_rejects_missing_required_operation_arguments() -> None:
    result = Dispatcher().execute(
        "automation.source.put",
        CommandRequest(
            idempotency_key="missing-source",
            arguments={"home": "agent-schema-home"},
        ),
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "request.invalid"
    assert result.error.details["path"] == "arguments"


def test_public_source_put_rejects_production_without_changing_source(tmp_path) -> None:
    home = tmp_path / "home"
    create_project(home, "demo", set_active=True)
    dev = Workspace.open(home, project="demo", env="dev")
    put_source(
        dev.automation_sources,
        "report",
        {
            "name": "report",
            "phases": [
                {"id": "build", "module": "build"},
                {
                    "id": "verify",
                    "module": "verify",
                    "role": "verify",
                    "needs": ["build"],
                },
            ],
        },
        {
            "build.py": "def run(context):\n    return {'summary': {'ok': True}}\n",
            "verify.py": "def run(context):\n    return {'summary': {'ok': True}}\n",
        },
    )
    before_digest, before_files = source_tree_digest(dev.automation_sources / "report")

    result = Dispatcher().execute(
        "automation.source.put",
        CommandRequest(
            idempotency_key="production-source",
            arguments={
                "home": str(home),
                "project": "demo",
                "environment": "prod",
                "name": "report",
                "manifest": {
                    "name": "report",
                    "phases": [
                        {"id": "build", "module": "build"},
                        {
                            "id": "verify",
                            "module": "verify",
                            "role": "verify",
                            "needs": ["build"],
                        },
                    ],
                },
                "files": {
                    "build.py": "def run(context):\n    return {'summary': {'ok': True}}\n",
                    "verify.py": "def run(context):\n    return {'summary': {'ok': True}}\n",
                },
            },
        ),
    )

    after_digest, after_files = source_tree_digest(dev.automation_sources / "report")
    assert result.ok is False
    assert result.error is not None
    assert result.error.message == "editable automation sources can change only in development"
    assert (after_digest, after_files) == (before_digest, before_files)


def test_idempotency_claim_blocks_concurrent_effects(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()

    def handler(_request: CommandRequest) -> dict[str, object]:
        started.set()
        assert release.wait(timeout=2)
        return {"value": "ok"}

    catalog = _catalog(handler, output_type="string", idempotency=Idempotency.REQUIRED)
    request = CommandRequest(
        idempotency_key="one-effect",
        arguments={"home": str(tmp_path / "home"), "value": "same"},
    )
    first: dict[str, object] = {}

    def execute_first() -> None:
        first["result"] = Dispatcher(catalog).execute("test.schema", request)

    thread = threading.Thread(target=execute_first)
    thread.start()
    assert started.wait(timeout=2)
    second = Dispatcher(catalog).execute("test.schema", request)
    assert second.ok is False
    assert second.error is not None
    assert second.error.code == "request.in_progress"
    assert second.error.retryable is True
    release.set()
    thread.join(timeout=2)
    assert first["result"].ok is True  # type: ignore[union-attr]

    replay = Dispatcher(catalog).execute("test.schema", request)
    assert replay.ok is True
    assert replay.warnings == ["Returned the first result for this idempotency key."]


def test_dispatcher_rejects_arguments_that_do_not_match_input_schema() -> None:
    called = False

    def handler(_request: CommandRequest) -> dict[str, object]:
        nonlocal called
        called = True
        return {"value": "ok"}

    dispatcher = Dispatcher(_catalog(handler, output_type="string"))
    result = dispatcher.execute(
        "test.schema",
        CommandRequest(arguments={"value": 12, "unknown": True}),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "request.invalid"
    assert result.error.details["path"] == "arguments"
    assert called is False


def test_dispatcher_does_not_coerce_operation_arguments() -> None:
    called = False

    def handler(_request: CommandRequest) -> dict[str, object]:
        nonlocal called
        called = True
        return {"value": "ok"}

    result = Dispatcher(_catalog(handler, output_type="string")).execute(
        "test.schema",
        CommandRequest(arguments={"value": 12}),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.details == {"path": "arguments.value", "validator": "type"}
    assert called is False


def test_dispatcher_reports_const_for_one_value_literal_failures() -> None:
    dispatcher = Dispatcher(_catalog(lambda _request: {"value": "other"}, output_type="const"))
    result = dispatcher.execute(
        "test.schema",
        CommandRequest(arguments={"value": "valid"}),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.details == {"path": "result.value", "validator": "const"}


def test_dispatcher_rejects_handler_output_that_does_not_match_schema() -> None:
    dispatcher = Dispatcher(_catalog(lambda _request: {"value": 12}, output_type="string"))
    result = dispatcher.execute(
        "test.schema",
        CommandRequest(arguments={"value": "valid"}),
    )

    assert result.ok is False
    assert result.changed is False
    assert result.error is not None
    assert result.error.code == "operation.contract_violation"
    assert result.error.details["path"] == "result.value"


def _catalog(
    handler: object,
    *,
    output_type: str,
    idempotency: Idempotency = Idempotency.NOT_APPLICABLE,
) -> OperationCatalog:
    input_model = create_model(
        "TestSchemaInput",
        __base__=InputArguments,
        value=(str, ...),
        home=(str, ""),
    )
    output_annotation = (
        str if output_type == "string" else Literal["deleted"] if output_type == "const" else int
    )
    output_model = create_model(
        "TestSchemaOutput",
        __base__=OperationResult,
        value=(output_annotation, ...),
    )
    catalog = OperationCatalog()
    catalog.register(
        OperationDefinition(
            manifest=OperationManifest(
                operation_id="test.schema",
                description="Schema validation test.",
                input_schema=input_model.model_json_schema(),
                output_schema=output_model.model_json_schema(),
                effect=Effect.READ,
                idempotency=idempotency,
                approval_rule="none",
                plan_rule="none",
                secret_input_paths=[],
                filesystem_requirements=[],
                network_requirements=[],
                pagination=False,
                execution_mode="sync",
                timeout_seconds=10,
            ),
            handler=handler,  # type: ignore[arg-type]
            input_model=input_model,
            output_model=output_model,
        )
    )
    return catalog
