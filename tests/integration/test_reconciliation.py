"""Graph/filesystem reconciliation for artifacts and phase handoffs."""

from __future__ import annotations

from pathlib import Path

from roi_h import RunSession
from roi_h.harness.loader import default_skills_root
from roi_h.harness.workspace import Workspace, create_project


def _harness(tmp_path: Path) -> RunSession:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")
    harness = RunSession.create(
        workspace,
        run_id="reconcile-1",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("reconcile graph and files", phase_plan=["collect"])
    return harness


def test_reconcile_dry_run_and_safe_repairs(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    harness.begin_phase("collect")
    source = tmp_path / "report.txt"
    source.write_text("original", encoding="utf-8")
    artifact = harness.put_artifact(source, name="report.txt")
    ended = harness.end_phase(summary={"rows": 1})

    artifact_path = Path(artifact["path"])
    artifact_path.write_text("changed after record", encoding="utf-8")
    orphan_path = artifact_path.parent / "art_deadbeef00--orphan.txt"
    orphan_path.write_text("untracked", encoding="utf-8")
    phase_id = ended["phase_id"]
    harness.runtime.graph.patch_object(phase_id, {"handoff_path": None})

    artifact_obj = harness.runtime.graph.get_object(artifact["object_id"])
    assert artifact_obj is not None
    original_sha = artifact_obj.data["sha256"]

    dry_run = harness.reconcile()
    assert dry_run.repair_requested is False
    assert dry_run.ok is False
    assert dry_run.repairs == 0
    assert {issue.kind for issue in dry_run.issues} >= {
        "artifact_metadata_mismatch",
        "orphan_artifact_file",
        "phase_handoff_path_mismatch",
    }
    unchanged = harness.runtime.graph.get_object(artifact["object_id"])
    assert unchanged is not None
    assert unchanged.data["sha256"] == original_sha
    assert not any(
        item.data.get("name") == "orphan.txt"
        for item in harness.runtime.graph.objects(type="rpa.artifact")
    )

    repaired = harness.reconcile(repair=True)
    assert repaired.ok is True
    assert repaired.repairs >= 3
    refreshed_artifact = harness.runtime.graph.get_object(artifact["object_id"])
    refreshed_phase = harness.runtime.graph.get_object(phase_id)
    assert refreshed_artifact is not None
    assert refreshed_artifact.data["sha256"] != original_sha
    assert refreshed_phase is not None
    assert refreshed_phase.data["handoff_path"] == ended["handoff_uri"]
    assert any(
        item.data.get("name") == "orphan.txt"
        for item in harness.runtime.graph.objects(type="rpa.artifact")
    )

    clean = harness.reconcile()
    assert clean.ok is True
    assert clean.issues == []


def test_reconcile_reports_irreparable_missing_artifact(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    source = tmp_path / "evidence.txt"
    source.write_text("evidence", encoding="utf-8")
    artifact = harness.put_artifact(source, name="evidence.txt")
    Path(artifact["path"]).unlink()

    report = harness.reconcile(repair=True)
    assert report.ok is False
    issue = next(item for item in report.issues if item.kind == "artifact_missing_file")
    assert issue.repaired is False
    assert issue.severity == "error"
