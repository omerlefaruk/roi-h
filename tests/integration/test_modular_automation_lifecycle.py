"""Immutable development-to-production lifecycle for modular automation source."""

from __future__ import annotations

from pathlib import Path

import pytest

from roi_h.harness.activegraph_runtime import ROIHRuntime
from roi_h.harness.automation import load_automation
from roi_h.harness.automation_source import put_source
from roi_h.harness.journeys import run_automation, run_development_source, ship_automation
from roi_h.harness.records import evidenced_artifacts
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.workspace import Workspace, create_project


def _put(workspace: Workspace, *, value: str = "verified") -> str:
    snapshot = put_source(
        workspace.automation_sources,
        "report",
        {
            "schema_version": 1,
            "name": "report",
            "phases": [
                {"id": "build", "module": "phases.build"},
                {
                    "id": "verify",
                    "module": "phases.verify",
                    "role": "verify",
                    "needs": ["build"],
                },
            ],
        },
        {
            "phases/build.py": (
                "def run(context):\n"
                "    path = context.output_path('report.txt')\n"
                f"    path.write_text({value!r}, encoding='utf-8')\n"
                "    return {'artifacts': {'report': 'report.txt'}}\n"
            ),
            "phases/verify.py": (
                "def run(context):\n"
                "    value = context.dependencies['build']['report'].read_text(encoding='utf-8')\n"
                "    assert value\n"
                "    path = context.output_path('proof.txt')\n"
                "    path.write_text(value, encoding='utf-8')\n"
                "    return {'summary': {'verified': value}, "
                "'artifacts': {'proof': 'proof.txt'}}\n"
            ),
        },
    )
    return snapshot.source_digest


def test_verified_dev_source_ships_and_runs_as_exact_production_package(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    dev = Workspace.open(home, project="demo", env="dev")
    source_digest = _put(dev)

    development = run_development_source(dev, name="report", run_id="dev-report")
    assert development["ok"] is True
    assert development["source_digest"] == source_digest

    shipped = ship_automation(
        dev,
        name="report",
        version="1.0.0",
        from_run="dev-report",
    )
    assert shipped["source_digest"] == source_digest

    _put(dev, value="changed-after-shipping")
    prod = Workspace.open(home, project="demo", env="prod")
    package = load_automation(prod, "report")
    assert package["source_digest"] == source_digest

    production = run_automation(prod, name="report", run_id="prod-report")
    assert production["ok"] is True
    assert production["automation"]["package_digest"] == shipped["package_digest"]
    assert production["phases"]["verify"]["summary"] == {"verified": "verified"}


def test_failed_development_run_cannot_ship(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    dev = Workspace.open(home, project="demo", env="dev")
    _put(dev)
    (dev.automation_sources / "report" / "phases" / "verify.py").write_text(
        "def run(context):\n    raise RuntimeError('verification failed')\n",
        encoding="utf-8",
    )

    result = run_development_source(dev, name="report", run_id="failed-report")
    assert result["ok"] is False

    with pytest.raises(ValueError, match="did not complete"):
        ship_automation(
            dev,
            name="report",
            version="1.0.0",
            from_run="failed-report",
        )


def test_run_inputs_are_materialized_for_phases_and_recorded_in_activegraph(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    dev = Workspace.open(home, project="demo", env="dev")
    source_input = tmp_path / "customer.txt"
    source_input.write_text("customer-value", encoding="utf-8")
    put_source(
        dev.automation_sources,
        "input-report",
        {
            "name": "input-report",
            "phases": [
                {"id": "read", "module": "phases.read"},
                {
                    "id": "verify",
                    "module": "phases.verify",
                    "role": "verify",
                    "needs": ["read"],
                },
            ],
        },
        {
            "phases/read.py": (
                "def run(context):\n"
                "    value = (context.input_dir / 'value.txt').read_text(encoding='utf-8')\n"
                "    path = context.output_path('value.txt')\n"
                "    path.write_text(value, encoding='utf-8')\n"
                "    return {'artifacts': {'value': 'value.txt'}}\n"
            ),
            "phases/verify.py": (
                "def run(context):\n"
                "    value = context.dependencies['read']['value'].read_text(encoding='utf-8')\n"
                "    assert value == 'customer-value'\n"
                "    return {'summary': {'verified': value}}\n"
            ),
        },
    )

    result = run_development_source(
        dev,
        name="input-report",
        run_id="input-run",
        inputs={"value.txt": str(source_input)},
    )

    assert result["phases"]["verify"]["summary"] == {"verified": "customer-value"}
    runtime = ROIHRuntime.load(str(dev.db), run_id="input-run")
    run = next(iter(runtime.graph.objects(type="rpa.run")))
    assert run.data["inputs"][0]["path"] == "run://input/value.txt"
    assert any(event.type == "run.inputs_materialized" for event in runtime.graph.events)


def test_run_id_cannot_be_reused(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    dev = Workspace.open(home, project="demo", env="dev")
    _put(dev)
    run_development_source(dev, name="report", run_id="one-run")

    with pytest.raises(FileExistsError, match="run id already exists"):
        run_development_source(dev, name="report", run_id="one-run")

    runtime = ROIHRuntime.load(str(dev.db), run_id="one-run")
    assert len(list(runtime.graph.objects(type="rpa.run"))) == 1


def test_input_preflight_failure_does_not_reserve_development_or_production_run(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    dev = Workspace.open(home, project="demo", env="dev")
    _put(dev)
    input_file = tmp_path / "input.txt"
    input_file.write_text("input", encoding="utf-8")
    missing_file = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError, match="not a regular file"):
        run_development_source(
            dev,
            name="report",
            run_id="dev-input-retry",
            inputs={"input.txt": str(missing_file)},
        )
    assert not RunStorage(dev).paths("dev-input-retry").root.exists()
    assert run_development_source(
        dev,
        name="report",
        run_id="dev-input-retry",
        inputs={"input.txt": str(input_file)},
    )["ok"]

    ship_automation(dev, name="report", version="1.0.0", from_run="dev-input-retry")
    prod = Workspace.open(home, project="demo", env="prod")
    with pytest.raises(FileNotFoundError, match="not a regular file"):
        run_automation(
            prod,
            name="report",
            run_id="prod-input-retry",
            inputs={"input.txt": str(missing_file)},
        )
    assert not RunStorage(prod).paths("prod-input-retry").root.exists()
    assert run_automation(
        prod,
        name="report",
        run_id="prod-input-retry",
        inputs={"input.txt": str(input_file)},
    )["ok"]


def test_artifact_reads_verify_activegraph_evidence(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    dev = Workspace.open(home, project="demo", env="dev")
    _put(dev)
    run_development_source(dev, name="report", run_id="artifact-evidence")
    artifacts = evidenced_artifacts(dev, "artifact-evidence")
    assert artifacts
    artifacts[0].path.write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact evidence mismatch"):
        evidenced_artifacts(dev, "artifact-evidence")
