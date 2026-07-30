"""Pydantic validation at the public dispatcher seam."""

from __future__ import annotations

import threading

from roi_h.agent.catalog import OperationCatalog, OperationDefinition, build_catalog
from roi_h.agent.contract import CommandRequest, Effect, Idempotency, OperationManifest
from roi_h.agent.dispatcher import Dispatcher
from roi_h.agent.operation_models import OperationModel, OperationOutput


class _TestInput(OperationModel):
    value: str
    home: str | None = None


class _TestOutput(OperationOutput):
    value: str


def test_public_operation_schemas_come_from_typed_models() -> None:
    catalog = build_catalog()
    definition = catalog.definition("run.start")
    manifest = definition.manifest

    assert manifest.input_schema == definition.input_model.model_json_schema()
    assert manifest.output_schema == definition.output_model.model_json_schema()
    assert manifest.input_schema["required"] == ["goal"]
    assert manifest.output_schema["required"] == [
        "run_id",
        "object_id",
        "status",
        "project",
        "environment",
    ]
    assert manifest.output_schema["properties"]["run_id"]["type"] == "string"

    backup = catalog.describe("store.backup")[0]
    assert backup.execution_mode == "task"
    assert backup.input_schema["required"] == ["output"]
    assert backup.output_schema["required"] == ["task"]


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
    assert result.error.details["path"] == "arguments.goal"


def test_idempotency_claim_blocks_concurrent_effects(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()

    def handler(_request: CommandRequest) -> dict[str, object]:
        started.set()
        assert release.wait(timeout=2)
        return {"value": "ok"}

    catalog = _catalog(handler, idempotency=Idempotency.REQUIRED)
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


def test_dispatcher_rejects_arguments_that_do_not_match_input_model() -> None:
    called = False

    def handler(_request: CommandRequest) -> dict[str, object]:
        nonlocal called
        called = True
        return {"value": "ok"}

    result = Dispatcher(_catalog(handler)).execute(
        "test.schema",
        CommandRequest(arguments={"value": 12, "unknown": True}),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "request.invalid"
    assert result.error.details["path"] == "arguments.unknown"
    assert called is False


def test_dispatcher_rejects_handler_output_that_does_not_match_model() -> None:
    result = Dispatcher(_catalog(lambda _request: {"value": 12})).execute(
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
    idempotency: Idempotency = Idempotency.NOT_APPLICABLE,
) -> OperationCatalog:
    catalog = OperationCatalog()
    catalog.register(
        OperationDefinition(
            manifest=OperationManifest(
                operation_id="test.schema",
                description="Schema validation test.",
                input_schema=_TestInput.model_json_schema(),
                output_schema=_TestOutput.model_json_schema(),
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
            input_model=_TestInput,
            output_model=_TestOutput,
            handler=handler,  # type: ignore[arg-type]
        )
    )
    return catalog
