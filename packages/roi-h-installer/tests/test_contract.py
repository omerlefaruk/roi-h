import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from roi_h_installer import (
    DataState,
    EffectKind,
    InstallerError,
    InstallerErrorCode,
    InstallOperation,
    InstallRequest,
    PointerState,
    RequirementKind,
    StagingState,
    inspect,
    plan,
)


def test_inspect_reports_missing_explicit_install_root_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "managed-install"
    data_home = tmp_path / "user-data"
    monkeypatch.setenv("ROI_H_INSTALL_ROOT", str(install_root))
    monkeypatch.setenv("ROI_H_HOME", str(data_home))

    state = inspect()

    assert state.schema_version == "1.0"
    assert state.install_root == install_root
    assert state.data_home == data_home
    assert state.managed is False
    assert state.active_version is None
    assert state.installed_versions == ()
    assert state.pointer_state is PointerState.MISSING
    assert state.data_state is DataState.MISSING
    assert state.staging_state is StagingState.NONE
    assert not install_root.exists()
    assert not data_home.exists()


def test_request_model_rejects_coercion_and_unknown_fields(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        InstallRequest.model_validate(
            {
                "request_id": "req_install_001",
                "operation": "install",
                "install_root": str(tmp_path / "install"),
                "data_home": tmp_path / "data",
                "channel": "stable",
                "release_description": tmp_path / "release.json",
                "unexpected": True,
            }
        )


def test_plan_initial_install_from_local_trusted_release_without_writing(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "managed-install"
    data_home = tmp_path / "user-data"
    description = tmp_path / "release-0.1.0.json"
    description.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": "0.1.0",
                "channel": "stable",
                "python_version": "3.12.13",
                "browser_revision": "chromium-1234",
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
                    },
                    {
                        "name": "roi_h-runtime-0.1.0.lock",
                        "length": 2048,
                        "sha256": "b" * 64,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    request = InstallRequest(
        request_id="req_install_001",
        operation=InstallOperation.INSTALL,
        install_root=install_root,
        data_home=data_home,
        channel="stable",
        version=None,
        release_description=description,
    )

    install_plan = plan(request)

    assert install_plan.schema_version == "1.0"
    assert install_plan.request_id == "req_install_001"
    assert install_plan.operation is InstallOperation.INSTALL
    assert install_plan.current_version is None
    assert install_plan.requested_version == "0.1.0"
    assert install_plan.selected_channel == "stable"
    assert install_plan.changed is True
    assert install_plan.network_required is False
    assert install_plan.selected_release.targets[0].sha256 == "a" * 64
    assert install_plan.selected_release.targets[1].sha256 == "b" * 64
    assert {requirement.kind for requirement in install_plan.requirements} == {
        RequirementKind.FILE,
        RequirementKind.PLATFORM,
    }
    assert {effect.kind for effect in install_plan.effects} == {
        EffectKind.CREATE_INSTALL_ROOT,
        EffectKind.CREATE_VERSION,
        EffectKind.INSTALL_LAUNCHER,
        EffectKind.ACTIVATE_VERSION,
    }
    assert install_plan.staging_rule == "stage_before_activation"
    assert install_plan.activation_rule == "activate_after_staged_doctor"
    assert install_plan.recovery_steps
    assert not install_root.exists()
    assert not data_home.exists()


def test_plan_returns_stable_error_for_invalid_local_release(tmp_path: Path) -> None:
    description = tmp_path / "release.json"
    description.write_text("{}", encoding="utf-8")
    request = InstallRequest(
        request_id="req_invalid_release",
        operation=InstallOperation.INSTALL,
        install_root=tmp_path / "install",
        data_home=tmp_path / "data",
        channel="stable",
        release_description=description,
    )

    with pytest.raises(InstallerError) as captured:
        plan(request)

    assert captured.value.failure.code is InstallerErrorCode.RELEASE_METADATA_UNTRUSTED
    assert captured.value.failure.retryable is False
    assert captured.value.failure.recovery_action
    assert not request.install_root.exists()
    assert not request.data_home.exists()
