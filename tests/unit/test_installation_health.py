from __future__ import annotations

import json
from pathlib import Path

from roi_h import installation
from roi_h.installation import (
    BUILT_IN_SKILLS,
    HealthCheck,
    InstallationHealthReport,
    inspect_installation_health,
    managed_browser_root,
)


def _make_skills(root: Path, *, omit: str | None = None) -> None:
    for name in BUILT_IN_SKILLS:
        if name == omit:
            continue
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")


def _checks_by_code(report: InstallationHealthReport) -> dict[str, HealthCheck]:
    return {check.code: check for check in report.checks}


def test_inspect_reports_a_healthy_managed_install(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    skills_root = tmp_path / "skills"
    install_root.mkdir()
    data_home.mkdir()
    _make_skills(skills_root)
    (install_root / "install-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_version": "0.1.0",
                "browser_revision": "chromium-1228",
            }
        ),
        encoding="utf-8",
    )
    (install_root / "current").write_text("versions/0.1.0\n", encoding="utf-8")
    browser_root = install_root / "browsers"
    (browser_root / "chromium-1228").mkdir(parents=True)
    monkeypatch.setattr(
        installation,
        "_probe_browser",
        lambda root, revision: {
            "browser_root": str(root),
            "executable": str(revision / "chrome"),
            "browser_version": "149.0",
            "playwright_version": "1.61.0",
        },
    )

    report = inspect_installation_health(
        install_root=install_root,
        data_home=data_home,
        application_version="0.1.0",
        python_version=(3, 12, 9),
        skills_root=skills_root,
    )

    assert report.schema_version == 1
    assert report.application_version == "0.1.0"
    assert report.python_version == "3.12.9"
    assert report.python_compatible is True
    assert report.managed_install_state == "managed"
    assert report.data_home_access == "read_write"
    assert report.built_in_skills == dict.fromkeys(BUILT_IN_SKILLS, True)
    assert report.healthy is True
    assert [check.code for check in report.checks] == [
        "application.version",
        "python.version",
        "skills.built_in",
        "data_home.access",
        "install.managed_state",
        "browser.launch",
        "runtime.socket_bootstrap",
        "runtime.tls_bootstrap",
    ]
    browser = _checks_by_code(report)["browser.launch"]
    assert browser.status == "pass"
    assert browser.details["browser_root"] == str(browser_root)
    assert _checks_by_code(report)["runtime.socket_bootstrap"].status == "pass"
    assert _checks_by_code(report)["runtime.tls_bootstrap"].status == "pass"


def test_inspect_does_not_create_a_missing_data_home(tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "missing-data-home"
    skills_root = tmp_path / "skills"
    _make_skills(skills_root)

    report = inspect_installation_health(
        install_root=install_root,
        data_home=data_home,
        application_version="0.1.0",
        python_version=(3, 12, 0),
        skills_root=skills_root,
    )

    assert not data_home.exists()
    assert report.data_home_access == "available_for_creation"
    assert _checks_by_code(report)["data_home.access"].status == "pass"
    assert report.managed_install_state == "unmanaged"


def test_inspect_reports_failures_without_mutation(tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    skills_root = tmp_path / "skills"
    install_root.mkdir()
    data_home.write_text("not a directory", encoding="utf-8")
    _make_skills(skills_root, omit="browser")
    (install_root / "install-state.json").write_text("{not-json", encoding="utf-8")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    report = inspect_installation_health(
        install_root=install_root,
        data_home=data_home,
        application_version="0.1.0",
        python_version=(3, 13, 0),
        skills_root=skills_root,
    )

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    checks = _checks_by_code(report)
    assert after == before
    assert report.healthy is False
    assert report.python_compatible is False
    assert report.built_in_skills["browser"] is False
    assert report.data_home_access == "unavailable"
    assert report.managed_install_state == "invalid"
    assert checks["python.version"].status == "fail"
    assert checks["skills.built_in"].status == "fail"
    assert checks["data_home.access"].status == "fail"
    assert checks["install.managed_state"].status == "fail"


def test_report_dictionary_is_strict_and_json_serializable(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _make_skills(skills_root)

    report = inspect_installation_health(
        install_root=tmp_path / "install",
        data_home=tmp_path / "data",
        application_version="2.4.1",
        python_version=(3, 12, 4),
        skills_root=skills_root,
    )

    payload = report.to_dict()
    assert set(payload) == {
        "schema_version",
        "application_version",
        "python_version",
        "python_compatible",
        "built_in_skills",
        "install_root",
        "data_home",
        "data_home_access",
        "managed_install_state",
        "healthy",
        "checks",
    }
    assert all(
        set(check) == {"code", "status", "message", "details"} for check in payload["checks"]
    )
    assert json.loads(json.dumps(payload)) == payload


def test_managed_install_fails_when_required_browser_is_missing(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    skills_root = tmp_path / "skills"
    install_root.mkdir()
    data_home.mkdir()
    _make_skills(skills_root)
    (install_root / "install-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_version": "0.1.0",
                "browser_revision": "chromium-1228",
            }
        ),
        encoding="utf-8",
    )
    (install_root / "current").write_text("versions/0.1.0\n", encoding="utf-8")

    report = inspect_installation_health(
        install_root=install_root,
        data_home=data_home,
        application_version="0.1.0",
        python_version=(3, 12, 13),
        skills_root=skills_root,
    )

    browser = _checks_by_code(report)["browser.launch"]
    assert report.healthy is False
    assert browser.status == "fail"
    assert browser.details == {
        "reason": "revision_missing",
        "revision": "chromium-1228",
    }


def test_managed_install_fails_when_browser_cannot_launch(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "install"
    skills_root = tmp_path / "skills"
    _make_skills(skills_root)
    (install_root / "browsers" / "chromium-1228").mkdir(parents=True)
    (install_root / "current").write_text("0.1.3\n", encoding="utf-8")
    (install_root / "install-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_version": "0.1.3",
                "browser_revision": "chromium-1228",
            }
        ),
        encoding="utf-8",
    )

    def fail_probe(*_args):
        message = "browser exited"
        raise RuntimeError(message)

    monkeypatch.setattr(installation, "_probe_browser", fail_probe)
    report = inspect_installation_health(
        install_root=install_root,
        data_home=tmp_path / "data",
        application_version="0.1.3",
        python_version=(3, 12, 0),
        skills_root=skills_root,
    )

    browser = _checks_by_code(report)["browser.launch"]
    assert report.healthy is False
    assert browser.status == "fail"
    assert browser.details == {
        "reason": "launch_failed",
        "revision": "chromium-1228",
        "error": "RuntimeError: browser exited",
    }


def test_managed_browser_root_follows_a_custom_install_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    install_root = tmp_path / "custom-install"

    assert managed_browser_root(install_root) == (install_root / "browsers").resolve()
