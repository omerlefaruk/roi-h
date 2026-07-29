import json
from pathlib import Path

import pytest

import roi_h_installer
from roi_h_installer import (
    DataState,
    InstallPlan,
    InstallResult,
    PointerState,
    StagingState,
)
from roi_h_installer.cli import main

_INVALID_REQUEST_EXIT = 2
_INTERRUPTED_EXIT = 130


def test_inspect_emits_one_json_installation_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    monkeypatch.setenv("ROI_H_INSTALL_ROOT", str(install_root))
    monkeypatch.setenv("ROI_H_HOME", str(data_home))

    exit_code = main(["inspect", "--output", "json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "schema_version": "1.0",
        "install_root": str(install_root),
        "data_home": str(data_home),
        "managed": False,
        "active_version": None,
        "installed_versions": [],
        "pointer_state": "missing",
        "data_state": "missing",
        "staging_state": "none",
    }


def test_install_plans_and_applies_selected_trusted_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    release_description = tmp_path / "release.json"
    release_description.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": "0.1.0",
                "channel": "stable",
                "python_version": "3.12.13",
                "browser_revision": "chromium-1234",
                "application_target": "roi_h-0.1.0-py3-none-any.whl",
                "data_compatibility": {
                    "readable_home_layouts": [1],
                    "writable_home_layout": 1,
                    "activegraph_version": "1.10.0",
                },
                "targets": [
                    {
                        "name": "roi_h-0.1.0-py3-none-any.whl",
                        "length": 4096,
                        "sha256": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured_plan: InstallPlan | None = None

    def apply_success(install_plan: InstallPlan) -> InstallResult:
        nonlocal captured_plan
        captured_plan = install_plan
        return InstallResult(
            plan_id=install_plan.plan_id,
            request_id=install_plan.request_id,
            transaction_id=install_plan.transaction_id,
            operation=install_plan.operation,
            installed_version=install_plan.requested_version,
            requested_version=install_plan.requested_version,
            selected_channel=install_plan.selected_channel,
            changed=True,
            pointer_state=PointerState.ACTIVE,
            data_state=DataState.MISSING,
            staging_state=StagingState.NONE,
            retryable=False,
            diagnostic_id=None,
            recovery_action=None,
        )

    monkeypatch.setattr(roi_h_installer, "apply", apply_success)

    exit_code = main(
        [
            "install",
            "--release-description",
            str(release_description),
            "--install-root",
            str(install_root),
            "--data-home",
            str(data_home),
            "--channel",
            "stable",
            "--version",
            "0.1.0",
            "--output",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert captured_plan is not None
    assert captured_plan.install_root == install_root
    assert captured_plan.data_home == data_home
    assert captured_plan.requested_version == "0.1.0"
    assert payload["schema_version"] == "1.0"
    assert payload["installed_version"] == "0.1.0"
    assert payload["selected_channel"] == "stable"
    assert payload["changed"] is True


def test_install_emits_structured_domain_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    release_description = tmp_path / "release.json"
    release_description.write_text("{}", encoding="utf-8")

    exit_code = main(
        [
            "install",
            "--release-description",
            str(release_description),
            "--install-root",
            str(tmp_path / "install"),
            "--data-home",
            str(tmp_path / "data"),
            "--channel",
            "stable",
            "--output",
            "json",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "code": "release.metadata_untrusted",
        "category": "validation",
        "message": "The local release description is not trusted release metadata.",
        "retryable": False,
        "diagnostic_id": None,
        "recovery_action": ("Use a valid description from the local trusted release repository."),
    }


def test_install_rejects_invalid_request_with_stable_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "install",
            "--release-description",
            str(tmp_path / "release.json"),
            "--install-root",
            str(tmp_path / "install"),
            "--data-home",
            str(tmp_path / "data"),
            "--version",
            "latest",
            "--output",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == _INVALID_REQUEST_EXIT
    assert captured.err == ""
    assert payload["code"] == "release.version_not_found"
    assert payload["category"] == "validation"
    assert payload["retryable"] is False
    assert payload["recovery_action"]


def test_install_rejects_invalid_channel_with_stable_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "install",
            "--release-description",
            str(tmp_path / "release.json"),
            "--install-root",
            str(tmp_path / "install"),
            "--data-home",
            str(tmp_path / "data"),
            "--channel",
            "STABLE",
            "--output",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == _INVALID_REQUEST_EXIT
    assert captured.err == ""
    assert payload["code"] == "release.channel_not_found"
    assert payload["category"] == "validation"
    assert payload["retryable"] is False
    assert payload["recovery_action"]


def test_unknown_command_emits_structured_usage_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["upgrade", "--output", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == _INVALID_REQUEST_EXIT
    assert captured.err == ""
    assert payload["code"] == "install.operation_not_supported"
    assert payload["category"] == "validation"
    assert payload["retryable"] is False
    assert payload["recovery_action"]


def test_install_returns_interrupt_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    release_description = tmp_path / "release.json"
    release_description.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": "0.1.0",
                "channel": "stable",
                "python_version": "3.12.13",
                "browser_revision": "chromium-1234",
                "application_target": "roi_h-0.1.0-py3-none-any.whl",
                "data_compatibility": {
                    "readable_home_layouts": [1],
                    "writable_home_layout": 1,
                    "activegraph_version": "1.10.0",
                },
                "targets": [
                    {
                        "name": "roi_h-0.1.0-py3-none-any.whl",
                        "length": 4096,
                        "sha256": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def interrupt(_install_plan: InstallPlan) -> InstallResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(roi_h_installer, "apply", interrupt)

    exit_code = main(
        [
            "install",
            "--release-description",
            str(release_description),
            "--install-root",
            str(tmp_path / "install"),
            "--data-home",
            str(tmp_path / "data"),
            "--output",
            "json",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == _INTERRUPTED_EXIT
    assert captured.err == ""
    assert json.loads(captured.out)["category"] == "internal"


def test_update_constructs_update_request_and_applies_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    release_description = tmp_path / "release.json"
    release_description.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": "0.2.0",
                "channel": "stable",
                "python_version": "3.12.13",
                "browser_revision": "chromium-1234",
                "application_target": "roi_h-0.2.0-py3-none-any.whl",
                "data_compatibility": {
                    "readable_home_layouts": [1],
                    "writable_home_layout": 1,
                    "activegraph_version": "1.10.0",
                },
                "targets": [
                    {
                        "name": "roi_h-0.2.0-py3-none-any.whl",
                        "length": 4096,
                        "sha256": "b" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    initial_install_plan = roi_h_installer.plan
    captured_operation = None

    def plan_update(request: roi_h_installer.InstallRequest) -> InstallPlan:
        nonlocal captured_operation
        captured_operation = request.operation
        plan = initial_install_plan(
            request.model_copy(update={"operation": roi_h_installer.InstallOperation.INSTALL})
        )
        return plan.model_copy(update={"operation": request.operation})

    def apply_update(install_plan: InstallPlan) -> InstallResult:
        return InstallResult(
            plan_id=install_plan.plan_id,
            request_id=install_plan.request_id,
            transaction_id=install_plan.transaction_id,
            operation=install_plan.operation,
            installed_version=install_plan.requested_version,
            requested_version=install_plan.requested_version,
            selected_channel=install_plan.selected_channel,
            changed=True,
            pointer_state=PointerState.ACTIVE,
            data_state=DataState.MISSING,
            staging_state=StagingState.NONE,
            retryable=False,
            diagnostic_id=None,
            recovery_action=None,
        )

    monkeypatch.setattr(roi_h_installer, "plan", plan_update)
    monkeypatch.setattr(roi_h_installer, "apply", apply_update)

    exit_code = main(
        [
            "update",
            "--release-description",
            str(release_description),
            "--install-root",
            str(install_root),
            "--data-home",
            str(data_home),
            "--channel",
            "stable",
            "--version",
            "0.2.0",
            "--output",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert captured_operation is roi_h_installer.InstallOperation.UPDATE
    assert payload["operation"] == "update"
    assert payload["installed_version"] == "0.2.0"
