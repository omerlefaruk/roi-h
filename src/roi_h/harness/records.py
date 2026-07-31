"""Typed ActiveGraph payloads used by modular automation runs."""

from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel, ConfigDict

from roi_h.harness.activegraph_runtime import ROIHRuntime
from roi_h.harness.run_storage import ArtifactAttachment, RunStorage
from roi_h.harness.workspace import Workspace


class ArtifactRecord(BaseModel):
    """Portable payload for one durable phase artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    run_id: str
    name: str
    uri: str
    bytes: int
    sha256: str
    media_type: str = "application/octet-stream"
    source: str = ""
    created_at: str | None = None
    phase: str
    phase_id: str

    def to_graph(self) -> dict[str, object]:
        """Return an ActiveGraph object payload."""
        return self.model_dump(mode="json")


def evidenced_artifacts(workspace: Workspace, run_id: str) -> list[ArtifactAttachment]:
    """Load artifact authority from ActiveGraph and verify each durable file."""
    try:
        runtime = ROIHRuntime.load(str(workspace.db), run_id=run_id)
    except Exception as exc:
        msg = f"run evidence not found: {run_id}"
        raise FileNotFoundError(msg) from exc
    records = [
        ArtifactRecord.model_validate(obj.data)
        for obj in runtime.graph.objects(type="rpa.artifact")
    ]
    stored = RunStorage(workspace).list(run_id)
    by_id = {item.artifact_id: item for item in stored}
    if len(by_id) != len(stored):
        msg = f"artifact storage has duplicate identities for run {run_id}"
        raise ValueError(msg)
    verified: list[ArtifactAttachment] = []
    seen: set[str] = set()
    for record in records:
        if record.run_id != run_id or record.uri != f"artifact://{record.artifact_id}":
            msg = f"artifact evidence identity mismatch: {record.artifact_id}"
            raise ValueError(msg)
        if record.artifact_id in seen:
            msg = f"ActiveGraph has duplicate artifact evidence: {record.artifact_id}"
            raise ValueError(msg)
        seen.add(record.artifact_id)
        attachment = by_id.get(record.artifact_id)
        if attachment is None:
            msg = f"artifact.file_missing: {record.artifact_id}"
            raise FileNotFoundError(msg)
        if (
            attachment.name != record.name
            or attachment.sha256 != record.sha256
            or attachment.bytes != record.bytes
        ):
            msg = f"artifact evidence mismatch: {record.artifact_id}"
            raise ValueError(msg)
        verified.append(
            replace(
                attachment,
                media_type=record.media_type,
                source=record.source,
                created_at=record.created_at or attachment.created_at,
            )
        )
    return verified


__all__ = ["ArtifactRecord", "evidenced_artifacts"]
