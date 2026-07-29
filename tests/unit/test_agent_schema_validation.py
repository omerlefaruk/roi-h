"""JSON Schema validation at the public dispatcher seam."""

from __future__ import annotations

from roi_h.agent.catalog import OperationCatalog, OperationDefinition
from roi_h.agent.contract import (
    JSON_SCHEMA_DIALECT,
    CommandRequest,
    Effect,
    Idempotency,
    OperationManifest,
)
from roi_h.agent.dispatcher import Dispatcher


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


def test_dispatcher_rejects_handler_output_that_does_not_match_schema() -> None:
    dispatcher = Dispatcher(
        _catalog(lambda _request: {"value": 12}, output_type="string")
    )
    result = dispatcher.execute(
        "test.schema",
        CommandRequest(arguments={"value": "valid"}),
    )

    assert result.ok is False
    assert result.changed is False
    assert result.error is not None
    assert result.error.code == "operation.contract_violation"
    assert result.error.details["path"] == "result.value"


def _catalog(handler: object, *, output_type: str) -> OperationCatalog:
    catalog = OperationCatalog()
    catalog.register(
        OperationDefinition(
            manifest=OperationManifest(
                operation_id="test.schema",
                description="Schema validation test.",
                input_schema={
                    "$schema": JSON_SCHEMA_DIALECT,
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                output_schema={
                    "$schema": JSON_SCHEMA_DIALECT,
                    "type": "object",
                    "properties": {"value": {"type": output_type}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                effect=Effect.READ,
                idempotency=Idempotency.NOT_APPLICABLE,
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
        )
    )
    return catalog
