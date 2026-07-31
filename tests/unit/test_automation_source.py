"""Contracts for modular automation source trees."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from roi_h.harness.automation_source import (
    AutomationSourceManifest,
    PhaseResult,
    load_source_manifest,
    put_source,
    show_source,
    snapshot_source,
    source_tree_digest,
)


def _source(root: Path) -> Path:
    source = root / "daily-report"
    (source / "phases").mkdir(parents=True)
    (source / "automation.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "daily-report",
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
    for name in ("left", "right", "verify"):
        (source / "phases" / f"{name}.py").write_text(
            "def run(context):\n    return {'summary': {}, 'artifacts': {}}\n",
            encoding="utf-8",
        )
    return source


def test_manifest_validates_dependency_graph_and_modules(tmp_path: Path) -> None:
    source = _source(tmp_path)

    manifest = load_source_manifest(source)

    assert manifest.ordered_phase_ids() == ["left", "right", "verify"]
    assert manifest.max_parallel == 2


def test_manifest_rejects_cycles_and_requires_verification() -> None:
    with pytest.raises(ValidationError, match="verification phase"):
        AutomationSourceManifest.model_validate(
            {"name": "bad", "phases": [{"id": "work", "module": "phases.work"}]}
        )

    with pytest.raises(ValidationError, match="dependency cycle"):
        AutomationSourceManifest.model_validate(
            {
                "name": "bad",
                "phases": [
                    {"id": "a", "module": "phases.a", "needs": ["b"]},
                    {
                        "id": "b",
                        "module": "phases.b",
                        "needs": ["a"],
                        "role": "verify",
                    },
                ],
            }
        )


def test_phase_result_rejects_nonportable_artifact_paths() -> None:
    with pytest.raises(ValidationError, match="relative"):
        PhaseResult.model_validate({"artifacts": {"bad": "../outside.txt"}})


def test_manifest_requires_separate_work_and_verification_modules() -> None:
    with pytest.raises(ValidationError, match="work phase separate"):
        AutomationSourceManifest.model_validate(
            {"name": "bad", "phases": [{"id": "verify", "module": "verify", "role": "verify"}]}
        )

    with pytest.raises(ValidationError, match="own Python module"):
        AutomationSourceManifest.model_validate(
            {
                "name": "bad",
                "phases": [
                    {"id": "work", "module": "all"},
                    {
                        "id": "verify",
                        "module": "all",
                        "role": "verify",
                        "needs": ["work"],
                    },
                ],
            }
        )


def test_snapshot_is_content_addressed_and_immutable(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "run" / "source"

    snapshot = snapshot_source(source, destination)
    same = snapshot_source(source, destination)

    assert snapshot.source_digest == same.source_digest
    assert snapshot.files == same.files
    assert source_tree_digest(destination)[0] == snapshot.source_digest

    (source / "phases" / "left.py").write_text(
        "def run(context):\n    return {'summary': {'changed': True}, 'artifacts': {}}\n",
        encoding="utf-8",
    )
    with pytest.raises(FileExistsError, match="different content"):
        snapshot_source(source, destination)


def test_source_reader_recovers_an_interrupted_pointer_update(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    put_source(
        sources,
        "recover",
        {
            "name": "recover",
            "phases": [
                {"id": "work", "module": "work"},
                {"id": "verify", "module": "verify", "role": "verify", "needs": ["work"]},
            ],
        },
        {
            "work.py": "def run(context): return {}\n",
            "verify.py": "def run(context): return {}\n",
        },
    )
    target = sources / "recover"
    previous = sources / ".recover.previous-test"
    target.replace(previous)
    updates = sources / ".updates"
    updates.mkdir(exist_ok=True)
    (updates / "recover.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": "recover",
                "previous": previous.name,
                "staging": ".recover.source-missing",
            }
        ),
        encoding="utf-8",
    )

    result = show_source(target)

    assert result["name"] == "recover"
    assert target.is_dir()
    assert not previous.exists()
    assert not (updates / "recover.json").exists()
