"""Package-shape and transaction-scenario checks for issue #14."""

# ruff: noqa: PLR0913, PLR0917, S314, S603

from __future__ import annotations

import hashlib
import inspect
import io
import json
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
            "package signing and notarization",
            "macOS 14 execution",
        ],
    }
    assert evidence["host"]["system"] == "Darwin"
    assert evidence["root"] == str(root)

    package_evidence = evidence["package"]
    assert package_evidence["path"] == str(package)
    assert package_evidence["format"] == "flat-product-pkg"
    assert package_evidence["signed"] is False
    assert package_evidence["signing_identity"] is None
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


def test_rejects_output_overlap_and_blank_signing_identity(tmp_path: Path) -> None:
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

    blank_signing = [
        *_command(
            tmp_path / "managed-signing-check",
            tmp_path / "blank-signing.pkg",
            roi_h_home,
            archive_a,
            identity_a,
            archive_b,
            identity_b,
        ),
        "--signing-identity",
        "   ",
    ]
    completed, evidence = _run(blank_signing, cwd)
    assert completed.returncode == 1
    assert evidence["error_type"] == "ValueError"
    assert evidence["error"] == "signing identity must not be blank"
    assert not (tmp_path / "blank-signing.pkg").exists()
