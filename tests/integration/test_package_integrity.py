"""Immutable modular source package integrity."""

import stat
from pathlib import Path

import pytest

from roi_h.harness.automation import load_automation
from roi_h.harness.automation_source import put_source
from roi_h.harness.journeys import run_development_source, ship_automation
from roi_h.harness.workspace import Workspace, create_project


def _verified_package(tmp_path: Path) -> tuple[Workspace, dict[str, object]]:
    home = tmp_path / ".roi-h"
    create_project(home, "integrity")
    dev = Workspace.open(home, project="integrity", env="dev")
    put_source(
        dev.automation_sources,
        "integrity",
        {
            "name": "integrity",
            "phases": [
                {"id": "build", "module": "build"},
                {"id": "verify", "module": "verify", "role": "verify", "needs": ["build"]},
            ],
        },
        {
            "build.py": "def run(context):\n    return {'summary': {'built': True}}\n",
            "verify.py": "def run(context):\n    return {'summary': {'verified': True}}\n",
        },
    )
    assert run_development_source(dev, name="integrity", run_id="integrity-dev")["ok"]
    shipped = ship_automation(
        dev,
        name="integrity",
        version="1.0.0",
        from_run="integrity-dev",
    )
    return dev, shipped


def test_same_verified_source_version_is_idempotent(tmp_path: Path) -> None:
    dev, first = _verified_package(tmp_path)

    second = ship_automation(
        dev,
        name="integrity",
        version="1.0.0",
        from_run="integrity-dev",
    )

    assert second["package_digest"] == first["package_digest"]


def test_package_tampering_is_rejected_before_load(tmp_path: Path) -> None:
    dev, _ = _verified_package(tmp_path)
    source = dev.automations / "integrity" / "1.0.0" / "source" / "verify.py"
    source.chmod(stat.S_IREAD | stat.S_IWRITE)
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="digest mismatch"):
        load_automation(dev, "integrity", version="1.0.0")
