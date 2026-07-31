"""Read-only observer projections and HTTP routes."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from openpyxl import Workbook

from roi_h.observer.projection import (
    catalog,
    get_run,
    list_runs,
    preview_artifact,
    resolve_artifact,
)
from roi_h.observer.server import ObserverServer


def _event(
    connection: sqlite3.Connection,
    *,
    sequence: int,
    event_type: str,
    payload: dict[str, object],
) -> None:
    connection.execute(
        """
        INSERT INTO events
            (seq, id, type, actor, payload, frame_id, caused_by, timestamp, run_id)
        VALUES (?, ?, ?, 'system', ?, NULL, NULL, ?, 'demo-run')
        """,
        (
            sequence,
            f"evt_{sequence:03d}",
            event_type,
            json.dumps(payload),
            f"2026-07-29T09:0{sequence}:00Z",
        ),
    )


def _observer_home(tmp_path: Path) -> Path:
    home = tmp_path / ".roi-h"
    project = home / "projects" / "demo"
    environment = project / "dev"
    artifacts = environment / "artifacts" / "demo-run"
    artifacts.mkdir(parents=True)
    (home / "config.json").write_text('{"project":"demo","version":2}', encoding="utf-8")
    (project / "config.json").write_text(
        '{"name":"demo","display_name":"Demo project","env":"dev"}',
        encoding="utf-8",
    )
    database = environment / "rpa.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                parent_run_id TEXT,
                forked_at_event_id TEXT,
                label TEXT,
                created_at TEXT NOT NULL,
                goal TEXT,
                frame_id TEXT
            );
            CREATE TABLE events (
                seq INTEGER PRIMARY KEY,
                id TEXT NOT NULL,
                type TEXT NOT NULL,
                actor TEXT,
                payload TEXT NOT NULL,
                frame_id TEXT,
                caused_by TEXT,
                timestamp TEXT NOT NULL,
                run_id TEXT NOT NULL
            );
            INSERT INTO runs (run_id, created_at)
            VALUES ('demo-run', '2026-07-29T09:00:00Z');
            """
        )
        _event(
            connection,
            sequence=1,
            event_type="object.created",
            payload={
                "object": {
                    "id": "rpa.run#1",
                    "type": "rpa.run",
                    "data": {
                        "goal": "Prepare daily sales summary",
                        "status": "open",
                        "env": "dev",
                        "phase_plan": [{"name": "download"}, {"name": "deliver"}],
                    },
                }
            },
        )
        _event(
            connection,
            sequence=2,
            event_type="object.created",
            payload={
                "object": {
                    "id": "rpa.phase#2",
                    "type": "rpa.phase",
                    "data": {
                        "run_id": "demo-run",
                        "name": "download",
                        "index": 1,
                        "status": "open",
                    },
                }
            },
        )
        _event(
            connection,
            sequence=3,
            event_type="object.created",
            payload={
                "object": {
                    "id": "rpa.step#3",
                    "type": "rpa.step",
                    "data": {
                        "run_id": "demo-run",
                        "skill": "browser",
                        "tool": "download",
                        "name": "browser.download",
                        "status": "ok",
                        "phase": "download",
                        "phase_id": "rpa.phase#2",
                        "invocation_id": "inv_1",
                    },
                }
            },
        )
        _event(
            connection,
            sequence=4,
            event_type="object.created",
            payload={
                "object": {
                    "id": "rpa.artifact#4",
                    "type": "rpa.artifact",
                    "data": {
                        "run_id": "demo-run",
                        "name": "summary.xlsx",
                        "path": str(artifacts / "summary.xlsx"),
                        "bytes": 0,
                        "sha256": "abc",
                        "phase": "download",
                        "phase_id": "rpa.phase#2",
                    },
                }
            },
        )
        _event(
            connection,
            sequence=5,
            event_type="patch.applied",
            payload={
                "patch": {
                    "target": "rpa.phase#2",
                    "value": {"status": "done"},
                }
            },
        )

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["Date", "Amount"])
    worksheet.append(["2026-07-29", 125])
    workbook.save(artifacts / "summary.xlsx")
    return home


def test_observer_projects_run_story_and_workbook_preview(tmp_path: Path) -> None:
    home = _observer_home(tmp_path)

    projects = catalog(home)
    assert projects["active_project"] == "demo"
    assert projects["projects"][0]["display_name"] == "Demo project"

    runs = list_runs(home)
    assert len(runs) == 1
    assert runs[0]["title"] == "Prepare daily sales summary"
    assert runs[0]["status"] == "completed"

    detail = get_run(home, project="demo", env="dev", run_id="demo-run")
    assert [item["status"] for item in detail["story"]] == ["completed", "not_started"]
    assert detail["story"][0]["artifacts"][0]["name"] == "summary.xlsx"

    preview = preview_artifact(
        home,
        project="demo",
        env="dev",
        run_id="demo-run",
        relative_path="summary.xlsx",
    )
    assert preview["kind"] == "table"
    assert preview["rows"] == [["Date", "Amount"], ["2026-07-29", 125]]


def test_observer_rejects_artifact_path_traversal(tmp_path: Path) -> None:
    home = _observer_home(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        resolve_artifact(
            home,
            project="demo",
            env="dev",
            run_id="demo-run",
            relative_path="../../../../../../outside.txt",
        )


def test_observer_http_surface_is_get_only(tmp_path: Path) -> None:
    home = _observer_home(tmp_path)
    server = ObserverServer(("127.0.0.1", 0), home=home)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(  # noqa: S310
            f"{base}/api/runs",
            timeout=3,
        ) as response:
            payload = json.loads(response.read())
        assert payload["runs"][0]["run_id"] == "demo-run"

        request = urllib.request.Request(  # noqa: S310
            f"{base}/api/runs",
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=3)  # noqa: S310
        assert raised.value.code == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
