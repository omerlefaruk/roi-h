import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

import roi_h_installer.core as installer_core
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
    apply,
    inspect,
    plan,
)

_TEST_VERSION = "0.1.0"
_ACTIVE_DOCTOR_CALL = 3


class _SimulatedDoctorError(RuntimeError):
    pass


def _build_test_roi_h_wheel(
    wheelhouse: Path,
    *,
    version: str = _TEST_VERSION,
    doctor_ok: bool = True,
    fail_when_active: bool = False,
) -> Path:
    wheelhouse.mkdir()
    wheel = wheelhouse / f"roi_h-{version}-py3-none-any.whl"
    active_check = (
        "        current = Path(os.environ['ROI_H_INSTALL_ROOT']) / 'current'\n"
        f"        active = (current.read_text().strip() == {version!r} if current.is_file() "
        "else current.exists() and current.resolve() == Path(sys.prefix).resolve())\n"
        "        ok = not active\n"
        if fail_when_active
        else f"        ok = {doctor_ok!r}\n"
    )
    files = {
        "roi_h_test/__init__.py": f'__version__ = "{version}"\n'.encode(),
        "roi_h_test/cli.py": (
            "import json\n"
            "import os\n"
            "import sys\n\n"
            "from pathlib import Path\n\n"
            "def main():\n"
            '    if sys.argv[1:] == ["doctor", "--output", "json"]:\n'
            f"{active_check}"
            f"        print(json.dumps({{'ok': ok, 'version': '{version}'}}))\n"
            "        return 0\n"
            '    if sys.argv[1:] == ["instructions", "--install", "--output", "json"]:\n'
            "        marker = Path(os.environ['ROI_H_INSTALL_ROOT']) / "
            "'agent-instructions-installed'\n"
            f"        marker.write_text({version!r}, encoding='utf-8')\n"
            "        print(json.dumps({'ok': True, 'changed': True}))\n"
            "        return 0\n"
            "    if Path(sys.argv[0]).name == 'playwright' and "
            "sys.argv[1:] == ['install', 'chromium']:\n"
            "        browser_root = Path(os.environ['PLAYWRIGHT_BROWSERS_PATH'])\n"
            "        marker = browser_root / os.environ['ROI_H_BROWSER_REVISION'] / 'READY'\n"
            "        marker.parent.mkdir(parents=True, exist_ok=True)\n"
            "        marker.write_text('ready', encoding='utf-8')\n"
            "        return 0\n"
            "    return 2\n"
        ).encode(),
        f"roi_h-{version}.dist-info/METADATA": (
            "Metadata-Version: 2.4\n"
            "Name: roi-h\n"
            f"Version: {version}\n"
            "Requires-Python: >=3.12,<3.13\n"
        ).encode(),
        f"roi_h-{version}.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Generator: roi-h-installer-test\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        ),
        f"roi_h-{version}.dist-info/entry_points.txt": (
            b"[console_scripts]\nplaywright = roi_h_test.cli:main\nroi-h = roi_h_test.cli:main\n"
        ),
    }
    record_path = f"roi_h-{version}.dist-info/RECORD"
    records = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        records.append(f"{name},sha256={digest},{len(content)}")
    records.append(f"{record_path},,")
    files[record_path] = ("\n".join(records) + "\n").encode()
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return wheel


def _make_release_request(  # noqa: PLR0913
    tmp_path: Path,
    install_root: Path,
    data_home: Path,
    *,
    version: str,
    operation: InstallOperation,
    request_id: str,
    fail_when_active: bool = False,
) -> tuple[InstallRequest, Path]:
    wheelhouse = tmp_path / f"wheelhouse-{request_id}"
    wheel = _build_test_roi_h_wheel(
        wheelhouse,
        version=version,
        fail_when_active=fail_when_active,
    )
    wheel_bytes = wheel.read_bytes()
    description = wheelhouse / "release.json"
    description.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": version,
                "channel": "stable",
                "python_version": platform.python_version(),
                "browser_revision": f"chromium-{version}",
                "application_target": wheel.name,
                "data_compatibility": {
                    "readable_home_layouts": [1],
                    "writable_home_layout": 1,
                    "activegraph_version": "1.10.0",
                },
                "targets": [
                    {
                        "name": wheel.name,
                        "length": len(wheel_bytes),
                        "sha256": hashlib.sha256(wheel_bytes).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return (
        InstallRequest(
            request_id=request_id,
            operation=operation,
            install_root=install_root,
            data_home=data_home,
            channel="stable",
            release_description=description,
        ),
        wheel,
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
    assert install_plan.network_required is True
    assert install_plan.selected_release.targets[0].sha256 == "a" * 64
    assert install_plan.selected_release.targets[1].sha256 == "b" * 64
    assert {requirement.kind for requirement in install_plan.requirements} == {
        RequirementKind.FILE,
        RequirementKind.NETWORK,
        RequirementKind.PLATFORM,
    }
    assert {effect.kind for effect in install_plan.effects} == {
        EffectKind.CREATE_INSTALL_ROOT,
        EffectKind.CREATE_VERSION,
        EffectKind.INSTALL_BROWSER,
        EffectKind.INSTALL_AGENT_INSTRUCTIONS,
        EffectKind.INSTALL_AGENT_SKILL,
        EffectKind.INSTALL_LAUNCHER,
        EffectKind.ACTIVATE_VERSION,
    }
    user_home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME", user_home / ".codex")).expanduser()
    instruction_targets = {codex_home / "AGENTS.md", user_home / ".agents" / "AGENTS.md"}
    skill_targets = {
        root / "skills" / "migrate-code-automation" / relative
        for root in (codex_home, user_home / ".agents")
        for relative in ("SKILL.md", "agents/openai.yaml")
    }
    assert {
        Path(effect.target)
        for effect in install_plan.effects
        if effect.kind is EffectKind.INSTALL_AGENT_INSTRUCTIONS
    } == instruction_targets
    assert {
        Path(effect.target)
        for effect in install_plan.effects
        if effect.kind is EffectKind.INSTALL_AGENT_SKILL
    } == skill_targets
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


def test_apply_initial_install_activates_only_after_offline_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheel = _build_test_roi_h_wheel(wheelhouse)
    wheel_bytes = wheel.read_bytes()
    description = wheelhouse / "release.json"
    description.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": _TEST_VERSION,
                "channel": "stable",
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}."
                f"{sys.version_info.micro}",
                "browser_revision": "chromium-test",
                "application_target": wheel.name,
                "data_compatibility": {
                    "readable_home_layouts": [1],
                    "writable_home_layout": 1,
                    "activegraph_version": "1.10.0",
                },
                "targets": [
                    {
                        "name": wheel.name,
                        "length": len(wheel_bytes),
                        "sha256": hashlib.sha256(wheel_bytes).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    data_home.mkdir()
    marker = data_home / "preserve.txt"
    marker.write_text("keep", encoding="utf-8")
    request = InstallRequest(
        request_id="req_apply_initial",
        operation=InstallOperation.INSTALL,
        install_root=install_root,
        data_home=data_home,
        channel="stable",
        release_description=description,
    )

    result = apply(plan(request))

    assert result.changed is True
    assert result.installed_version == _TEST_VERSION
    assert result.pointer_state is PointerState.ACTIVE
    assert result.data_state is DataState.PRESENT
    assert result.staging_state is StagingState.NONE
    assert marker.read_text(encoding="utf-8") == "keep"
    monkeypatch.setenv("ROI_H_INSTALL_ROOT", str(install_root))
    monkeypatch.setenv("ROI_H_HOME", str(data_home))
    state = inspect()
    assert state.managed is True
    assert state.active_version == _TEST_VERSION
    assert state.installed_versions == (_TEST_VERSION,)
    assert state.staging_state is StagingState.NONE
    active_cli = (
        install_root / "versions" / _TEST_VERSION / "Scripts" / "roi-h.exe"
        if sys.platform == "win32"
        else install_root / "current" / "bin" / "roi-h"
    )
    doctor = subprocess.run(  # noqa: S603
        [active_cli, "doctor", "--output", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(doctor.stdout) == {"ok": True, "version": _TEST_VERSION}
    saved_state = json.loads((install_root / "install-state.json").read_text(encoding="utf-8"))
    assert saved_state["schema_version"] == 1
    assert saved_state["active_version"] == _TEST_VERSION
    assert saved_state["transaction_id"] == result.transaction_id
    assert (install_root / "browsers" / "chromium-test" / "READY").read_text(
        encoding="utf-8"
    ) == "ready"
    assert (install_root / "agent-instructions-installed").read_text(
        encoding="utf-8"
    ) == _TEST_VERSION
    record = install_root / "transactions" / f"{result.transaction_id}.json"
    assert json.loads(record.read_text(encoding="utf-8"))["status"] == "complete"


def test_apply_rejects_changed_target_bytes_and_records_failure(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheel = _build_test_roi_h_wheel(wheelhouse)
    wheel_bytes = wheel.read_bytes()
    description = wheelhouse / "release.json"
    description.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": _TEST_VERSION,
                "channel": "stable",
                "python_version": platform.python_version(),
                "browser_revision": "chromium-test",
                "application_target": wheel.name,
                "data_compatibility": {
                    "readable_home_layouts": [1],
                    "writable_home_layout": 1,
                    "activegraph_version": "1.10.0",
                },
                "targets": [
                    {
                        "name": wheel.name,
                        "length": len(wheel_bytes),
                        "sha256": hashlib.sha256(wheel_bytes).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    request = InstallRequest(
        request_id="req_changed_target",
        operation=InstallOperation.INSTALL,
        install_root=tmp_path / "install",
        data_home=tmp_path / "data",
        channel="stable",
        release_description=description,
    )
    install_plan = plan(request)
    wheel.write_bytes(wheel_bytes + b"changed")

    with pytest.raises(InstallerError) as captured:
        apply(install_plan)

    assert captured.value.failure.code is InstallerErrorCode.RELEASE_TARGET_VERIFICATION_FAILED
    assert not (request.install_root / "current").exists()
    assert not (request.install_root / "versions" / _TEST_VERSION).exists()
    assert not (
        request.install_root / "transactions" / f"{install_plan.transaction_id}.staging"
    ).exists()
    record = request.install_root / "transactions" / f"{install_plan.transaction_id}.json"
    saved = json.loads(record.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["failure"]["code"] == "release.target_verification_failed"


def test_apply_cleans_staging_when_doctor_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheel = _build_test_roi_h_wheel(wheelhouse, doctor_ok=False)
    wheel_bytes = wheel.read_bytes()
    description = wheelhouse / "release.json"
    description.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": _TEST_VERSION,
                "channel": "stable",
                "python_version": platform.python_version(),
                "browser_revision": "chromium-test",
                "application_target": wheel.name,
                "data_compatibility": {
                    "readable_home_layouts": [1],
                    "writable_home_layout": 1,
                    "activegraph_version": "1.10.0",
                },
                "targets": [
                    {
                        "name": wheel.name,
                        "length": len(wheel_bytes),
                        "sha256": hashlib.sha256(wheel_bytes).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    data_home.mkdir()
    marker = data_home / "preserve.txt"
    marker.write_text("keep", encoding="utf-8")
    request = InstallRequest(
        request_id="req_bad_doctor",
        operation=InstallOperation.INSTALL,
        install_root=install_root,
        data_home=data_home,
        channel="stable",
        release_description=description,
    )
    install_plan = plan(request)

    with pytest.raises(InstallerError) as captured:
        apply(install_plan)

    assert captured.value.failure.code is InstallerErrorCode.DOCTOR_FAILED
    assert marker.read_text(encoding="utf-8") == "keep"
    monkeypatch.setenv("ROI_H_INSTALL_ROOT", str(install_root))
    monkeypatch.setenv("ROI_H_HOME", str(data_home))
    state = inspect()
    assert state.managed is False
    assert state.active_version is None
    assert state.installed_versions == ()
    assert state.staging_state is StagingState.NONE
    record = install_root / "transactions" / f"{install_plan.transaction_id}.json"
    saved = json.loads(record.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["failure"]["code"] == "doctor.failed"


def test_managed_update_activates_new_version_and_retains_old_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    data_home.mkdir()
    marker = data_home / "preserve.txt"
    marker.write_text("keep", encoding="utf-8")
    wheelhouse_a = tmp_path / "wheelhouse-a"
    wheel_a = _build_test_roi_h_wheel(wheelhouse_a, version="0.1.0")
    bytes_a = wheel_a.read_bytes()
    description_a = wheelhouse_a / "release.json"
    description_a.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": "0.1.0",
                "channel": "stable",
                "python_version": platform.python_version(),
                "browser_revision": "chromium-a",
                "application_target": wheel_a.name,
                "data_compatibility": {
                    "readable_home_layouts": [1],
                    "writable_home_layout": 1,
                    "activegraph_version": "1.10.0",
                },
                "targets": [
                    {
                        "name": wheel_a.name,
                        "length": len(bytes_a),
                        "sha256": hashlib.sha256(bytes_a).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    apply(
        plan(
            InstallRequest(
                request_id="req_install_a",
                operation=InstallOperation.INSTALL,
                install_root=install_root,
                data_home=data_home,
                channel="stable",
                release_description=description_a,
            )
        )
    )
    wheelhouse_b = tmp_path / "wheelhouse-b"
    wheel_b = _build_test_roi_h_wheel(wheelhouse_b, version="0.2.0")
    bytes_b = wheel_b.read_bytes()
    description_b = wheelhouse_b / "release.json"
    description_b.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": "0.2.0",
                "channel": "stable",
                "python_version": platform.python_version(),
                "browser_revision": "chromium-b",
                "application_target": wheel_b.name,
                "data_compatibility": {
                    "readable_home_layouts": [1],
                    "writable_home_layout": 1,
                    "activegraph_version": "1.10.0",
                },
                "targets": [
                    {
                        "name": wheel_b.name,
                        "length": len(bytes_b),
                        "sha256": hashlib.sha256(bytes_b).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    update_plan = plan(
        InstallRequest(
            request_id="req_update_b",
            operation=InstallOperation.UPDATE,
            install_root=install_root,
            data_home=data_home,
            channel="stable",
            release_description=description_b,
        )
    )

    assert update_plan.current_version == "0.1.0"
    assert update_plan.requested_version == "0.2.0"
    assert update_plan.changed is True
    result = apply(update_plan)

    assert result.changed is True
    assert result.installed_version == "0.2.0"
    monkeypatch.setenv("ROI_H_INSTALL_ROOT", str(install_root))
    monkeypatch.setenv("ROI_H_HOME", str(data_home))
    state = inspect()
    assert state.active_version == "0.2.0"
    assert state.installed_versions == ("0.1.0", "0.2.0")
    assert marker.read_text(encoding="utf-8") == "keep"
    saved_state = json.loads((install_root / "install-state.json").read_text(encoding="utf-8"))
    assert saved_state["active_version"] == "0.2.0"
    assert (install_root / "agent-instructions-installed").read_text(encoding="utf-8") == "0.2.0"


def test_same_selected_version_returns_no_change_without_reinstall(tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    install_request, _ = _make_release_request(
        tmp_path,
        install_root,
        data_home,
        version="0.1.0",
        operation=InstallOperation.INSTALL,
        request_id="install_same",
    )
    first_result = apply(plan(install_request))
    same_request, same_wheel = _make_release_request(
        tmp_path,
        install_root,
        data_home,
        version="0.1.0",
        operation=InstallOperation.UPDATE,
        request_id="update_same",
    )
    same_plan = plan(same_request)
    transactions_before = {path.name for path in (install_root / "transactions").iterdir()}
    same_wheel.unlink()

    result = apply(same_plan)

    assert first_result.changed is True
    assert same_plan.current_version == "0.1.0"
    assert same_plan.changed is False
    assert same_plan.effects == ()
    assert result.changed is False
    assert result.installed_version == "0.1.0"
    assert {path.name for path in (install_root / "transactions").iterdir()} == transactions_before


def test_update_target_failure_keeps_previous_version_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    data_home.mkdir()
    marker = data_home / "preserve.txt"
    marker.write_text("keep", encoding="utf-8")
    install_request, _ = _make_release_request(
        tmp_path,
        install_root,
        data_home,
        version="0.1.0",
        operation=InstallOperation.INSTALL,
        request_id="install_before_bad_target",
    )
    apply(plan(install_request))
    previous_state = (install_root / "install-state.json").read_bytes()
    update_request, update_wheel = _make_release_request(
        tmp_path,
        install_root,
        data_home,
        version="0.2.0",
        operation=InstallOperation.UPDATE,
        request_id="update_bad_target",
    )
    update_plan = plan(update_request)
    update_wheel.write_bytes(update_wheel.read_bytes() + b"changed")

    with pytest.raises(InstallerError) as captured:
        apply(update_plan)

    assert captured.value.failure.code is InstallerErrorCode.RELEASE_TARGET_VERIFICATION_FAILED
    monkeypatch.setenv("ROI_H_INSTALL_ROOT", str(install_root))
    monkeypatch.setenv("ROI_H_HOME", str(data_home))
    state = inspect()
    assert state.active_version == "0.1.0"
    assert state.installed_versions == ("0.1.0",)
    assert (install_root / "install-state.json").read_bytes() == previous_state
    assert marker.read_text(encoding="utf-8") == "keep"
    record = install_root / "transactions" / f"{update_plan.transaction_id}.json"
    assert json.loads(record.read_text(encoding="utf-8"))["status"] == "failed"


def test_post_activation_doctor_failure_restores_previous_pointer_and_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    data_home.mkdir()
    marker = data_home / "preserve.txt"
    marker.write_text("keep", encoding="utf-8")
    install_request, _ = _make_release_request(
        tmp_path,
        install_root,
        data_home,
        version="0.1.0",
        operation=InstallOperation.INSTALL,
        request_id="install_before_bad_active_doctor",
    )
    apply(plan(install_request))
    previous_state = (install_root / "install-state.json").read_bytes()
    update_request, _ = _make_release_request(
        tmp_path,
        install_root,
        data_home,
        version="0.2.0",
        operation=InstallOperation.UPDATE,
        request_id="update_bad_active_doctor",
        fail_when_active=True,
    )
    update_plan = plan(update_request)

    with pytest.raises(InstallerError) as captured:
        apply(update_plan)

    assert captured.value.failure.code is InstallerErrorCode.DOCTOR_FAILED
    monkeypatch.setenv("ROI_H_INSTALL_ROOT", str(install_root))
    monkeypatch.setenv("ROI_H_HOME", str(data_home))
    state = inspect()
    assert state.active_version == "0.1.0"
    assert state.installed_versions == ("0.1.0",)
    assert state.pointer_state is PointerState.ACTIVE
    assert state.staging_state is StagingState.NONE
    assert (install_root / "install-state.json").read_bytes() == previous_state
    assert marker.read_text(encoding="utf-8") == "keep"
    active_cli = (
        install_root / "versions" / "0.1.0" / "Scripts" / "roi-h.exe"
        if sys.platform == "win32"
        else install_root / "current" / "bin" / "roi-h"
    )
    active_doctor = subprocess.run(  # noqa: S603
        [active_cli, "doctor", "--output", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(active_doctor.stdout) == {"ok": True, "version": "0.1.0"}
    record = install_root / "transactions" / f"{update_plan.transaction_id}.json"
    saved_record = json.loads(record.read_text(encoding="utf-8"))
    assert saved_record["status"] == "failed"
    assert saved_record["failure"]["code"] == "doctor.failed"


def test_agent_files_are_not_changed_when_the_transaction_record_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "install"
    install_request, _ = _make_release_request(
        tmp_path,
        install_root,
        tmp_path / "data",
        version="0.1.0",
        operation=InstallOperation.INSTALL,
        request_id="record_failure",
    )

    def create_environment(environment: Path) -> None:
        (environment / "Scripts").mkdir(parents=True)

    record_failed = False
    agent_install_called = False

    def fail_complete_record(path: Path, value: object) -> None:
        nonlocal record_failed
        if not record_failed and path.parent.name == "transactions":
            record_failed = True
            message = "record failed"
            raise OSError(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def install_agent_files(*_args: object) -> None:
        nonlocal agent_install_called
        agent_install_called = True

    monkeypatch.setattr(installer_core, "_is_windows", lambda: True)
    monkeypatch.setattr(installer_core, "_create_environment", create_environment)
    monkeypatch.setattr(installer_core, "_install_wheelhouse", lambda *_args: None)
    monkeypatch.setattr(installer_core, "_install_browser", lambda *_args: None)
    monkeypatch.setattr(installer_core, "_run_staged_doctor", lambda *_args: None)
    monkeypatch.setattr(installer_core, "_write_json_atomic", fail_complete_record)
    monkeypatch.setattr(installer_core, "_install_agent_instructions", install_agent_files)

    with pytest.raises(InstallerError):
        apply(plan(install_request))

    assert record_failed is True
    assert agent_install_called is False
    assert not (install_root / "versions" / "0.1.0").exists()


def test_windows_install_uses_an_atomic_pointer_file_without_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"
    install_request, _ = _make_release_request(
        tmp_path,
        install_root,
        data_home,
        version="0.1.0",
        operation=InstallOperation.INSTALL,
        request_id="windows_install",
    )

    def create_environment(environment: Path) -> None:
        scripts = environment / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "roi-h.exe").write_bytes(b"test")

    monkeypatch.setattr(installer_core, "_is_windows", lambda: True)
    monkeypatch.setattr(installer_core, "_create_environment", create_environment)
    monkeypatch.setattr(installer_core, "_install_wheelhouse", lambda *_args: None)
    monkeypatch.setattr(installer_core, "_install_browser", lambda *_args: None)
    monkeypatch.setattr(installer_core, "_run_staged_doctor", lambda *_args: None)
    monkeypatch.setattr(installer_core, "_install_agent_instructions", lambda *_args: None)

    result = apply(plan(install_request))

    pointer = install_root / "current"
    assert result.pointer_state is PointerState.ACTIVE
    assert pointer.is_file()
    assert not pointer.is_symlink()
    assert pointer.read_text(encoding="ascii") == "0.1.0\n"
    assert (install_root / "versions" / "0.1.0" / "Scripts" / "roi-h.exe").is_file()
    assert not (install_root / "transactions" / f"{result.transaction_id}.staging").exists()


def test_windows_update_failure_restores_pointer_file_and_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "install"
    data_home = tmp_path / "data"

    def create_environment(environment: Path) -> None:
        (environment / "Scripts").mkdir(parents=True)

    monkeypatch.setattr(installer_core, "_is_windows", lambda: True)
    monkeypatch.setattr(installer_core, "_create_environment", create_environment)
    monkeypatch.setattr(installer_core, "_install_wheelhouse", lambda *_args: None)
    monkeypatch.setattr(installer_core, "_install_browser", lambda *_args: None)
    monkeypatch.setattr(installer_core, "_run_staged_doctor", lambda *_args: None)
    monkeypatch.setattr(installer_core, "_install_agent_instructions", lambda *_args: None)
    install_request, _ = _make_release_request(
        tmp_path,
        install_root,
        data_home,
        version="0.1.0",
        operation=InstallOperation.INSTALL,
        request_id="windows_before_failed_update",
    )
    apply(plan(install_request))
    previous_state = (install_root / "install-state.json").read_bytes()
    update_request, _ = _make_release_request(
        tmp_path,
        install_root,
        data_home,
        version="0.2.0",
        operation=InstallOperation.UPDATE,
        request_id="windows_failed_update",
    )
    update_plan = plan(update_request)
    doctor_calls = 0

    def fail_after_activation(*_args: object) -> None:
        nonlocal doctor_calls
        doctor_calls += 1
        if doctor_calls == _ACTIVE_DOCTOR_CALL:
            raise _SimulatedDoctorError

    monkeypatch.setattr(installer_core, "_run_staged_doctor", fail_after_activation)

    with pytest.raises(InstallerError) as captured:
        apply(update_plan)

    assert captured.value.failure.code is InstallerErrorCode.UPDATE_ACTIVATION_FAILED
    assert (install_root / "current").read_text(encoding="ascii") == "0.1.0\n"
    assert (install_root / "install-state.json").read_bytes() == previous_state
    assert not (install_root / "versions" / "0.2.0").exists()
