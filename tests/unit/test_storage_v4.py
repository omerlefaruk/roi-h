"""Version-4 layout, logical paths, store lifecycle, and archive portability."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from roi_h import RunSession
from roi_h.harness.lease import project_policy_lease, run_lease
from roi_h.harness.loader import default_skills_root
from roi_h.harness.logical_paths import (
    LogicalPath,
    LogicalPathError,
    PathCapabilityError,
    PathResolver,
    PathScope,
    materialize_tool_payload,
)
from roi_h.harness.project_archive import ProjectArchive
from roi_h.harness.retention import RetentionPlanner
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.secrets import get_secret, list_secrets, set_secret
from roi_h.harness.store_lifecycle import StoreLifecycle
from roi_h.harness.workspace import Workspace, configure_project, create_project


def _workspace(tmp_path: Path, *, project: str = "demo") -> Workspace:
    home = tmp_path / project
    create_project(home, project)
    return Workspace.open(home, project=project)


@pytest.mark.parametrize(
    "value",
    [
        "run://work/../escape.txt",
        "run://output/C:\\escape.txt",
        "project://skills/tool.py",
        "artifact://bad",
        "run://work/e\u0301.txt",
    ],
)
def test_logical_path_rejects_nonportable_values(value: str) -> None:
    with pytest.raises(LogicalPathError):
        LogicalPath.parse(value)


def test_path_resolver_and_capability_enforcement(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    RunStorage(workspace).prepare("run1")
    scope = PathScope(workspace, run_id="run1")

    resolved = PathResolver().resolve("run://output/report.txt", scope, "create")
    assert resolved.physical == (workspace.runs / "run1" / "workspace" / "output" / "report.txt")
    materialized = materialize_tool_payload(
        {"path": "run://output/report.txt"},
        scope=scope,
        capabilities=("run:output:read-write",),
        effect="write",
    )
    assert materialized["path"] == str(resolved.physical)

    with pytest.raises(PathCapabilityError, match="capability_denied"):
        materialize_tool_payload(
            {"path": "project://reference/customer.csv"},
            scope=scope,
            capabilities=("run:output:read-write",),
            effect="read",
        )


def test_artifact_record_contains_logical_identity_only(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    harness = RunSession.create(
        workspace,
        run_id="artifact-run",
        skills_root=default_skills_root(),
        auto_approve=True,
    )
    harness.start_run("attach")
    output = workspace.runs / "artifact-run" / "workspace" / "output" / "report.txt"
    output.write_text("portable", encoding="utf-8")
    attachment = harness.put_artifact("run://output/report.txt", name="report.txt")

    record = harness.runtime.graph.get_object(attachment["object_id"])
    assert record is not None
    assert record.data["uri"] == attachment["uri"]
    assert record.data["source"] == "run://output/report.txt"
    assert "path" not in record.data
    assert str(workspace.root) not in json.dumps(record.data)


def test_store_backup_is_consistent_and_restore_is_staged(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    harness = RunSession.create(
        workspace,
        run_id="store-run",
        skills_root=default_skills_root(),
    )
    harness.start_run("store")
    lifecycle = StoreLifecycle()
    backup_path = tmp_path / "store-backup.sqlite"
    backup = lifecycle.backup(workspace, backup_path)
    assert backup.sha256.startswith("sha256:")
    assert Path(backup.manifest_path).is_file()
    assert harness.runtime.graph.store is not None
    harness.runtime.graph.store.close()

    with sqlite3.connect(workspace.db) as connection:
        original_events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        connection.execute("DELETE FROM events")
    connection.close()
    restored = lifecycle.restore(workspace, backup_path)
    assert restored.ok is True
    assert restored.previous_backup is not None
    with sqlite3.connect(workspace.db) as connection:
        restored_events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert restored_events == original_events


def test_project_archive_round_trip_between_absolute_homes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, project="portable")
    (workspace.reference / "schema.json").write_text('{"ok":true}', encoding="utf-8")
    archive = tmp_path / "portable.roih"
    exported = ProjectArchive().export(workspace, archive, mode="definition")
    assert exported.ok is True

    target_home = tmp_path / "different-root"
    verified = ProjectArchive().import_archive(
        archive,
        target_home,
        verify_only=True,
    )
    assert verified.changed is False
    imported = ProjectArchive().import_archive(archive, target_home)
    assert imported.changed is True
    imported_workspace = Workspace.open(target_home, project="portable")
    assert (imported_workspace.reference / "schema.json").read_text() == '{"ok":true}'
    assert str(workspace.root) not in archive.read_bytes().decode("latin1")


def test_project_archive_rejects_unlisted_traversal_entry(tmp_path: Path) -> None:
    archive = tmp_path / "evil.roih"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("../escape", "bad")
        target.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "roi-h-project",
                    "format_version": 1,
                    "mode": "definition",
                    "project": {"project_id": "prj_test", "slug": "evil"},
                    "files": [],
                }
            ),
        )
    with pytest.raises(ValueError, match=r"archive\.path_unsafe"):
        ProjectArchive().inspect(archive)


def test_secret_values_are_environment_isolated_and_not_in_project_files(
    tmp_path: Path,
) -> None:
    dev = _workspace(tmp_path)
    prod = Workspace.open(dev.root, project=dev.project, env="prod")
    set_secret(dev, "PORTAL_PASSWORD", "dev-only-value")
    assert get_secret(dev, "PORTAL_PASSWORD") == "dev-only-value"
    assert get_secret(prod, "PORTAL_PASSWORD") is None
    assert list_secrets(dev)["names"] == ["PORTAL_PASSWORD"]
    assert list_secrets(prod)["names"] == []
    assert "dev-only-value" not in (dev.project_root / "secrets.meta.json").read_text()
    assert not (dev.project_root / "secrets.json").exists()


def test_retention_requires_a_fresh_plan_for_closed_run_logs(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    paths = RunStorage(workspace).prepare("cleanup-run")
    log = paths.diagnostics / "worker.log"
    log.write_text("delete later", encoding="utf-8")
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    manifest.update(
        state="terminal",
        terminal_status="success",
        finalized_at=(datetime.now(UTC) - timedelta(days=8)).isoformat(),
    )
    paths.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    planner = RetentionPlanner()
    stale = planner.plan(workspace)
    log.write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"retention\.plan_stale"):
        planner.apply(workspace, stale.plan_id)

    fresh = planner.plan(workspace)
    result = planner.apply(workspace, fresh.plan_id)
    assert result.ok is True
    assert list(paths.diagnostics.iterdir()) == []
    assert paths.input.is_dir()
    assert paths.output.is_dir()


def test_retention_rejects_changed_policy_or_plan_target(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    paths = RunStorage(workspace).prepare("closed-run")
    (paths.diagnostics / "worker.log").write_text("delete later", encoding="utf-8")
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    manifest.update(
        state="terminal",
        terminal_status="success",
        finalized_at=(datetime.now(UTC) - timedelta(days=8)).isoformat(),
    )
    paths.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    planner = RetentionPlanner()

    policy_plan = planner.plan(workspace)
    configure_project(workspace.root, workspace.project, log_retention="forever")
    with pytest.raises(RuntimeError, match=r"retention\.plan_stale"):
        planner.apply(workspace, policy_plan.plan_id)
    assert (paths.diagnostics / "worker.log").is_file()

    configure_project(workspace.root, workspace.project, log_retention="7d")
    changed_plan = planner.plan(workspace)
    plan_path = workspace.runtime / "retention-plans" / f"{changed_plan.plan_id}.json"
    raw = json.loads(plan_path.read_text(encoding="utf-8"))
    raw["targets"][0]["relative_path"] = "reference"
    plan_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"retention\.plan_stale"):
        planner.apply(workspace, changed_plan.plan_id)
    assert workspace.reference.is_dir()
    assert (paths.diagnostics / "worker.log").is_file()

    metadata_plan = planner.plan(workspace)
    metadata_path = workspace.runtime / "retention-plans" / f"{metadata_plan.plan_id}.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["project_id"] = "prj_forged"
    metadata["bytes"] = 1
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"retention\.plan_stale"):
        planner.apply(workspace, metadata_plan.plan_id)


def test_retention_fingerprint_and_lease_protect_run_logs(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    paths = RunStorage(workspace).prepare("closed-run")
    first = paths.diagnostics / "first.log"
    second = paths.diagnostics / "second.log"
    first.write_text("aaaa", encoding="utf-8")
    second.write_text("bbbb", encoding="utf-8")
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    manifest.update(
        state="terminal",
        terminal_status="success",
        finalized_at=(datetime.now(UTC) - timedelta(days=8)).isoformat(),
    )
    paths.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    planner = RetentionPlanner()

    changed = planner.plan(workspace)
    first.write_text("cccc", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"retention\.plan_stale"):
        planner.apply(workspace, changed.plan_id)

    changed_tree = planner.plan(workspace)
    (paths.diagnostics / "empty").mkdir()
    with pytest.raises(RuntimeError, match=r"retention\.plan_stale"):
        planner.apply(workspace, changed_tree.plan_id)
    (paths.diagnostics / "empty").rmdir()

    locked = planner.plan(workspace)
    with run_lease(workspace, "closed-run"), pytest.raises(RuntimeError, match="lease is busy"):
        planner.apply(workspace, locked.plan_id)

    with project_policy_lease(workspace.project_root):
        with pytest.raises(RuntimeError, match="lease is busy"):
            planner.apply(workspace, locked.plan_id)
        with pytest.raises(RuntimeError, match="lease is busy"):
            configure_project(workspace.root, workspace.project, log_retention="forever")


def test_run_lease_rejects_symlink_without_changing_target(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("keep", encoding="utf-8")
    lock = workspace.runtime / "locks" / "run-closed-run.lock"
    lock.symlink_to(target)

    with (
        pytest.raises(RuntimeError, match="must not be a symlink"),
        run_lease(workspace, "closed-run"),
    ):
        pytest.fail("symlinked lease was acquired")
    assert target.read_text(encoding="utf-8") == "keep"


def test_reopen_marks_terminal_workspace_active_before_retention(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    session = RunSession.create(
        workspace,
        run_id="reopened-run",
        skills_root=default_skills_root(),
    )
    session.start_run("reopen")
    storage = RunStorage(workspace)
    paths = storage.finalize("reopened-run", status="success")
    run_paths = storage.paths("reopened-run")
    (run_paths.diagnostics / "worker.log").write_text("keep", encoding="utf-8")
    manifest = json.loads(run_paths.manifest.read_text(encoding="utf-8"))
    manifest["finalized_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    run_paths.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    plan = RetentionPlanner().plan(workspace)
    assert plan.targets
    assert paths["ok"] is True
    assert session.runtime.graph.store is not None
    session.runtime.graph.store.close()

    RunSession.reopen(
        workspace,
        run_id="reopened-run",
        skills_root=default_skills_root(),
    )

    active = json.loads(run_paths.manifest.read_text(encoding="utf-8"))
    assert active["state"] == "active"
    with pytest.raises(RuntimeError, match=r"retention\.plan_stale"):
        RetentionPlanner().apply(workspace, plan.plan_id)
    assert (run_paths.diagnostics / "worker.log").is_file()


def test_retention_never_plans_active_or_forever_run_logs(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    active = RunStorage(workspace).prepare("active-run")
    (active.diagnostics / "worker.log").write_text("keep", encoding="utf-8")
    assert RetentionPlanner().plan(workspace).targets == ()

    closed = RunStorage(workspace).prepare("closed-run")
    (closed.diagnostics / "worker.log").write_text("keep", encoding="utf-8")
    manifest = json.loads(closed.manifest.read_text(encoding="utf-8"))
    manifest.update(
        state="terminal",
        terminal_status="success",
        finalized_at=(datetime.now(UTC) - timedelta(days=100)).isoformat(),
    )
    closed.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    configure_project(workspace.root, workspace.project, log_retention="forever")

    assert RetentionPlanner().plan(workspace).targets == ()
