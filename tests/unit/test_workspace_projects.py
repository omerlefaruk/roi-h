"""Multi-project workspace registry and resolution."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from roi_h import cli as cli_module
from roi_h.harness import workspace as workspace_module
from roi_h.harness.lease import run_lease
from roi_h.harness.workspace import (
    Workspace,
    configure_project,
    create_project,
    get_active_project,
    init_project,
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
    assert json.loads((project / "project.json").read_text())["retention"]["log_retention"] == "7d"
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


def test_init_project_selects_existing_project_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_project(home, "alpha")
    create_project(home, "beta", set_active=False)

    result = init_project(home, "beta", env="prod", log_retention="30d")

    assert result["created"] is False
    assert result["project"] == "beta"
    assert result["environment"] == "prod"
    assert get_active_project(home) == "beta"
    manifest = json.loads((home / "projects" / "beta" / "project.json").read_text())
    assert manifest["retention"]["log_retention"] == "30d"
    with pytest.raises(FileNotFoundError, match=r"project create missing"):
        init_project(home, "missing")


def test_init_project_infers_only_exact_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    create_project(home, "demo")
    project = home / "projects" / "demo"
    monkeypatch.chdir(project)
    assert init_project(home)["project"] == "demo"

    nested = project / "reference"
    monkeypatch.chdir(nested)
    with pytest.raises(ValueError, match="project name is required"):
        init_project(home)


def test_project_open_uses_the_managed_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    create_project(home, "demo")
    opened: list[Path] = []
    monkeypatch.setattr(cli_module, "_open_directory", opened.append)

    result = cli_module._cmd_project_open(Namespace(home=str(home), name="demo", env="dev"))

    assert result["opened"] == "project://"
    assert opened == [(home / "projects" / "demo").resolve()]


def test_project_retention_configuration_is_validated(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_project(home, "demo", log_retention="forever")
    assert configure_project(home, "demo", log_retention="3d")["log_retention"] == "3d"

    for invalid in ("0d", "1000000000d"):
        with pytest.raises(ValueError, match="log retention"):
            configure_project(home, "demo", log_retention=invalid)


def test_init_project_repairs_only_missing_supported_directories(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_project(home, "demo")
    reference = home / "projects" / "demo" / "reference"
    reference.rmdir()
    init_project(home, "demo")
    assert reference.is_dir()

    reference.rmdir()
    reference.write_text("blocked", encoding="utf-8")
    with pytest.raises(FileExistsError):
        init_project(home, "demo")


def test_project_create_rejects_symlinked_projects_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    (home / "projects").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match=r"path\.escape_denied"):
        create_project(home, "demo")
    assert not (outside / "demo").exists()


def test_project_open_rejects_project_symlink_outside_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_project(home, "demo")
    outside = tmp_path / "outside"
    project = home / "projects" / "demo"
    project.replace(outside)
    project.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="non-symlink"):
        init_project(home, "demo")


def test_project_open_rejects_child_symlink_outside_project(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_project(home, "demo")
    project = home / "projects" / "demo"
    outside = tmp_path / "outside"
    outside.mkdir()
    runs = project / "environments" / "dev" / "runs"
    runs.rmdir()
    runs.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match=r"path\.escape_denied"):
        init_project(home, "demo")


def test_workspace_open_does_not_require_home_write_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    create_project(home, "demo")
    monkeypatch.setattr(workspace_module.os, "access", lambda *_args: False)

    assert Workspace.open(home).project == "demo"


def test_project_home_access_error_is_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        workspace_module.os,
        "access",
        lambda path, _mode: Path(path) != home,
    )

    with pytest.raises(
        PermissionError,
        match=r"ROI-H cannot write to data home .*Do not run ROI-H with sudo",
    ):
        create_project(home, "demo")


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
