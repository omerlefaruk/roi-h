"""Multi-project workspace registry and resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roi_h.harness.lease import run_lease
from roi_h.harness.workspace import (
    Workspace,
    create_project,
    get_active_project,
    init_home,
    list_projects,
    resolve_home,
    set_active_env,
    set_active_project,
    validate_project_name,
)


def test_create_list_use_project(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    created = create_project(home, "acme-orders", display_name="Acme", set_active=True)
    assert created["ok"] is True
    assert created["project"] == "acme-orders"
    project = home / "projects" / "acme-orders"
    assert (project / "skills").is_dir()
    assert (project / "packages" / "automations").is_dir()
    assert (project / "environments" / "prod" / "store").is_dir()
    assert (project / "reference").is_dir()
    assert (home / "skills").is_dir()
    assert get_active_project(home) == "acme-orders"

    create_project(home, "beta-finance", set_active=False)
    items = list_projects(home)
    assert {p["name"] for p in items} == {"acme-orders", "beta-finance"}
    assert sum(1 for p in items if p["active"]) == 1

    set_active_project(home, "beta-finance")
    assert get_active_project(home) == "beta-finance"
    ws = Workspace.open(home)
    assert ws.project == "beta-finance"
    assert ws.project_skills == (home / "projects" / "beta-finance" / "skills").resolve()


def test_project_resolution_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "alpha", set_active=True)
    create_project(home, "beta", set_active=False)

    monkeypatch.delenv("ROI_H_PROJECT", raising=False)
    assert Workspace.open(home).project == "alpha"

    monkeypatch.setenv("ROI_H_PROJECT", "beta")
    assert Workspace.open(home).project == "beta"

    assert Workspace.open(home, project="alpha").project == "alpha"


def test_open_removes_legacy_adaptive_policy_from_all_environments(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "acme", set_active=True)
    manifests = [
        home / "projects" / "acme" / "environments" / env / "environment.json"
        for env in ("dev", "prod")
    ]
    for manifest in manifests:
        config = json.loads(manifest.read_text(encoding="utf-8"))
        config["execution"]["allow_adaptive"] = True
        manifest.write_text(json.dumps(config), encoding="utf-8")

    Workspace.open(home, project="acme", env="dev")

    for manifest in manifests:
        updated = json.loads(manifest.read_text(encoding="utf-8"))
        assert "allow_adaptive" not in updated["execution"]


def test_env_lives_on_project_config(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "acme", set_active=True, env="dev")
    create_project(home, "other", set_active=False, env="dev")

    set_active_env(home, "prod", project="acme")
    assert Workspace.open(home, project="acme").env == "prod"
    assert Workspace.open(home, project="other").env == "dev"

    # sticky project still acme; env sticky is per-project
    assert Workspace.open(home).env == "prod"


def test_init_home_creates_default(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = init_home(home)
    assert result["created"] is True
    assert result["project"] == "default"
    assert list_projects(home)[0]["name"] == "default"

    again = init_home(home)
    assert again["created"] is False


def test_isolation_of_skills_and_db(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "a", set_active=True)
    create_project(home, "b", set_active=False)
    a = Workspace.open(home, project="a", env="dev")
    b = Workspace.open(home, project="b", env="dev")
    assert a.db != b.db
    assert a.project_skills != b.project_skills
    assert a.automations != b.automations
    (a.project_skills / "marker.txt").write_text("a", encoding="utf-8")
    assert not (b.project_skills / "marker.txt").exists()


def test_validate_project_name() -> None:
    validate_project_name("acme-orders")
    with pytest.raises(ValueError, match="invalid project name"):
        validate_project_name("Acme")
    with pytest.raises(ValueError, match="reserved"):
        validate_project_name("projects")
    with pytest.raises(ValueError, match="invalid project name"):
        validate_project_name("../x")


def test_missing_project_errors(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    with pytest.raises(FileNotFoundError, match="no projects"):
        Workspace.open(home)
    create_project(home, "only", set_active=True)
    with pytest.raises(FileNotFoundError, match="not found"):
        Workspace.open(home, project="missing")


def test_sole_project_auto_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "solo", set_active=True)
    # clear sticky project from home config to force sole-project fallback
    (home / "config.json").write_text('{"version": 2}\n', encoding="utf-8")
    monkeypatch.delenv("ROI_H_PROJECT", raising=False)
    assert Workspace.open(home).project == "solo"


def test_roi_h_home_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "elsewhere"
    create_project(home, "x", set_active=True)
    monkeypatch.setenv("ROI_H_HOME", str(home))
    monkeypatch.delenv("ROI_H_PROJECT", raising=False)
    ws = Workspace.open()
    assert ws.root == home.resolve()
    assert ws.project == "x"


def test_default_home_is_user_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROI_H_HOME", raising=False)

    assert resolve_home() == (Path.home() / ".roi-h").resolve()
    assert resolve_home() != (tmp_path / ".roi-h").resolve()


def test_same_run_id_cannot_be_mutated_concurrently(tmp_path: Path) -> None:
    home = tmp_path / ".roi-h"
    create_project(home, "lease", set_active=True)
    workspace = Workspace.open(home, project="lease")

    with (
        run_lease(workspace, "same-run"),
        pytest.raises(RuntimeError, match="run lease is busy"),
        run_lease(workspace, "same-run"),
    ):
        pytest.fail("second lease unexpectedly acquired")

    with run_lease(workspace, "same-run"):
        pass
