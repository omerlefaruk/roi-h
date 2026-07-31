"""End-to-end modular automation source execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import roi_h.harness.automation_runner as runner_module
from roi_h.harness.automation_runner import run_source
from roi_h.harness.workspace import Workspace, create_project


def _source(root: Path) -> Path:
    source = root / "parallel-report"
    (source / "phases").mkdir(parents=True)
    (source / "automation.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "parallel-report",
                "max_parallel": 2,
                "phases": [
                    {
                        "id": "left",
                        "module": "phases.left",
                        "parallel_safe": True,
                    },
                    {
                        "id": "right",
                        "module": "phases.right",
                        "parallel_safe": True,
                    },
                    {
                        "id": "verify",
                        "module": "phases.verify",
                        "role": "verify",
                        "needs": ["left", "right"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    for name in ("left", "right"):
        (source / "phases" / f"{name}.py").write_text(
            (
                "import time\n"
                "def run(context):\n"
                "    started = time.time()\n"
                "    time.sleep(0.35)\n"
                f"    path = context.output_path('{name}.txt')\n"
                f"    path.write_text('{name}', encoding='utf-8')\n"
                "    ended = time.time()\n"
                f"    return {{'summary': {{'started': started, 'ended': ended}}, "
                f"'artifacts': {{'{name}': '{name}.txt'}}}}\n"
            ),
            encoding="utf-8",
        )
    (source / "phases" / "verify.py").write_text(
        (
            "def run(context):\n"
            "    left = context.dependencies['left']['left'].read_text(encoding='utf-8')\n"
            "    right = context.dependencies['right']['right'].read_text(encoding='utf-8')\n"
            "    assert left + right == 'leftright'\n"
            "    path = context.output_path('verified.txt')\n"
            "    path.write_text(left + right, encoding='utf-8')\n"
            "    return {'summary': {'verified': True}, "
            "'artifacts': {'verified': 'verified.txt'}}\n"
        ),
        encoding="utf-8",
    )
    return source


def test_parallel_phases_feed_a_verification_phase_and_activegraph(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")

    result = run_source(workspace, _source(tmp_path), run_id="parallel-run")

    assert result["ok"] is True, result
    left = result["phases"]["left"]["summary"]
    right = result["phases"]["right"]["summary"]
    assert max(left["started"], right["started"]) < min(left["ended"], right["ended"])
    assert result["phases"]["verify"]["summary"] == {"verified": True}
    runtime = result["runtime"]
    event_types = [event.type for event in runtime.graph.events]
    assert event_types.count("phase.started") == 3
    assert event_types.count("phase.succeeded") == 3
    assert "source.snapshotted" in event_types
    assert "run.completed" in event_types
    artifacts = list(runtime.graph.objects(type="rpa.artifact"))
    assert {item.data["name"] for item in artifacts} == {"left", "right", "verified"}


def test_failed_phase_blocks_its_dependents(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")
    source = _source(tmp_path)
    (source / "phases" / "left.py").write_text(
        "def run(context):\n    raise RuntimeError('expected failure')\n",
        encoding="utf-8",
    )

    result = run_source(workspace, source, run_id="failed-run")

    assert result["ok"] is False
    assert result["phase_states"]["left"] == "failed"
    assert result["phase_states"]["right"] == "done"
    assert result["phase_states"]["verify"] == "blocked"


def test_each_phase_uses_an_isolated_source_copy(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")
    source = _source(tmp_path)
    (source / "phases" / "left.py").write_text(
        (
            "def run(context):\n"
            "    target = context.source_root / 'phases' / 'verify.py'\n"
            "    target.chmod(0o600)\n"
            "    target.write_text('def run(context): return {}\\n', encoding='utf-8')\n"
            "    return {'summary': {'changed': True}}\n"
        ),
        encoding="utf-8",
    )

    result = run_source(workspace, source, run_id="source-mutation")

    assert result["ok"] is False
    assert result["phase_states"]["left"] == "failed"
    assert result["phase_states"]["right"] == "done"
    assert result["phase_states"]["verify"] == "blocked"
    left = next(
        item
        for item in result["runtime"].graph.objects(type="rpa.phase")
        if item.data["name"] == "left"
    )
    assert left.data["error"]["code"] == "source.integrity_failed"


def test_secret_output_is_removed_and_diagnostics_are_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declared_value = "customer-protected-value"
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")
    source = _source(tmp_path)
    manifest = json.loads((source / "automation.json").read_text(encoding="utf-8"))
    manifest["required_secrets"] = ["TOKEN"]
    (source / "automation.json").write_text(json.dumps(manifest), encoding="utf-8")
    (source / "phases" / "left.py").write_text(
        (
            "def run(context):\n"
            "    value = context.secret('TOKEN')\n"
            "    print(value)\n"
            "    relative = value + '.txt'\n"
            "    path = context.output_path(relative)\n"
            "    path.write_text('safe-content', encoding='utf-8')\n"
            "    return {'summary': {'ok': True}, 'artifacts': {'leak': relative}}\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner_module,
        "get_secret",
        lambda _workspace, _name: declared_value,
    )

    result = run_source(workspace, source, run_id="secret-leak")

    assert result["ok"] is False
    assert result["phases"]["left"]["error"]["code"] == "phase.secret_leak"
    graph = result["runtime"].graph
    graph_text = json.dumps(
        {
            "objects": [item.data for item in graph.objects()],
            "events": [item.payload for item in graph.events],
        },
        default=str,
    )
    assert declared_value not in graph_text
    diagnostics = list((workspace.runs / "secret-leak" / "diagnostics").glob("*.log"))
    assert diagnostics
    assert all(declared_value not in path.read_text(encoding="utf-8") for path in diagnostics)
    run_root = workspace.runs / "secret-leak"
    assert all(declared_value not in path.name for path in run_root.rglob("*"))
    left = next(
        item
        for item in result["runtime"].graph.objects(type="rpa.phase")
        if item.data["name"] == "left"
    )
    assert left.data["attempt_id"].startswith("attempt_")
    assert left.data["diagnostics"]["stdout"].startswith("run://diagnostics/")


def test_physical_paths_cannot_enter_phase_evidence(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")
    source = _source(tmp_path)
    (source / "phases" / "left.py").write_text(
        "def run(context):\n    return {'summary': {'path': str(context.work_dir)}}\n",
        encoding="utf-8",
    )

    result = run_source(workspace, source, run_id="physical-summary")

    assert result["ok"] is False
    assert result["phases"]["left"]["error"]["code"] == "phase.unsafe_result"
    graph = result["runtime"].graph
    graph_text = json.dumps(
        {
            "objects": [item.data for item in graph.objects()],
            "events": [item.payload for item in graph.events],
        },
        default=str,
    )
    assert str(workspace.root) not in graph_text


def test_artifact_names_are_scoped_by_phase(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")
    source = _source(tmp_path)
    for phase_id in ("left", "right"):
        (source / "phases" / f"{phase_id}.py").write_text(
            (
                "def run(context):\n"
                "    path = context.output_path('result.txt')\n"
                f"    path.write_text('{phase_id}', encoding='utf-8')\n"
                "    return {'artifacts': {'result': 'result.txt'}}\n"
            ),
            encoding="utf-8",
        )
    (source / "phases" / "verify.py").write_text(
        (
            "def run(context):\n"
            "    assert context.dependencies['left']['result'].read_text() == 'left'\n"
            "    assert context.dependencies['right']['result'].read_text() == 'right'\n"
            "    return {'summary': {'verified': True}}\n"
        ),
        encoding="utf-8",
    )

    result = run_source(workspace, source, run_id="phase-scoped-artifacts")

    assert result["ok"] is True
    artifacts = list(result["runtime"].graph.objects(type="rpa.artifact"))
    result_artifacts = [item for item in artifacts if item.data["name"] == "result"]
    assert len(result_artifacts) == 2
    assert len({item.data["artifact_id"] for item in result_artifacts}) == 2
