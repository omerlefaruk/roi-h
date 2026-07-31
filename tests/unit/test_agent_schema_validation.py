"""JSON Schema validation at the public dispatcher seam."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Literal

from pydantic import create_model

from roi_h.agent.catalog import OperationCatalog, OperationDefinition, build_catalog
from roi_h.agent.contract import CommandRequest, Effect, Idempotency, OperationManifest
from roi_h.agent.dispatcher import Dispatcher
from roi_h.agent.operation_models import InputArguments, OperationResult


def test_public_operation_schemas_describe_required_arguments_and_results() -> None:
    catalog = build_catalog()
    run_start = catalog.describe("run.start")[0]
    assert run_start.input_schema["required"] == ["goal"]
    assert run_start.output_schema["required"] == [
        "run_id",
        "object_id",
        "status",
        "project",
        "environment",
    ]
    assert run_start.output_schema["properties"]["run_id"] == {"type": "string"}

    tool_invoke = catalog.describe("tool.invoke")[0]
    assert tool_invoke.input_schema["properties"]["approval_mode"] == {
        "enum": ["required", "full"],
        "type": "string",
    }

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
    assert fingerprint == "7babfaef7c95217f66861c20f2aa6f46922612ab44238c8cbd2d806440d6e4ae"


def test_dispatcher_rejects_missing_required_operation_arguments() -> None:
    result = Dispatcher().execute(
        "run.start",
        CommandRequest(
            idempotency_key="missing-goal",
            arguments={"home": "agent-schema-home"},
        ),
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "request.invalid"
    assert result.error.details["path"] == "arguments"


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
