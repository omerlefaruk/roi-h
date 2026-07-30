"""Package-shape and transaction-scenario checks for issue #14."""

# ruff: noqa: PLR0913, PLR0915, PLR0917, S108, S314, S603

from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from roi_h.harness.macos_native_package_prototype import _rollback

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS package tools")

_PROBE_OUTPUT = "ROI-H managed Chromium prototype"


def _identity(path: Path) -> tuple[int, str]:
    content = path.read_bytes()
    return len(content), hashlib.sha256(content).hexdigest()


def _browser_archive(path: Path, marker: str) -> tuple[int, str]:
    script = f"#!/bin/sh\n# {marker}\nprintf '{_PROBE_OUTPUT}\\n'\n".encode()
    executable = tarfile.TarInfo("browser-probe")
    executable.size = len(script)
    executable.mode = 0o755
    framework = b"safe framework fixture\n"
    framework_file = tarfile.TarInfo("Frameworks/A/fixture.txt")
    framework_file.size = len(framework)
    framework_link = tarfile.TarInfo("Frameworks/Current")
    framework_link.type = tarfile.SYMTYPE
    framework_link.linkname = "A"
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(executable, io.BytesIO(script))
        archive.addfile(framework_file, io.BytesIO(framework))
        archive.addfile(framework_link)
    return _identity(path)


def _command(
    root: Path,
    package: Path,
    roi_h_home: Path,
    archive_a: Path,
    identity_a: tuple[int, str],
    archive_b: Path,
    identity_b: tuple[int, str],
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "roi_h.harness.macos_native_package_prototype",
        "--root",
        str(root),
        "--output-pkg",
        str(package),
        "--roi-h-home",
        str(roi_h_home),
        "--browser-a",
        str(archive_a),
        "--browser-a-bytes",
        str(identity_a[0]),
        "--browser-a-sha256",
        identity_a[1],
        "--browser-a-target",
        "chromium-A",
        "--browser-b",
        str(archive_b),
        "--browser-b-bytes",
        str(identity_b[0]),
        "--browser-b-sha256",
        identity_b[1],
        "--browser-b-target",
        "chromium-B",
    ]


def _run(
    command: list[str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    completed = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    return completed, json.loads(completed.stdout)


def test_package_shape_transaction_recovery_and_offline_rollback(tmp_path: Path) -> None:  # noqa: PLR0915
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    roi_h_home = tmp_path / "customer-data" / ".roi-h"
    (roi_h_home / "projects" / "customer").mkdir(parents=True)
    (roi_h_home / "projects" / "customer" / "records.json").write_bytes(b'{"kept":true}\n')
    (roi_h_home / "secret.bin").write_bytes(bytes(range(256)))
    home_before = {
        path.relative_to(roi_h_home).as_posix(): path.read_bytes()
        for path in roi_h_home.rglob("*")
        if path.is_file()
    }
    archive_a = tmp_path / "chromium-a.tar.gz"
    archive_b = tmp_path / "chromium-b.tar.gz"
    identity_a = _browser_archive(archive_a, "target A")
    identity_b = _browser_archive(archive_b, "target B")

    failed_command = _command(
        tmp_path / "failed-managed-root",
        tmp_path / "failed.pkg",
        roi_h_home,
        archive_a,
        identity_a,
        archive_b,
        (identity_b[0], "0" * 64),
    )
    failed, failure = _run(failed_command, unrelated_cwd)
    assert failed.returncode == 1
    assert failure["ok"] is False
    assert failure["evidence_scope"] == "package-shape-and-transaction-scenario"
    assert failure["error_type"] == "ValueError"
    assert "digest-bound file mismatch" in str(failure["error"])
    assert not (tmp_path / "failed-managed-root").exists()

    root = tmp_path / "managed-root"
    package = tmp_path / "roi-h-issue-14.pkg"
    completed, evidence = _run(
        _command(
            root,
            package,
            roi_h_home,
            archive_a,
            identity_a,
            archive_b,
            identity_b,
        ),
        unrelated_cwd,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout

    assert evidence["ok"] is True
    assert evidence["prototype"] == "Wayfinder issue #14"
    assert evidence["public_api"] is False
    assert evidence["evidence_scope"] == {
        "classification": "package-shape-and-transaction-scenario",
        "native_acceptance": False,
        "unproved": [
            "actual Installer execution",
            "actual locked ROI-H payload",
            "package notarization",
            "macOS 14 execution",
        ],
    }
    assert evidence["host"]["system"] == "Darwin"
    assert evidence["root"] == str(root)

    package_evidence = evidence["package"]
    assert package_evidence["path"] == str(package)
    assert package_evidence["format"] == "flat-product-pkg"
    assert package_evidence["signed"] is False
    assert package_evidence["signature"]["status"] == "unsigned"
    assert package_evidence["signature"]["confirmed"] is False
    assert package_evidence["notarized"] is False
    assert package_evidence["notarization_attempted"] is False
    assert package_evidence["bytes"] == package.stat().st_size
    assert package_evidence["sha256"] == _identity(package)[1]
    assert package_evidence["distribution"] == {
        "domain": "CurrentUserHomeDirectory",
        "enable_currentUserHome": True,
        "enable_localSystem": False,
        "enable_anywhere": False,
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "xml": package_evidence["distribution"]["xml"],
    }
    assert package_evidence["payload"]["version"] == "A"
    assert package_evidence["payload"]["bytes"] == 33
    assert package_evidence["payload"]["kind"] == "digest-bound-runtime-fixture"
    assert package_evidence["payload"]["actual_locked_roi_h_runtime"] is False
    assert (
        package_evidence["payload"]["sha256"]
        == hashlib.sha256(b"ROI-H locked runtime prototype A\n").hexdigest()
    )
    assert [item["command"][0] for item in package_evidence["commands"]] == [
        "pkgbuild",
        "productbuild",
    ]

    expanded = tmp_path / "expanded-package"
    expansion = subprocess.run(
        ["/usr/sbin/pkgutil", "--expand-full", str(package), str(expanded)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert expansion.returncode == 0, expansion.stderr
    distribution = ET.parse(expanded / "Distribution").getroot()
    domains = distribution.find("domains")
    options = distribution.find("options")
    minimum = distribution.find("./allowed-os-versions/os-version")
    assert domains is not None and domains.attrib == {
        "enable_anywhere": "false",
        "enable_currentUserHome": "true",
        "enable_localSystem": "false",
    }
    assert options is not None and options.attrib["hostArchitectures"] == "arm64"
    assert minimum is not None and minimum.attrib["min"] == "14.0"
    payloads = list(expanded.rglob("runtime.lock"))
    assert len(payloads) == 1
    assert (
        payloads[0]
        .relative_to(expanded)
        .as_posix()
        .endswith("Library/Application Support/ROI-H/versions/A/runtime.lock")
    )
    assert payloads[0].read_bytes() == b"ROI-H locked runtime prototype A\n"
    package_info = ET.parse(next(expanded.rglob("PackageInfo"))).getroot()
    assert package_info.attrib["identifier"] == "com.roih.wayfinder.issue14.prototype"
    assert package_info.attrib["version"] == "0.0.14"

    assert [item["event"] for item in evidence["events"]] == [
        "install_a",
        "interrupt_update_b",
        "restart_transaction_reconciliation",
        "interrupt_activation_b_after_pointer",
        "restart_activation_reconciliation",
        "tampered_runtime_activation_rejected",
        "offline_rollback_a",
    ]
    assert evidence["events"][0]["active_pointer"] == "versions/A"
    assert evidence["events"][1]["child_returncode"] == 73
    assert evidence["events"][1]["active_pointer"] == "versions/A"
    assert evidence["events"][2]["active_pointer"] == "versions/A"
    assert evidence["events"][3] == {
        "event": "interrupt_activation_b_after_pointer",
        "child_returncode": 74,
        "active_pointer": "versions/B",
        "state_active": "A",
    }
    assert evidence["events"][4]["active_pointer"] == "versions/B"
    assert evidence["events"][4]["state_active"] == "B"
    assert evidence["events"][5]["rejected"] is True
    assert evidence["events"][6]["active_pointer"] == "versions/A"

    recovery = evidence["recovery"]
    assert recovery["unowned_sentinel_preserved"] is True
    assert recovery["other_transaction_staging_preserved"] is True
    assert sorted(recovery["removed_only_transaction_owned"]) == [
        ".transactions/interrupted-update-B",
        "browsers/.staging-interrupted-update-B-chromium-B",
    ]
    assert not (root / "transaction.json").exists()
    assert not (root / "browsers" / ".staging-interrupted-update-B-chromium-B").exists()
    assert (root / ".transactions" / "other-transaction" / "keep.txt").read_text() == "keep\n"
    assert (
        root / "browsers" / ".staging-other-transaction-chromium-B" / "keep.txt"
    ).read_text() == "keep\n"
    assert (root / "browsers" / "not-transaction-owned" / "keep.txt").read_text() == "keep\n"

    assert not (root / "activation.json").exists()
    assert (root / "current").is_symlink()
    assert str((root / "current").readlink()) == "versions/A"
    state = json.loads((root / "state.json").read_text())
    assert state == evidence["state"]
    assert state["active"] == "A"
    assert state["previous_healthy"] == "B"
    assert state["retained_browser_targets"] == ["chromium-A", "chromium-B"]
    assert (root / "versions" / "A" / "runtime.lock").read_bytes().endswith(b" A\n")
    assert (root / "versions" / "B" / "runtime.lock").read_bytes().endswith(b" B\n")

    for target, identity in (("chromium-A", identity_a), ("chromium-B", identity_b)):
        browser = evidence["browser_targets"][target]
        executable = root / "browsers" / target / "browser-probe"
        assert browser["executable_path"] == str(executable)
        assert browser["target_digest"] == identity[1]
        assert browser["target_bytes"] == identity[0]
        assert browser["doctor_output"] == _PROBE_OUTPUT
        assert (root / "browsers" / target / "Frameworks" / "Current").is_symlink()

    rollback = evidence["rollback"]
    assert rollback["offline"] is True
    assert rollback["used_retained_target"] is True
    assert "source" not in rollback
    assert "archive" not in rollback
    assert rollback["browser"]["target"] == "chromium-A"
    assert rollback["active_pointer"] == "versions/A"
    assert list(inspect.signature(_rollback).parameters) == ["root", "version", "current_state"]

    home_evidence = evidence["roi_h_home"]
    assert set(home_evidence) == {"before", "after", "unchanged"}
    assert set(home_evidence["before"]) == {"sha256", "entry_count", "bytes"}
    assert home_evidence["unchanged"] is True
    assert home_evidence["before"] == home_evidence["after"]
    home_after = {
        path.relative_to(roi_h_home).as_posix(): path.read_bytes()
        for path in roi_h_home.rglob("*")
        if path.is_file()
    }
    assert home_after == home_before
    assert archive_a.exists() and archive_b.exists()


def test_rejects_output_overlap(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    roi_h_home = tmp_path / "customer" / ".roi-h"
    roi_h_home.mkdir(parents=True)
    archive_a = tmp_path / "a.tar.gz"
    archive_b = tmp_path / "b.tar.gz"
    identity_a = _browser_archive(archive_a, "A")
    identity_b = _browser_archive(archive_b, "B")

    archive_a_before = archive_a.read_bytes()
    output_over_archive = _command(
        tmp_path / "managed-output-archive-check",
        archive_a,
        roi_h_home,
        archive_a,
        identity_a,
        archive_b,
        identity_b,
    )
    completed, evidence = _run(output_over_archive, cwd)
    assert completed.returncode == 1
    assert evidence["error"] == "output package must not replace a browser archive"
    assert archive_a.read_bytes() == archive_a_before

    output_in_home = _command(
        tmp_path / "managed-output-check",
        roi_h_home / "forbidden.pkg",
        roi_h_home,
        archive_a,
        identity_a,
        archive_b,
        identity_b,
    )
    completed, evidence = _run(output_in_home, cwd)
    assert completed.returncode == 1
    assert evidence["error_type"] == "ValueError"
    assert "output package path overlaps managed data" in str(evidence["error"])
    assert not (roi_h_home / "forbidden.pkg").exists()


@pytest.mark.skipif(
    os.environ.get("ROI_H_RUN_ISSUE14_NATIVE") != "1",
    reason="set ROI_H_RUN_ISSUE14_NATIVE=1 to execute the real unsigned native journey",
)
def test_unsigned_native_journey_from_unrelated_cwd(tmp_path: Path) -> None:
    unrelated_cwd = tmp_path / "unrelated-native-cwd"
    unrelated_cwd.mkdir()
    roi_h_home = tmp_path / "existing-roi-h-home"
    (roi_h_home / "projects").mkdir(parents=True)
    (roi_h_home / "identity.bin").write_bytes(bytes(range(256)))
    output_dir = Path(
        os.environ.get("ROI_H_ISSUE14_OUTPUT_DIR", "/tmp/roi-h-issue14-unsigned-native-proof")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    children_before = set(output_dir.iterdir())
    browser_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    wheel = Path(__file__).resolve().parents[2] / "dist" / "roi_h-0.1.3-py3-none-any.whl"
    command = [
        sys.executable,
        "-m",
        "roi_h.harness.macos_native_package_prototype",
        "native-journey",
        "--roi-h-home",
        str(roi_h_home),
        "--browser-cache",
        str(browser_cache),
        "--wheel",
        str(wheel),
        "--output-dir",
        str(output_dir),
    ]
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        completed = subprocess.run(
            command,
            cwd=unrelated_cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    finally:
        if completed is None or completed.returncode != 0:
            for child in set(output_dir.iterdir()) - children_before:
                result = child / "result.json"
                if result.is_file():
                    subprocess.run(
                        [*command[:3], "cleanup", "--result", str(result)],
                        cwd=unrelated_cwd,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
    assert completed is not None
    evidence = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert evidence["ok"] is True
    assert evidence["mode"] == "native-journey"
    assert evidence["unsigned"] is True
    assert evidence["native_installer_executed"] is True
    assert evidence["host"]["system"] == "Darwin"
    assert evidence["host"]["machine"] == "arm64"
    assert evidence["host"]["macos_version"] == "26.5"
    artifact_dir = Path(evidence["artifact_dir"])
    assert artifact_dir.parent == output_dir.resolve()
    assert artifact_dir.name == evidence["journey_id"]
    assert artifact_dir.stat().st_mode & 0o777 == 0o700

    assert set(evidence["packages"]) == {"A", "B"}
    for version, receipt in (
        ("A", "com.roih.wayfinder.issue14.native.a"),
        ("B", "com.roih.wayfinder.issue14.native.b"),
    ):
        package = evidence["packages"][version]
        package_path = Path(package["path"])
        assert package_path.is_file()
        assert package["sha256"] == _identity(package_path)[1]
        assert package["bytes"] == package_path.stat().st_size
        assert package["receipt_id"] == receipt
        assert package["unsigned"] is True
        assert package["signature"]["status"] == "unsigned"
        assert package["signature"]["check_returncode"] != 0
        assert package["lifecycle_scripts"] is False
        assert evidence["installs"][version]["returncode"] == 0
        assert evidence["installs"][version]["without_sudo"] is True

    assert set(evidence["browser_target_manifests"]) == {
        "chromium-1228",
        "chromium_headless_shell-1228",
        "ffmpeg-1011",
    }
    for target, manifest in evidence["browser_target_manifests"].items():
        assert manifest["entry_count"] > 0
        assert len(manifest["sha256"]) == 64
        artifact = json.loads(Path(manifest["artifact"]).read_text(encoding="utf-8"))
        assert {key: artifact[key] for key in ("entry_count", "bytes", "sha256")} == {
            key: manifest[key] for key in ("entry_count", "bytes", "sha256")
        }, target
    for version, manifest in evidence["runtime_manifests"].items():
        artifact = json.loads(Path(manifest["artifact"]).read_text(encoding="utf-8"))
        assert {
            key: artifact["runtime_manifest"][key] for key in ("entry_count", "bytes", "sha256")
        } == {key: manifest[key] for key in ("entry_count", "bytes", "sha256")}, version
    for version, manifest in evidence["managed_root_manifests"].items():
        artifact = json.loads(Path(manifest["artifact"]).read_text(encoding="utf-8"))
        assert {key: artifact[key] for key in ("entry_count", "bytes", "sha256")} == {
            key: manifest[key] for key in ("entry_count", "bytes", "sha256")
        }, version
        assert (
            evidence["packages"][version]["expanded_verification"]["sha256"] == manifest["sha256"]
        )
    for verification in evidence["version_verifications"].values():
        runtime = verification["runtime"]
        assert runtime["python_version"] == "3.12.13"
        assert runtime["versions"] == {
            "roi-h": "0.1.3",
            "playwright": "1.61.0",
            "activegraph": "1.10.0",
        }
        assert verification["doctor"]["ok"] is True
        assert verification["playwright"]["revision"] == "1228"
        assert verification["playwright"]["evaluation_6_times_7_equals_42"] is True
        assert set(verification["playwright"]["target_manifest_digests"]) == {
            "chromium-1228",
            "chromium_headless_shell-1228",
            "ffmpeg-1011",
        }

    assert [event["event"] for event in evidence["events"]] == [
        "activate_A",
        "recover_activation_before_pointer",
        "recover_activation_after_pointer",
        "recover_activation_after_native_state",
        "recover_activation_after_install_state",
        "retained_A_tamper_rejected",
        "offline_rollback_A",
    ]
    boundaries = evidence["update_recovery"]["activation_boundaries"]
    assert {name: item["child_returncode"] for name, item in boundaries.items()} == {
        "before_pointer": 75,
        "after_pointer": 76,
        "after_native_state": 77,
        "after_install_state": 78,
    }
    assert boundaries["before_pointer"]["active"] == "A"
    assert boundaries["before_pointer"]["convergence"] == "prior"
    assert boundaries["after_pointer"]["active"] == "B"
    assert boundaries["after_pointer"]["convergence"] == "desired"
    assert not (Path(evidence["managed_root"]) / "activation-journal.json").exists()
    assert evidence["tamper_rejection"]["rejected"] is True
    assert evidence["tamper_rejection"]["tampered_code_executed"] is False
    assert evidence["tamper_rejection"]["B_remained_active"] is True
    assert evidence["rollback"]["offline_input_contract"] is True
    assert evidence["rollback"]["package_source"] is False
    assert evidence["rollback"]["archive_source"] is False
    assert evidence["rollback"]["network_access_blocked"] is False
    assert evidence["retained"]["exact"] is True
    assert all(item["matched"] for item in evidence["retained"]["versions"].values())
    assert all(item["matched"] for item in evidence["retained"]["browser_targets"].values())
    assert evidence["roi_h_home"]["unchanged"] is True
    assert evidence["roi_h_home"]["before"] == evidence["roi_h_home"]["after"]
    assert evidence["cleanup"]["marker_matched"] is True
    assert evidence["cleanup"]["root_absent"] is True
    assert evidence["cleanup"]["errors"] == []
    assert all(item["absent_after"] for item in evidence["cleanup"]["receipts"].values())
    assert evidence["tracked_receipts"] == [
        "com.roih.wayfinder.issue14.native.a",
        "com.roih.wayfinder.issue14.native.b",
    ]
    assert not (Path.home() / "Library" / "Application Support" / "ROI-H").exists()
    assert not (
        Path.home() / "Library" / "Application Support" / ".roi-h-issue14-native.lock"
    ).exists()
    assert Path(evidence["result_path"]).is_file()
    assert json.loads(Path(evidence["result_path"]).read_text(encoding="utf-8")) == evidence
    assert evidence["limitations"] == [
        "macOS 14 was not executed; this proof ran only on macOS 26.5",
        "the production managed-root documentation/default conflicts with the issue #9 prototype root",
        "Installer-process interruption was not tested and /usr/sbin/installer was not killed",
        "A and B use identical ROI-H 0.1.3 bytes; cross-version compatibility was not tested",
        "the host did not enforce network denial",
    ]

    explicit_cleanup = subprocess.run(
        [*command[:3], "cleanup", "--result", evidence["result_path"]],
        cwd=unrelated_cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert explicit_cleanup.returncode == 0, explicit_cleanup.stderr or explicit_cleanup.stdout
    assert json.loads(explicit_cleanup.stdout)["cleanup"]["root_absent"] is True
