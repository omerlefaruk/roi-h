"""Versioned public contract for external AI callers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from roi_h.agent.contract import (
    CONTRACT_VERSION,
    CommandRequest,
    CommandResult,
    Effect,
    Idempotency,
    OperationManifest,
    StructuredError,
)


def test_contract_models_are_strict_and_publish_json_schema_fixtures() -> None:
    request = CommandRequest.model_validate(
        {
            "schema_version": "1.0",
            "request_id": "req_test",
            "context": {"project": "demo", "environment": "dev"},
            "arguments": {"limit": 10},
        }
    )
    assert request.schema_version == CONTRACT_VERSION

    with pytest.raises(ValidationError):
        CommandRequest.model_validate(
            {
                "schema_version": "1.0",
                "arguments": {},
                "unknown": True,
            }
        )

    failure = CommandResult(
        operation="project.show",
        request_id="req_test",
        ok=False,
        changed=False,
        error=StructuredError(
            code="project.not_found",
            category="not_found",
            message="The selected project does not exist.",
            retryable=False,
        ),
    )
    assert failure.error is not None
    assert failure.error.code == "project.not_found"

    manifest = OperationManifest(
        operation_id="project.show",
        description="Show one project.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
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
    )
    assert manifest.model_dump(mode="json")["effect"] == "read"

    fixture_root = Path(__file__).parents[1] / "fixtures" / "agent_contract" / "1.0"
    expected = {
        "command-request.schema.json": CommandRequest.model_json_schema(),
        "command-result.schema.json": CommandResult.model_json_schema(),
        "operation-manifest.schema.json": OperationManifest.model_json_schema(),
    }
    for name, schema in expected.items():
        assert json.loads((fixture_root / name).read_text(encoding="utf-8")) == schema
