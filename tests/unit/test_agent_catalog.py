"""Operation catalog and dispatcher seam."""

from __future__ import annotations

from pathlib import Path

import pytest

from roi_h.agent.catalog import OperationCatalog, OperationDefinition, build_catalog
from roi_h.agent.contract import (
    CommandRequest,
    Effect,
    Idempotency,
    OperationManifest,
)


def _definition(operation_id: str) -> OperationDefinition:
    return OperationDefinition(
        manifest=OperationManifest(
            operation_id=operation_id,
            description="Test operation.",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
            },
            output_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
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
        handler=lambda _request: {"value": "ok"},
    )


def test_catalog_rejects_duplicate_operation_ids() -> None:
    catalog = OperationCatalog()
    catalog.register(_definition("system.test"))
    with pytest.raises(ValueError, match="duplicate operation ID"):
        catalog.register(_definition("system.test"))


def test_default_catalog_describes_and_executes_read_operation(tmp_path: Path) -> None:
    catalog = build_catalog()
    manifests = catalog.describe()
    assert "system.version" in {item.operation_id for item in manifests}
    assert all("$schema" in item.input_schema for item in manifests)
    assert all("$schema" in item.output_schema for item in manifests)

    result = catalog.execute(
        "project.list",
        CommandRequest(
            request_id="req_catalog",
            arguments={"home": str(tmp_path / "home")},
        ),
    )
    assert result == {"items": [], "count": 0}
