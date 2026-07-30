"""PROTOTYPE: collect fast scenario or opt-in unsigned native Installer evidence.

This throwaway Wayfinder issue #14 module is not a public API.
Run it only as ``python -m roi_h.harness.macos_native_package_prototype``.
"""

# ruff: noqa: E501, EM101, EM102, PLR0917, PTH115, S314, TRY003, TRY301

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

if sys.platform == "darwin":
    import fcntl
else:
    fcntl = None

_PROBE_OUTPUT = "ROI-H managed Chromium prototype"
_PACKAGE_ID = "com.roih.wayfinder.issue14.prototype"
_PACKAGE_VERSION = "0.0.14"
_RUNTIME_FIXTURES = {
    "A": b"ROI-H locked runtime prototype A\n",
    "B": b"ROI-H locked runtime prototype B\n",
}
_RUNTIME_IDENTITIES = {
    "A": (33, "e8fdca7b06217765f5eb53e6dbaf63655f1f03ee95c01ebe8ab668f57bdfeb4c"),
    "B": (33, "ac907d50716fb91fc839502286577b832419f791fa0ab1fe33a349adfa917ea3"),
}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    length = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            length += len(chunk)
            digest.update(chunk)
    return length, digest.hexdigest()


def _verify_file(path: Path, expected_length: int, expected_sha256: str) -> None:
    observed_length, observed_sha256 = _file_identity(path)
    if observed_length != expected_length or observed_sha256 != expected_sha256:
        raise ValueError(
            f"digest-bound file mismatch for {path}: expected "
            f"{expected_length}/{expected_sha256}, observed "
            f"{observed_length}/{observed_sha256}"
        )


def _runtime(version: str) -> tuple[bytes, dict[str, Any]]:
    content = _RUNTIME_FIXTURES[version]
    expected_length, expected_sha256 = _RUNTIME_IDENTITIES[version]
    if len(content) != expected_length or _sha256(content) != expected_sha256:
        raise RuntimeError(f"digest-bound runtime fixture identity changed: {version}")
    return content, {
        "version": version,
        "bytes": expected_length,
        "sha256": expected_sha256,
        "kind": "digest-bound-runtime-fixture",
        "actual_locked_roi_h_runtime": False,
    }


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _tree_identity(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise ValueError(f"ROI_H_HOME must be an existing directory: {root}")
    identities: list[dict[str, Any]] = []
    byte_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            identities.append({"path": relative, "kind": "symlink", "target": str(path.readlink())})
        elif path.is_dir():
            identities.append({"path": relative, "kind": "directory"})
        elif path.is_file():
            length, digest = _file_identity(path)
            byte_count += length
            identities.append({"path": relative, "kind": "file", "bytes": length, "sha256": digest})
        else:
            raise ValueError(f"unsupported ROI_H_HOME entry: {path}")
    encoded = json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()
    return {
        "sha256": _sha256(encoded),
        "entry_count": len(identities),
        "bytes": byte_count,
    }


def _distribution_xml() -> str:
    root = ET.Element("installer-gui-script", {"minSpecVersion": "2"})
    ET.SubElement(root, "title").text = "ROI-H Wayfinder issue 14 prototype"
    ET.SubElement(
        root,
        "options",
        {"customize": "never", "require-scripts": "false", "hostArchitectures": "arm64"},
    )
    ET.SubElement(
        root,
        "domains",
        {
            "enable_anywhere": "false",
            "enable_currentUserHome": "true",
            "enable_localSystem": "false",
        },
    )
    allowed = ET.SubElement(root, "allowed-os-versions")
    ET.SubElement(allowed, "os-version", {"min": "14.0"})
    outline = ET.SubElement(root, "choices-outline")
    ET.SubElement(outline, "line", {"choice": "default"})
    choice = ET.SubElement(root, "choice", {"id": "default", "visible": "false"})
    ET.SubElement(choice, "pkg-ref", {"id": _PACKAGE_ID})
    reference = ET.SubElement(
        root,
        "pkg-ref",
        {"id": _PACKAGE_ID, "version": _PACKAGE_VERSION, "onConclusion": "none"},
    )
    reference.text = "component.pkg"
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")


def _run_native(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        command, check=True, capture_output=True, text=True
    )
    return {
        "command": [Path(command[0]).name, *command[1:-1], Path(command[-1]).name],
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _signature_check(package: Path) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        ["/usr/sbin/pkgutil", "--check-signature", str(package)],
        check=False,
        capture_output=True,
        text=True,
    )
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode == 0 or "no signature" not in output.lower():
        raise RuntimeError(f"pkgutil did not prove that the package is unsigned: {output}")
    return {
        "status": "unsigned",
        "confirmed": False,
        "check_returncode": completed.returncode,
        "check_output": output,
    }


def _build_package(output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="roi-h-issue-14-pkg-") as temporary:
        work = Path(temporary)
        payload_file = (
            work
            / "payload"
            / "Library"
            / "Application Support"
            / "ROI-H"
            / "versions"
            / "A"
            / "runtime.lock"
        )
        payload_file.parent.mkdir(parents=True)
        content, runtime_identity = _runtime("A")
        payload_file.write_bytes(content)
        _verify_file(payload_file, runtime_identity["bytes"], runtime_identity["sha256"])
        distribution = work / "Distribution"
        distribution.write_text(_distribution_xml(), encoding="utf-8")
        component = work / "component.pkg"
        commands = [
            _run_native(
                [
                    "/usr/bin/pkgbuild",
                    "--root",
                    str(work / "payload"),
                    "--identifier",
                    _PACKAGE_ID,
                    "--version",
                    _PACKAGE_VERSION,
                    "--install-location",
                    "/",
                    str(component),
                ]
            )
        ]
        product_command = [
            "/usr/bin/productbuild",
            "--distribution",
            str(distribution),
            "--package-path",
            str(work),
        ]
        product_command.append(str(output))
        commands.append(_run_native(product_command))
    signature = _signature_check(output)
    length, digest = _file_identity(output)
    return {
        "path": str(output.resolve()),
        "bytes": length,
        "sha256": digest,
        "identifier": _PACKAGE_ID,
        "version": _PACKAGE_VERSION,
        "format": "flat-product-pkg",
        "signed": False,
        "signature": signature,
        "notarized": False,
        "notarization_attempted": False,
        "distribution": {
            "domain": "CurrentUserHomeDirectory",
            "enable_currentUserHome": True,
            "enable_localSystem": False,
            "enable_anywhere": False,
            "architecture": "arm64",
            "minimum_macos": "14.0",
            "xml": _distribution_xml(),
        },
        "payload": {
            **runtime_identity,
            "install_path": "Library/Application Support/ROI-H/versions/A/runtime.lock",
        },
        "commands": commands,
    }


def _contained_file(parent: Path, path: Path) -> Path:
    parent_resolved = parent.resolve(strict=True)
    path_resolved = path.resolve(strict=True)
    if path_resolved == parent_resolved or parent_resolved not in path_resolved.parents:
        raise RuntimeError(f"managed executable escapes its target: {path}")
    return path_resolved


def _extract_browser(
    root: Path,
    *,
    target: str,
    archive_path: Path,
    archive_length: int,
    archive_sha256: str,
    transaction: str,
) -> dict[str, Any]:
    if not _NAME.fullmatch(target) or not _NAME.fullmatch(transaction):
        raise ValueError("invalid browser target or transaction")
    browser_root = root / "browsers"
    browser_root.mkdir(parents=True, exist_ok=True)
    staging = browser_root / f".staging-{transaction}-{target}"
    destination = browser_root / target
    if staging.exists() or destination.exists():
        raise FileExistsError(f"browser target or owned staging already exists: {target}")
    staging.mkdir()
    owned_archive = staging / "browser.tar"
    try:
        shutil.copyfile(archive_path, owned_archive)
        _verify_file(owned_archive, archive_length, archive_sha256)
        with tarfile.open(owned_archive, mode="r:*") as archive:
            archive.extractall(staging, filter="data")
        owned_archive.unlink()
        executable = _contained_file(staging, staging / "browser-probe")
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(f"managed browser executable is not executable: {executable}")
        executable_length, executable_sha256 = _file_identity(executable)
        metadata = {
            "target": target,
            "archive_bytes": archive_length,
            "archive_sha256": archive_sha256,
            "executable": "browser-probe",
            "executable_bytes": executable_length,
            "executable_sha256": executable_sha256,
        }
        _write_json_atomic(staging / "target.json", metadata)
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return metadata


def _doctor(executable: Path) -> str:
    completed = subprocess.run(  # noqa: S603
        [str(executable)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=executable.parent,
        env={"PATH": "/usr/bin:/bin"},
    )
    output = completed.stdout.rstrip("\n")
    if completed.returncode != 0 or output != _PROBE_OUTPUT:
        raise RuntimeError(
            f"managed browser doctor failed: returncode={completed.returncode}, output={output!r}"
        )
    return output


def _verify_browser(root: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    target = str(metadata["target"])
    if not _NAME.fullmatch(target) or metadata["executable"] != "browser-probe":
        raise RuntimeError("invalid trusted browser metadata")
    browser_root = root / "browsers"
    destination = browser_root / target
    if browser_root.is_symlink() or destination.is_symlink():
        raise RuntimeError("managed browser target must not be a symlink")
    if browser_root.resolve(strict=True) not in destination.resolve(strict=True).parents:
        raise RuntimeError("managed browser target escapes its owned root")
    executable = _contained_file(destination, destination / "browser-probe")
    _verify_file(
        executable,
        int(metadata["executable_bytes"]),
        str(metadata["executable_sha256"]),
    )
    return {
        "target": target,
        "path": str(destination.resolve()),
        "executable_path": str(executable),
        "target_digest": str(metadata["archive_sha256"]),
        "target_bytes": int(metadata["archive_bytes"]),
        "executable_sha256": str(metadata["executable_sha256"]),
        "doctor_output": _doctor(executable),
    }


def _version_metadata(version: str, target: str, browser: dict[str, Any]) -> dict[str, Any]:
    _, runtime_identity = _runtime(version)
    if target != browser["target"]:
        raise ValueError("version and browser targets differ")
    return {**runtime_identity, "browser_target": target, "browser": browser, "healthy": True}


def _stage_version(
    root: Path,
    version: str,
    transaction: str,
    metadata: dict[str, Any],
) -> Path:
    content, _ = _runtime(version)
    staging = root / ".transactions" / transaction / "version"
    if staging.exists():
        raise FileExistsError(f"version staging already exists: {staging}")
    staging.mkdir(parents=True)
    runtime_path = staging / "runtime.lock"
    runtime_path.write_bytes(content)
    _verify_file(runtime_path, int(metadata["bytes"]), str(metadata["sha256"]))
    _write_json_atomic(staging / "version.json", metadata)
    return staging


def _commit_version(
    root: Path,
    version: str,
    target: str,
    transaction: str,
    browser: dict[str, Any],
) -> dict[str, Any]:
    metadata = _version_metadata(version, target, browser)
    staging = _stage_version(root, version, transaction, metadata)
    destination = root / "versions" / version
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"version already exists: {version}")
    staging.replace(destination)
    shutil.rmtree(root / ".transactions" / transaction, ignore_errors=True)
    return metadata


def _verify_version(root: Path, version: str, trusted: dict[str, Any]) -> dict[str, Any]:
    destination = root / "versions" / version
    stored = json.loads((destination / "version.json").read_text(encoding="utf-8"))
    if stored != trusted or trusted.get("version") != version:
        raise RuntimeError(f"trusted version metadata mismatch: {version}")
    _verify_file(destination / "runtime.lock", int(trusted["bytes"]), str(trusted["sha256"]))
    return _verify_browser(root, dict(trusted["browser"]))


def _activate(
    root: Path,
    version: str,
    state: dict[str, Any],
    *,
    interrupt_after_pointer: bool = False,
) -> dict[str, Any]:
    if state.get("active") != version:
        raise RuntimeError("activation state does not name the requested version")
    trusted = dict(state["versions"][version])
    browser = _verify_version(root, version, trusted)
    record_path = root / "activation.json"
    _write_json_atomic(record_path, {"version": version, "state": state})
    pointer = root / "current"
    temporary = root / f".current-{os.getpid()}"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(Path("versions") / version)
    temporary.replace(pointer)
    if interrupt_after_pointer:
        os._exit(74)
    _write_json_atomic(root / "state.json", state)
    record_path.unlink()
    return browser


def _reconcile_activation(root: Path) -> dict[str, Any]:
    record_path = root / "activation.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if set(record) != {"version", "state"}:
        raise RuntimeError("invalid activation record")
    version = str(record["version"])
    state = dict(record["state"])
    if state.get("active") != version or not _NAME.fullmatch(version):
        raise RuntimeError("activation record state does not match its version")
    browser = _verify_version(root, version, dict(state["versions"][version]))
    expected_pointer = Path("versions") / version
    if (root / "current").readlink() != expected_pointer:
        raise RuntimeError("activation pointer does not match its recovery record")
    _write_json_atomic(root / "state.json", state)
    record_path.unlink()
    return {
        "active_pointer": str(expected_pointer),
        "state_active": state["active"],
        "browser": browser,
    }


def _interrupt_update_child(
    root: Path,
    archive_path: Path,
    archive_length: int,
    archive_sha256: str,
    target: str,
) -> None:
    transaction = "interrupted-update-B"
    record = {"id": transaction, "version": "B", "target": target}
    _validate_transaction_record(record)
    (root / ".transactions").mkdir(exist_ok=True)
    (root / "browsers").mkdir(exist_ok=True)
    _write_json_atomic(root / "transaction.json", record)
    transaction_root = root / ".transactions" / transaction
    version_staging = transaction_root / "version"
    version_staging.mkdir(parents=True)
    content, runtime = _runtime("B")
    (version_staging / "runtime.lock").write_bytes(content)
    _write_json_atomic(version_staging / "version.json", runtime)
    browser_staging = root / "browsers" / f".staging-{transaction}-{target}"
    browser_staging.mkdir()
    owned_archive = browser_staging / "browser.tar"
    shutil.copyfile(archive_path, owned_archive)
    _verify_file(owned_archive, archive_length, archive_sha256)
    os._exit(73)


def _validate_transaction_record(record: object) -> dict[str, str]:
    if not isinstance(record, dict) or set(record) != {"id", "version", "target"}:
        raise RuntimeError("invalid transaction record")
    values = {key: str(record[key]) for key in ("id", "version", "target")}
    if not _NAME.fullmatch(values["id"]) or not _NAME.fullmatch(values["target"]):
        raise RuntimeError("invalid transaction id or target")
    if values["version"] not in _RUNTIME_FIXTURES:
        raise RuntimeError("invalid transaction version")
    return values


def _owned_staging_path(parent: Path, path: Path) -> Path:
    if parent.is_symlink() or not parent.is_dir():
        raise RuntimeError(f"invalid owned staging parent: {parent}")
    parent_resolved = parent.resolve(strict=True)
    path_resolved = path.resolve(strict=False)
    if path.is_symlink() or parent_resolved not in path_resolved.parents:
        raise RuntimeError(f"transaction staging escapes owned parent: {path}")
    return path


def _reconcile_transaction(root: Path) -> dict[str, Any]:
    record_path = root / "transaction.json"
    record = _validate_transaction_record(json.loads(record_path.read_text(encoding="utf-8")))
    transaction_path = _owned_staging_path(
        root / ".transactions", root / ".transactions" / record["id"]
    )
    browser_path = _owned_staging_path(
        root / "browsers",
        root / "browsers" / f".staging-{record['id']}-{record['target']}",
    )
    removed: list[str] = []
    for path in (transaction_path, browser_path):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        removed.append(path.relative_to(root).as_posix())
    record_path.unlink()
    return {
        "transaction": record["id"],
        "removed_only_transaction_owned": removed,
        "active_pointer": str((root / "current").readlink()),
    }


def _state(active: str, previous: str | None, versions: dict[str, Any]) -> dict[str, Any]:
    retained_versions = [item for item in (active, previous) if item is not None]
    return {
        "active": active,
        "previous_healthy": previous,
        "versions": versions,
        "retained_browser_targets": sorted(
            {str(versions[version]["browser_target"]) for version in retained_versions}
        ),
    }


def _rollback(root: Path, version: str, current_state: dict[str, Any]) -> dict[str, Any]:
    state = _state(version, str(current_state["active"]), dict(current_state["versions"]))
    browser = _activate(root, version, state)
    return {
        "offline": True,
        "used_retained_target": True,
        "browser": browser,
        "active_pointer": str((root / "current").readlink()),
        "state": state,
    }


def _assert_separate(first: Path, second: Path) -> None:
    first_resolved = first.resolve()
    second_resolved = second.resolve()
    if first_resolved == second_resolved:
        raise ValueError("managed root and ROI_H_HOME must be separate")
    if first_resolved in second_resolved.parents or second_resolved in first_resolved.parents:
        raise ValueError("managed root and ROI_H_HOME must not contain each other")


def _assert_output_separate(output: Path, *managed: Path) -> None:
    output_resolved = output.resolve()
    for boundary in managed:
        boundary_resolved = boundary.resolve()
        if (
            output_resolved == boundary_resolved
            or boundary_resolved in output_resolved.parents
            or output_resolved in boundary_resolved.parents
        ):
            raise ValueError(f"output package path overlaps managed data: {output_resolved}")


def _spawn_update_interruption(
    root: Path,
    archive: Path,
    length: int,
    digest: str,
    target: str,
) -> int:
    code = (
        "from pathlib import Path; "
        "from roi_h.harness.macos_native_package_prototype import _interrupt_update_child; "
        "import sys; _interrupt_update_child(Path(sys.argv[1]), Path(sys.argv[2]), "
        "int(sys.argv[3]), sys.argv[4], sys.argv[5])"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code, str(root), str(archive), str(length), digest, target],
        check=False,
    )
    if completed.returncode != 73:
        raise RuntimeError(f"update interruption child returned {completed.returncode}")
    return completed.returncode


def _spawn_activation_interruption(root: Path, version: str, state: dict[str, Any]) -> int:
    code = (
        "from pathlib import Path; "
        "from roi_h.harness.macos_native_package_prototype import _activate; "
        "import json,sys; _activate(Path(sys.argv[1]), sys.argv[2], "
        "json.loads(sys.argv[3]), interrupt_after_pointer=True)"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code, str(root), version, json.dumps(state)],
        check=False,
    )
    if completed.returncode != 74:
        raise RuntimeError(f"activation interruption child returned {completed.returncode}")
    return completed.returncode


def _scenario(args: argparse.Namespace) -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise RuntimeError("Wayfinder issue #14 prototype requires macOS package tools")
    root = args.root.resolve()
    roi_h_home = args.roi_h_home.resolve()
    output = args.output_pkg.resolve()
    archive_a = args.browser_a.resolve()
    archive_b = args.browser_b.resolve()
    _assert_separate(root, roi_h_home)
    _assert_output_separate(output, root, roi_h_home)
    if output in {archive_a, archive_b}:
        raise ValueError("output package must not replace a browser archive")
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"managed prototype root must be absent or empty: {root}")
    if not _NAME.fullmatch(args.browser_a_target) or not _NAME.fullmatch(args.browser_b_target):
        raise ValueError("browser targets must be simple managed directory names")
    if args.browser_a_target == args.browser_b_target:
        raise ValueError("A and B must use distinct browser targets")

    _verify_file(archive_a, args.browser_a_bytes, args.browser_a_sha256)
    _verify_file(archive_b, args.browser_b_bytes, args.browser_b_sha256)
    home_before = _tree_identity(roi_h_home)
    package = _build_package(output)
    root.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    versions: dict[str, Any] = {}

    extracted_a = _extract_browser(
        root,
        target=args.browser_a_target,
        archive_path=archive_a,
        archive_length=args.browser_a_bytes,
        archive_sha256=args.browser_a_sha256,
        transaction="install-A",
    )
    versions["A"] = _commit_version(root, "A", args.browser_a_target, "install-A", extracted_a)
    state = _state("A", None, versions)
    browser_a = _activate(root, "A", state)
    events.append({"event": "install_a", "active_pointer": str((root / "current").readlink())})

    sentinel = root / "browsers" / "not-transaction-owned"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("keep\n", encoding="utf-8")
    other_transaction = root / ".transactions" / "other-transaction"
    other_browser = root / "browsers" / ".staging-other-transaction-chromium-B"
    other_transaction.mkdir(parents=True)
    other_browser.mkdir()
    (other_transaction / "keep.txt").write_text("keep\n", encoding="utf-8")
    (other_browser / "keep.txt").write_text("keep\n", encoding="utf-8")
    child_returncode = _spawn_update_interruption(
        root,
        archive_b,
        args.browser_b_bytes,
        args.browser_b_sha256,
        args.browser_b_target,
    )
    events.append(
        {
            "event": "interrupt_update_b",
            "child_returncode": child_returncode,
            "active_pointer": str((root / "current").readlink()),
        }
    )
    recovery = _reconcile_transaction(root)
    recovery["unowned_sentinel_preserved"] = (sentinel / "keep.txt").is_file()
    recovery["other_transaction_staging_preserved"] = (
        other_transaction / "keep.txt"
    ).is_file() and (other_browser / "keep.txt").is_file()
    events.append({"event": "restart_transaction_reconciliation", **recovery})

    extracted_b = _extract_browser(
        root,
        target=args.browser_b_target,
        archive_path=archive_b,
        archive_length=args.browser_b_bytes,
        archive_sha256=args.browser_b_sha256,
        transaction="update-B",
    )
    versions["B"] = _commit_version(root, "B", args.browser_b_target, "update-B", extracted_b)
    state_b = _state("B", "A", versions)
    activation_returncode = _spawn_activation_interruption(root, "B", state_b)
    events.append(
        {
            "event": "interrupt_activation_b_after_pointer",
            "child_returncode": activation_returncode,
            "active_pointer": str((root / "current").readlink()),
            "state_active": json.loads((root / "state.json").read_text())["active"],
        }
    )
    activation_recovery = _reconcile_activation(root)
    browser_b = activation_recovery.pop("browser")
    events.append({"event": "restart_activation_reconciliation", **activation_recovery})

    runtime_a = root / "versions" / "A" / "runtime.lock"
    runtime_a.write_bytes(b"tampered runtime fixture\n")
    try:
        _rollback(root, "A", state_b)
    except ValueError as error:
        tamper_rejected = "digest-bound file mismatch" in str(error)
    else:
        tamper_rejected = False
    if not tamper_rejected or (root / "current").readlink() != Path("versions/B"):
        raise RuntimeError("tampered runtime activation was not rejected")
    events.append({"event": "tampered_runtime_activation_rejected", "rejected": True})
    runtime_a.write_bytes(_runtime("A")[0])

    rollback = _rollback(root, "A", state_b)
    state = dict(rollback.pop("state"))
    events.append({"event": "offline_rollback_a", **rollback})
    home_after = _tree_identity(roi_h_home)
    home_unchanged = home_before == home_after

    return {
        "ok": home_unchanged,
        "prototype": "Wayfinder issue #14",
        "public_api": False,
        "evidence_scope": {
            "classification": "package-shape-and-transaction-scenario",
            "native_acceptance": False,
            "unproved": [
                "actual Installer execution",
                "actual locked ROI-H payload",
                "package notarization",
                "macOS 14 execution",
            ],
        },
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "macos_version": platform.mac_ver()[0],
        },
        "root": str(root),
        "package": package,
        "browser_targets": {
            args.browser_a_target: browser_a,
            args.browser_b_target: browser_b,
        },
        "events": events,
        "recovery": recovery,
        "activation_recovery": activation_recovery,
        "rollback": rollback,
        "state": state,
        "roi_h_home": {
            "before": home_before,
            "after": home_after,
            "unchanged": home_unchanged,
        },
    }


_NATIVE_ROOT_PARTS = ("Library", "Application Support", "ROI-H")
_NATIVE_RECEIPTS = {
    "A": "com.roih.wayfinder.issue14.native.a",
    "B": "com.roih.wayfinder.issue14.native.b",
}
_NATIVE_PACKAGE_VERSIONS = {"A": "0.0.14.1", "B": "0.0.14.2"}
_NATIVE_BROWSER_TARGETS = (
    "chromium-1228",
    "chromium_headless_shell-1228",
    "ffmpeg-1011",
)
_NATIVE_IDENTITIES = {
    "python": "3.12.13",
    "roi-h": "0.1.3",
    "playwright": "1.61.0",
    "activegraph": "1.10.0",
}
_OWNER_MARKER = ".issue14-native-owner.json"
_NATIVE_LOCK = ".roi-h-issue14-native.lock"
_NATIVE_LIMITATIONS = [
    "macOS 14 was not executed; this proof ran only on macOS 26.5",
    "the production managed-root documentation/default conflicts with the issue #9 prototype root",
    "Installer-process interruption was not tested and /usr/sbin/installer was not killed",
    "A and B use identical ROI-H 0.1.3 bytes; cross-version compatibility was not tested",
    "the host did not enforce network denial",
]


def _run_capture(
    command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _require_success(result: dict[str, Any], label: str) -> dict[str, Any]:
    if result["returncode"] != 0:
        raise RuntimeError(
            f"{label} failed ({result['returncode']}): {result['stderr'] or result['stdout']}"
        )
    return result


def _complete_tree_manifest(root: Path) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"manifest root must be a real directory: {root}")
    entries: list[dict[str, Any]] = []
    byte_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        entry: dict[str, Any] = {"path": path.relative_to(root).as_posix()}
        if path.is_symlink():
            entry.update(kind="symlink", target=os.readlink(path))
        elif path.is_dir():
            entry["kind"] = "directory"
        elif path.is_file():
            length, digest = _file_identity(path)
            byte_count += length
            entry.update(
                kind="file",
                bytes=length,
                sha256=digest,
                mode=f"0o{path.stat().st_mode & 0o777:03o}",
                executable=bool(path.stat().st_mode & 0o111),
            )
        else:
            raise ValueError(f"unsupported manifest entry: {path}")
        entries.append(entry)
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "entries": entries,
        "entry_count": len(entries),
        "bytes": byte_count,
        "sha256": _sha256(encoded),
    }


def _verify_tree_manifest(root: Path, expected: dict[str, Any], label: str) -> dict[str, Any]:
    observed = _complete_tree_manifest(root)
    if observed != expected:
        raise RuntimeError(
            f"{label} tree manifest mismatch: expected {expected.get('sha256')}, "
            f"observed {observed.get('sha256')}"
        )
    return {
        "path": str(root.resolve()),
        "entry_count": observed["entry_count"],
        "bytes": observed["bytes"],
        "sha256": observed["sha256"],
        "matched": True,
    }


def _receipt_info(home: Path, identifier: str) -> dict[str, Any] | None:
    result = _run_capture(
        ["/usr/sbin/pkgutil", "--volume", str(home), "--pkg-info-plist", identifier]
    )
    if int(result["returncode"]) != 0:
        return None
    value = plistlib.loads(str(result["stdout"]).encode())
    if not isinstance(value, dict):
        raise TypeError(f"invalid receipt information for {identifier}")
    return value


def _receipt_present(home: Path, identifier: str) -> bool:
    return _receipt_info(home, identifier) is not None


def _lock_path(home: Path) -> Path:
    return home / "Library" / "Application Support" / _NATIVE_LOCK


def _acquire_native_lock(home: Path) -> tuple[int, Path]:
    path = _lock_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path) and path.is_symlink():
        raise RuntimeError(f"native journey lock must not be a symlink: {path}")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)  # type: ignore[attr-defined]
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
    except OSError:
        os.close(descriptor)
        raise RuntimeError("another issue #14 native journey holds the exclusive lock") from None
    return descriptor, path


def _release_native_lock(descriptor: int, path: Path) -> None:
    try:
        current = os.fstat(descriptor)
        if path.exists() and not path.is_symlink() and path.stat().st_ino == current.st_ino:
            path.unlink()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)  # type: ignore[attr-defined]
        os.close(descriptor)


def _native_distribution(identifier: str, package_version: str) -> str:
    root = ET.Element("installer-gui-script", {"minSpecVersion": "2"})
    ET.SubElement(root, "title").text = f"ROI-H issue 14 native journey {package_version}"
    ET.SubElement(
        root,
        "options",
        {"customize": "never", "require-scripts": "false", "hostArchitectures": "arm64"},
    )
    ET.SubElement(
        root,
        "domains",
        {
            "enable_anywhere": "false",
            "enable_currentUserHome": "true",
            "enable_localSystem": "false",
        },
    )
    allowed = ET.SubElement(root, "allowed-os-versions")
    ET.SubElement(allowed, "os-version", {"min": "14.0"})
    outline = ET.SubElement(root, "choices-outline")
    ET.SubElement(outline, "line", {"choice": "default"})
    choice = ET.SubElement(root, "choice", {"id": "default", "visible": "false"})
    ET.SubElement(choice, "pkg-ref", {"id": identifier})
    reference = ET.SubElement(
        root,
        "pkg-ref",
        {"id": identifier, "version": package_version, "onConclusion": "none"},
    )
    reference.text = "component.pkg"
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")


def _copy_portable_runtime(destination: Path, wheel: Path, output_dir: Path) -> dict[str, Any]:
    expected_python = _NATIVE_IDENTITIES["python"]
    actual_python = platform.python_version()
    if actual_python != expected_python:
        raise RuntimeError(f"native journey requires Python {expected_python}, got {actual_python}")
    base = Path(sys.base_prefix).resolve()
    if ".local/share/uv/python" not in base.as_posix():
        raise RuntimeError(f"sys.base_prefix is not a uv-managed Python: {base}")
    shutil.copytree(base, destination, symlinks=True)
    externally_managed = destination / "lib" / "python3.12" / "EXTERNALLY-MANAGED"
    externally_managed.unlink()
    repository = Path(__file__).resolve().parents[3]
    requirements = output_dir / "native-requirements.txt"
    export = _require_success(
        _run_capture(
            [
                shutil.which("uv") or "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(requirements),
            ],
            cwd=repository,
        ),
        "uv export",
    )
    install_dependencies = _require_success(
        _run_capture(
            [
                shutil.which("uv") or "uv",
                "pip",
                "install",
                "--python",
                str(destination / "bin" / "python"),
                "--offline",
                "--require-hashes",
                "-r",
                str(requirements),
            ]
        ),
        "offline dependency install",
    )
    install_wheel = _require_success(
        _run_capture(
            [
                shutil.which("uv") or "uv",
                "pip",
                "install",
                "--python",
                str(destination / "bin" / "python"),
                "--no-deps",
                "--offline",
                str(wheel),
            ]
        ),
        "offline ROI-H wheel install",
    )
    pth_files = [str(path) for path in destination.rglob("*.pth")]
    if pth_files:
        raise RuntimeError(f"portable runtime contains .pth files: {pth_files}")
    roi_h_script = destination / "bin" / "roi-h"
    roi_h_script.write_text(
        '#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'exec "$HERE/python" -m roi_h.cli "$@"\n',
        encoding="utf-8",
    )
    roi_h_script.chmod(0o755)
    return {
        "source_base_prefix": str(base),
        "requirements": {
            "path": str(requirements),
            "bytes": _file_identity(requirements)[0],
            "sha256": _file_identity(requirements)[1],
        },
        "commands": [export, install_dependencies, install_wheel],
        "wheel": {
            "path": str(wheel),
            "bytes": _file_identity(wheel)[0],
            "sha256": _file_identity(wheel)[1],
        },
        "pth_files": pth_files,
    }


def _native_launcher_content() -> bytes:
    return (
        b'#!/bin/sh\nROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
        b'exec "$ROOT/current/runtime/bin/roi-h" "$@"\n'
    )


def _native_launcher(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_native_launcher_content())
    path.chmod(0o755)


def _build_native_package(
    output: Path,
    payload: Path,
    version: str,
    expected_managed_root: dict[str, Any],
) -> dict[str, Any]:
    identifier = _NATIVE_RECEIPTS[version]
    package_version = _NATIVE_PACKAGE_VERSIONS[version]
    distribution_xml = _native_distribution(identifier, package_version)
    with tempfile.TemporaryDirectory(prefix=f"roi-h-native-{version}-") as temporary:
        work = Path(temporary)
        distribution = work / "Distribution"
        distribution.write_text(distribution_xml, encoding="utf-8")
        component = work / "component.pkg"
        component_plist = work / "components.plist"
        analyze = _require_success(
            _run_capture(
                [
                    "/usr/bin/pkgbuild",
                    "--analyze",
                    "--root",
                    str(payload),
                    str(component_plist),
                ]
            ),
            f"pkgbuild analyze {version}",
        )
        components = plistlib.loads(component_plist.read_bytes())
        for component_definition in components:
            component_definition["BundleIsRelocatable"] = False
        component_plist.write_bytes(plistlib.dumps(components, sort_keys=True))
        commands = [
            analyze,
            _require_success(
                _run_capture(
                    [
                        "/usr/bin/pkgbuild",
                        "--root",
                        str(payload),
                        "--component-plist",
                        str(component_plist),
                        "--identifier",
                        identifier,
                        "--version",
                        package_version,
                        "--install-location",
                        "/",
                        str(component),
                    ]
                ),
                f"pkgbuild {version}",
            ),
            _require_success(
                _run_capture(
                    [
                        "/usr/bin/productbuild",
                        "--distribution",
                        str(distribution),
                        "--package-path",
                        str(work),
                        str(output),
                    ]
                ),
                f"productbuild {version}",
            ),
        ]
    signature = _signature_check(output)
    with tempfile.TemporaryDirectory(
        prefix=f"expanded-{version}-", dir=output.parent
    ) as expanded_temporary:
        expanded = Path(expanded_temporary) / "package"
        _require_success(
            _run_capture(["/usr/sbin/pkgutil", "--expand-full", str(output), str(expanded)]),
            f"expand package {version}",
        )
        distribution_root = ET.parse(expanded / "Distribution").getroot()
        domains = distribution_root.find("domains")
        options = distribution_root.find("options")
        minimum = distribution_root.find("./allowed-os-versions/os-version")
        package_info_path = next(expanded.rglob("PackageInfo"))
        package_info = ET.parse(package_info_path).getroot()
        if (
            domains is None
            or domains.attrib
            != {
                "enable_anywhere": "false",
                "enable_currentUserHome": "true",
                "enable_localSystem": "false",
            }
            or options is None
            or options.attrib.get("hostArchitectures") != "arm64"
            or options.attrib.get("require-scripts") != "false"
            or minimum is None
            or minimum.attrib.get("min") != "14.0"
            or package_info.attrib.get("identifier") != identifier
            or package_info.attrib.get("version") != package_version
            or list(expanded.rglob("Scripts"))
        ):
            raise RuntimeError(f"expanded package {version} metadata did not match its contract")
        managed_roots = [
            path
            for path in expanded.rglob("ROI-H")
            if path.is_dir() and path.as_posix().endswith("Library/Application Support/ROI-H")
        ]
        if len(managed_roots) != 1:
            raise RuntimeError(
                f"expanded package {version} has {len(managed_roots)} managed payload roots"
            )
        expanded_verification = _verify_tree_manifest(
            managed_roots[0], expected_managed_root, f"expanded managed root {version}"
        )
    length, digest = _file_identity(output)
    return {
        "version": version,
        "path": str(output),
        "bytes": length,
        "sha256": digest,
        "receipt_id": identifier,
        "package_version": package_version,
        "unsigned": True,
        "signature": signature,
        "distribution": {
            "domain": "CurrentUserHomeDirectory",
            "architecture": "arm64",
            "minimum_macos": "14.0",
            "xml": distribution_xml,
        },
        "lifecycle_scripts": False,
        "commands": commands,
        "expanded_verification": expanded_verification,
    }


def _native_install(
    package: Path,
    home: Path,
    version: str,
    expected_length: int,
    expected_sha256: str,
    tracked_receipts: list[str],
) -> dict[str, Any]:
    _verify_file(package, expected_length, expected_sha256)
    result = _run_capture(
        [
            "/usr/sbin/installer",
            "-pkg",
            str(package),
            "-target",
            "CurrentUserHomeDirectory",
        ]
    )
    if result["returncode"] != 0:
        raise RuntimeError(f"installer {version} failed: {result['stderr'] or result['stdout']}")
    identifier = _NATIVE_RECEIPTS[version]
    tracked_receipts.append(identifier)
    receipt = _receipt_info(home, identifier)
    if receipt is None:
        raise RuntimeError(f"installer did not record exact receipt {identifier} on {home}")
    observed_version = str(receipt.get("pkg-version", receipt.get("pkg_version", "")))
    if observed_version != _NATIVE_PACKAGE_VERSIONS[version]:
        raise RuntimeError(
            f"receipt {identifier} version mismatch: expected "
            f"{_NATIVE_PACKAGE_VERSIONS[version]}, observed {observed_version!r}"
        )
    return {
        **result,
        "receipt_id": identifier,
        "receipt_version": observed_version,
        "receipt_present": True,
        "package_verified_immediately_before_installer": True,
        "without_sudo": True,
    }


def _assert_current_user_owned(root: Path) -> dict[str, Any]:
    expected_uid = os.getuid()  # type: ignore[attr-defined]
    checked = 0
    for path in (root, *root.rglob("*")):
        if path.lstat().st_uid != expected_uid:
            raise RuntimeError(f"installed path is not owned by current user: {path}")
        checked += 1
    return {"uid": expected_uid, "entries_checked": checked, "matched": True}


def _native_environment(**updates: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
    }
    environment.update(PYTHONDONTWRITEBYTECODE="1", **updates)
    return environment


def _native_version_probe(
    root: Path,
    version: str,
    roi_h_home: Path,
    runtime_manifest: dict[str, Any],
    browser_manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    runtime = root / "versions" / version / "runtime"
    trusted_checks = {
        "runtime": _verify_tree_manifest(
            runtime, runtime_manifest, f"trusted runtime {version} before execution"
        ),
        "browsers": {
            target: _verify_tree_manifest(
                root / "browsers" / target,
                browser_manifests[target],
                f"trusted browser {target} before execution",
            )
            for target in _NATIVE_BROWSER_TARGETS
        },
    }
    executable = runtime / "bin" / "roi-h"
    environment = _native_environment(
        ROI_H_HOME=str(roi_h_home),
        ROI_H_INSTALL_ROOT=str(root),
        PLAYWRIGHT_BROWSERS_PATH=str(root / "browsers"),
    )
    version_result = _require_success(
        _run_capture([str(executable), "--version"], env=environment), f"roi-h {version} --version"
    )
    if version_result["stdout"] != "roi-h 0.1.3":
        raise RuntimeError(f"unexpected roi-h version output: {version_result['stdout']}")
    doctor_result = _require_success(
        _run_capture(
            [
                str(executable),
                "doctor",
                "--output",
                "json",
                "--install-root",
                str(root),
                "--home",
                str(roi_h_home),
            ],
            env=environment,
        ),
        f"roi-h {version} doctor",
    )
    doctor = json.loads(doctor_result["stdout"])
    if not doctor.get("ok"):
        raise RuntimeError(f"roi-h {version} doctor was not healthy: {doctor}")
    probe_code = """
import importlib.metadata as m, json, sys
import activegraph, playwright, roi_h
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    executable_path = p.chromium.executable_path
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    answer = page.evaluate("6*7 == 42")
    browser_version = browser.version
    browser.close()
print(json.dumps({
    "sys_executable": sys.executable,
    "python_version": m.platform.python_version() if False else ".".join(map(str, sys.version_info[:3])),
    "versions": {name: m.version(name) for name in ("roi-h", "playwright", "activegraph")},
    "module_paths": {"roi_h": roi_h.__file__, "playwright": playwright.__file__, "activegraph": activegraph.__file__},
    "chromium_executable_path": executable_path,
    "chromium_version": browser_version,
    "evaluation": answer,
}))
"""
    probe_result = _require_success(
        _run_capture([str(runtime / "bin" / "python"), "-c", probe_code], env=environment),
        f"runtime and browser probe {version}",
    )
    probe = json.loads(probe_result["stdout"])
    if probe["python_version"] != _NATIVE_IDENTITIES["python"] or probe["versions"] != {
        key: _NATIVE_IDENTITIES[key] for key in ("roi-h", "playwright", "activegraph")
    }:
        raise RuntimeError(f"runtime identities do not match: {probe}")
    runtime_resolved = runtime.resolve()
    paths = [probe["sys_executable"], *probe["module_paths"].values()]
    if any(runtime_resolved not in Path(path).resolve().parents for path in paths):
        raise RuntimeError(f"runtime executable or module escaped portable runtime: {paths}")
    chromium_path = Path(probe["chromium_executable_path"]).resolve()
    if (root / "browsers" / "chromium-1228").resolve() not in chromium_path.parents:
        raise RuntimeError(f"Chromium executable did not use revision 1228: {chromium_path}")
    if probe["evaluation"] is not True:
        raise RuntimeError("real Playwright evaluation did not prove that 6*7 == 42")
    target_manifest_digests = {
        target: {key: manifest[key] for key in ("entry_count", "bytes", "sha256")}
        for target in _NATIVE_BROWSER_TARGETS
        for manifest in [_complete_tree_manifest(root / "browsers" / target)]
    }
    return {
        "version": version,
        "trusted_tree_verification_before_execution": trusted_checks,
        "roi_h_version_command": version_result,
        "doctor_command": doctor_result,
        "doctor": doctor,
        "runtime": probe,
        "playwright": {
            "revision": "1228",
            "targets": list(_NATIVE_BROWSER_TARGETS),
            "executable_path": str(chromium_path),
            "chromium_version": probe["chromium_version"],
            "evaluation_6_times_7_equals_42": probe["evaluation"],
            "target_manifest_digests": target_manifest_digests,
        },
    }


def _native_state(active: str, previous: str | None) -> dict[str, Any]:
    return {
        "active": active,
        "previous_healthy": previous,
        "versions": {
            version: {"browser_targets": list(_NATIVE_BROWSER_TARGETS), "healthy": True}
            for version in ("A", "B")
            if version == "A" or version in {active, previous}
        },
    }


def _install_state(version: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "active_version": version,
        "browser_revision": "chromium-1228",
    }


def _read_activation_state(root: Path) -> dict[str, Any] | None:
    paths = [root / name for name in ("current", "native-state.json", "install-state.json")]
    present = [os.path.lexists(path) for path in paths]
    if not any(present):
        return None
    if not all(present) or not paths[0].is_symlink():
        raise RuntimeError("partial native activation state exists before journaling")
    native_state = json.loads(paths[1].read_text(encoding="utf-8"))
    install_state = json.loads(paths[2].read_text(encoding="utf-8"))
    version = str(native_state.get("active"))
    if (
        version not in {"A", "B"}
        or paths[0].readlink() != Path("versions") / version
        or install_state != _install_state(version)
    ):
        raise RuntimeError("native activation state does not agree before journaling")
    return {
        "version": version,
        "native_state": native_state,
        "install_state": install_state,
    }


def _native_prepare_activation(
    root: Path, version: str, state: dict[str, Any], journey_id: str
) -> None:
    if version not in {"A", "B"} or state.get("active") != version:
        raise RuntimeError("invalid native activation journal")
    marker_path = root / _OWNER_MARKER
    marker_stat = marker_path.lstat()
    if not stat.S_ISREG(marker_stat.st_mode) or marker_path.is_symlink():
        raise RuntimeError("native activation owner marker is not a regular file")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker != {"journey_id": journey_id}:
        raise RuntimeError("native activation ownership marker mismatch")
    journal_path = root / "activation-journal.json"
    if os.path.lexists(journal_path):
        raise RuntimeError("native activation journal already exists")
    _write_json_atomic(
        journal_path,
        {
            "journey_id": journey_id,
            "desired_version": version,
            "desired_state": state,
            "prior_state": _read_activation_state(root),
        },
    )


def _set_current(root: Path, version: str, journey_id: str) -> None:
    temporary = root / f".current-{journey_id}"
    if os.path.lexists(temporary):
        temporary.unlink()
    temporary.symlink_to(Path("versions") / version)
    temporary.replace(root / "current")


def _verify_activation_trees(
    root: Path,
    version: str,
    runtime_manifests: dict[str, dict[str, Any]],
    browser_manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "runtime": _verify_tree_manifest(
            root / "versions" / version / "runtime",
            runtime_manifests[version],
            f"active runtime {version} before stable launcher",
        ),
        "browsers": {
            target: _verify_tree_manifest(
                root / "browsers" / target,
                browser_manifests[target],
                f"active browser {target} before stable launcher",
            )
            for target in _NATIVE_BROWSER_TARGETS
        },
    }


def _finish_activation(
    root: Path,
    version: str,
    state: dict[str, Any],
    journey_id: str,
    runtime_manifests: dict[str, dict[str, Any]],
    browser_manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    _set_current(root, version, journey_id)
    _write_json_atomic(root / "native-state.json", state)
    _write_json_atomic(root / "install-state.json", _install_state(version))
    trusted = _verify_activation_trees(root, version, runtime_manifests, browser_manifests)
    launcher_content = _native_launcher_content()
    _verify_file(root / "bin" / "roi-h", len(launcher_content), _sha256(launcher_content))
    trusted["stable_launcher"] = {
        "bytes": len(launcher_content),
        "sha256": _sha256(launcher_content),
        "matched": True,
    }
    launcher = _require_success(
        _run_capture(
            [str(root / "bin" / "roi-h"), "--version"],
            env=_native_environment(),
        ),
        "stable launcher",
    )
    if launcher["stdout"] != "roi-h 0.1.3":
        raise RuntimeError("stable launcher did not run the activated ROI-H")
    if (
        (root / "current").readlink() != Path("versions") / version
        or json.loads((root / "native-state.json").read_text(encoding="utf-8")) != state
        or json.loads((root / "install-state.json").read_text(encoding="utf-8"))
        != _install_state(version)
    ):
        raise RuntimeError("activation files stopped agreeing after stable launcher success")
    (root / "activation-journal.json").unlink()
    return {
        "active": version,
        "pointer": str((root / "current").readlink()),
        "trusted_tree_verification_before_launcher": trusted,
        "launcher": launcher,
    }


def _native_reconcile(
    root: Path,
    journey_id: str,
    runtime_manifests: dict[str, dict[str, Any]],
    browser_manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    journal_path = root / "activation-journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if (
        set(journal)
        != {
            "journey_id",
            "desired_version",
            "desired_state",
            "prior_state",
        }
        or journal.get("journey_id") != journey_id
    ):
        raise RuntimeError("invalid native activation journal during reconciliation")
    desired_version = str(journal["desired_version"])
    desired_state = dict(journal["desired_state"])
    prior = journal["prior_state"]
    if desired_version not in {"A", "B"} or desired_state.get("active") != desired_version:
        raise RuntimeError("invalid desired activation state during reconciliation")
    pointer_path = root / "current"
    pointer = pointer_path.readlink() if pointer_path.is_symlink() else None
    desired_pointer = Path("versions") / desired_version
    prior_pointer = Path("versions") / str(prior["version"]) if prior is not None else None
    if pointer == desired_pointer or prior is None:
        selected_version = desired_version
        selected_state = desired_state
        convergence = "desired"
    elif pointer == prior_pointer:
        selected_version = str(prior["version"])
        selected_state = dict(prior["native_state"])
        if dict(prior["install_state"]) != _install_state(selected_version):
            raise RuntimeError("journal prior install state is invalid")
        convergence = "prior"
    else:
        raise RuntimeError(
            f"activation pointer {pointer!s} matches neither desired nor prior journal state"
        )
    activation = _finish_activation(
        root,
        selected_version,
        selected_state,
        journey_id,
        runtime_manifests,
        browser_manifests,
    )
    return {"journal_reconciled": True, "convergence": convergence, **activation}


def _native_activate(
    root: Path,
    version: str,
    state: dict[str, Any],
    journey_id: str,
    runtime_manifests: dict[str, dict[str, Any]],
    browser_manifests: dict[str, dict[str, Any]],
    *,
    crash_at: str | None = None,
) -> dict[str, Any]:
    crash_codes = {
        "before_pointer": 75,
        "after_pointer": 76,
        "after_native_state": 77,
        "after_install_state": 78,
    }
    if crash_at is not None and crash_at not in crash_codes:
        raise ValueError(f"invalid activation crash boundary: {crash_at}")
    _native_prepare_activation(root, version, state, journey_id)
    if crash_at == "before_pointer":
        os._exit(crash_codes[crash_at])
    _set_current(root, version, journey_id)
    if crash_at == "after_pointer":
        os._exit(crash_codes[crash_at])
    _write_json_atomic(root / "native-state.json", state)
    if crash_at == "after_native_state":
        os._exit(crash_codes[crash_at])
    _write_json_atomic(root / "install-state.json", _install_state(version))
    if crash_at == "after_install_state":
        os._exit(crash_codes[crash_at])
    return _finish_activation(
        root, version, state, journey_id, runtime_manifests, browser_manifests
    )


def _native_rollback(
    root: Path,
    version: str,
    state: dict[str, Any],
    roi_h_home: Path,
    journey_id: str,
    runtime_manifests: dict[str, dict[str, Any]],
    browser_manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    verification = _native_version_probe(
        root,
        version,
        roi_h_home,
        runtime_manifests[version],
        browser_manifests,
    )
    rollback_state = _native_state(version, str(state["active"]))
    activation = _native_activate(
        root,
        version,
        rollback_state,
        journey_id,
        runtime_manifests,
        browser_manifests,
    )
    return {
        "offline_input_contract": True,
        "package_source": False,
        "archive_source": False,
        "network_access_blocked": False,
        "verification_repeated_before_activation": verification,
        "activation": activation,
    }


def _owned_marker_matches(root: Path, journey_id: str) -> bool:
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink():
        return False
    marker = root / _OWNER_MARKER
    try:
        marker_stat = marker.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(marker_stat.st_mode) or marker.is_symlink():
        return False
    try:
        return bool(json.loads(marker.read_text(encoding="utf-8")) == {"journey_id": journey_id})
    except (OSError, ValueError):
        return False


def _remove_owned_root(root: Path) -> None:
    allowed_root = {
        "bin",
        "versions",
        "browsers",
        "current",
        "native-state.json",
        "install-state.json",
        "activation-journal.json",
        _OWNER_MARKER,
    }
    unknown_root = sorted(path.name for path in root.iterdir() if path.name not in allowed_root)
    for parent_name in ("bin", "versions", "browsers"):
        parent = root / parent_name
        if os.path.lexists(parent) and (parent.is_symlink() or not parent.is_dir()):
            raise RuntimeError(f"owned parent path is not a real directory: {parent}")
    for file_name in (
        "current",
        "native-state.json",
        "install-state.json",
        "activation-journal.json",
        _OWNER_MARKER,
    ):
        path = root / file_name
        if os.path.lexists(path) and path.is_dir() and not path.is_symlink():
            raise RuntimeError(f"owned file path is unexpectedly a directory: {path}")
    unknown_versions = (
        sorted(path.name for path in (root / "versions").iterdir() if path.name not in {"A", "B"})
        if (root / "versions").is_dir() and not (root / "versions").is_symlink()
        else []
    )
    unknown_browsers = (
        sorted(
            path.name
            for path in (root / "browsers").iterdir()
            if path.name not in _NATIVE_BROWSER_TARGETS
        )
        if (root / "browsers").is_dir() and not (root / "browsers").is_symlink()
        else []
    )
    unknown_bin = (
        sorted(path.name for path in (root / "bin").iterdir() if path.name != "roi-h")
        if (root / "bin").is_dir() and not (root / "bin").is_symlink()
        else []
    )
    if unknown_root or unknown_versions or unknown_browsers or unknown_bin:
        raise RuntimeError(
            "refusing cleanup because unknown managed-root content remains: "
            f"root={unknown_root}, versions={unknown_versions}, "
            f"browsers={unknown_browsers}, bin={unknown_bin}"
        )
    for version in ("A", "B"):
        path = root / "versions" / version
        if os.path.lexists(path):
            if path.is_symlink() or not path.is_dir():
                raise RuntimeError(f"owned version path is not a real directory: {path}")
            shutil.rmtree(path)
    for target in _NATIVE_BROWSER_TARGETS:
        path = root / "browsers" / target
        if os.path.lexists(path):
            if path.is_symlink() or not path.is_dir():
                raise RuntimeError(f"owned browser path is not a real directory: {path}")
            shutil.rmtree(path)
    for relative in (
        "bin/roi-h",
        "current",
        "native-state.json",
        "install-state.json",
        "activation-journal.json",
        _OWNER_MARKER,
    ):
        path = root / relative
        if os.path.lexists(path):
            if path.is_dir() and not path.is_symlink():
                raise RuntimeError(f"owned file path is unexpectedly a directory: {path}")
            path.unlink()
    for path in (root / "bin", root / "versions", root / "browsers", root):
        if path.exists():
            path.rmdir()


def _native_cleanup_locked(
    root: Path, home: Path, journey_id: str, tracked_receipts: list[str]
) -> dict[str, Any]:
    allowed = set(_NATIVE_RECEIPTS.values())
    if any(identifier not in allowed for identifier in tracked_receipts):
        raise ValueError(
            "cleanup receipt set contains an identifier outside the fixed prototype set"
        )
    marker_matched = _owned_marker_matches(root, journey_id)
    root_cleanup: dict[str, Any] = {"attempted": marker_matched, "errors": []}
    receipt_cleanup: dict[str, Any] = {
        "attempted": marker_matched,
        "errors": [],
        "receipts": {},
    }
    if marker_matched:
        try:
            _remove_owned_root(root)
        except Exception as error:  # noqa: BLE001
            root_cleanup["errors"].append(f"{type(error).__name__}: {error}")
        for identifier in dict.fromkeys(tracked_receipts):
            version = next(
                item for item, expected in _NATIVE_RECEIPTS.items() if expected == identifier
            )
            item: dict[str, Any] = {"identifier": identifier}
            try:
                receipt = _receipt_info(home, identifier)
                if receipt is None:
                    item["already_absent"] = True
                else:
                    observed = str(receipt.get("pkg-version", receipt.get("pkg_version", "")))
                    if observed != _NATIVE_PACKAGE_VERSIONS[version]:
                        raise RuntimeError(
                            f"tracked receipt version mismatch: expected "
                            f"{_NATIVE_PACKAGE_VERSIONS[version]}, observed {observed!r}"
                        )
                    item["forget"] = _run_capture(
                        ["/usr/sbin/pkgutil", "--volume", str(home), "--forget", identifier]
                    )
                    if item["forget"]["returncode"] != 0:
                        raise RuntimeError(
                            str(item["forget"]["stderr"] or item["forget"]["stdout"])
                        )
                item["absent_after"] = not _receipt_present(home, identifier)
                if not item["absent_after"]:
                    raise RuntimeError("receipt remained after cleanup")
            except Exception as error:  # noqa: BLE001
                item["absent_after"] = not _receipt_present(home, identifier)
                receipt_cleanup["errors"].append(f"{identifier}: {type(error).__name__}: {error}")
            receipt_cleanup["receipts"][version] = item
    root_cleanup["root_absent"] = not os.path.lexists(root)
    return {
        "marker_matched": marker_matched,
        "root_cleanup": root_cleanup,
        "receipt_cleanup": receipt_cleanup,
        "root_absent": root_cleanup["root_absent"],
        "receipts": receipt_cleanup["receipts"],
        "tracked_receipts": list(dict.fromkeys(tracked_receipts)),
        "errors": [*root_cleanup["errors"], *receipt_cleanup["errors"]],
    }


def _native_cleanup(
    root: Path, home: Path, journey_id: str, tracked_receipts: list[str]
) -> dict[str, Any]:
    descriptor, lock = _acquire_native_lock(home)
    try:
        return _native_cleanup_locked(root, home, journey_id, tracked_receipts)
    finally:
        _release_native_lock(descriptor, lock)


def _native_journey(args: argparse.Namespace) -> dict[str, Any]:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("native journey requires an arm64 macOS host")
    home = Path.home().resolve()
    root = home.joinpath(*_NATIVE_ROOT_PARTS)
    roi_h_home = args.roi_h_home.expanduser().resolve(strict=True)
    browser_cache = args.browser_cache.expanduser().resolve(strict=True)
    wheel = args.wheel.expanduser().resolve(strict=True)
    output_parent = args.output_dir.expanduser().resolve(strict=True)
    _assert_separate(root, roi_h_home)
    _assert_output_separate(output_parent, root, roi_h_home)
    if wheel.name != "roi_h-0.1.3-py3-none-any.whl":
        raise ValueError(f"expected the already built ROI-H 0.1.3 wheel, got {wheel.name}")
    target_sources = {target: browser_cache / target for target in _NATIVE_BROWSER_TARGETS}
    missing = [str(path) for path in target_sources.values() if not path.is_dir()]
    if missing:
        raise FileNotFoundError(f"exact Playwright 1.61.0 target set is missing: {missing}")
    journey_id = str(uuid.uuid4())
    output_dir = output_parent / journey_id
    output_dir.mkdir(mode=0o700, exist_ok=False)
    output_dir.chmod(0o700)
    result_path = output_dir / "result.json"
    home_before = _tree_identity(roi_h_home)
    tracked_receipts: list[str] = []
    result: dict[str, Any] = {
        "ok": False,
        "mode": "native-journey",
        "prototype": "Wayfinder issue #14",
        "journey_id": journey_id,
        "unsigned": True,
        "native_installer_executed": False,
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "macos_version": platform.mac_ver()[0],
        },
        "managed_root": str(root),
        "artifact_dir": str(output_dir),
        "result_path": str(result_path),
        "tracked_receipts": tracked_receipts,
        "roi_h_home": {"before": home_before},
        "limitations": list(_NATIVE_LIMITATIONS),
    }
    _write_json_atomic(result_path, result)
    descriptor, lock = _acquire_native_lock(home)
    try:
        if os.path.lexists(root):
            raise FileExistsError(f"fixed native managed root already exists: {root}")
        existing_receipts = [
            identifier
            for identifier in _NATIVE_RECEIPTS.values()
            if _receipt_present(home, identifier)
        ]
        if existing_receipts:
            raise FileExistsError(
                f"exact prototype receipts already exist on $HOME: {existing_receipts}"
            )
        with tempfile.TemporaryDirectory(prefix="private-build-", dir=output_dir) as temporary:
            work = Path(temporary)
            payload_a_root = work / "payload-a" / Path(*_NATIVE_ROOT_PARTS)
            payload_b_root = work / "payload-b" / Path(*_NATIVE_ROOT_PARTS)
            runtime_build = _copy_portable_runtime(
                payload_a_root / "versions" / "A" / "runtime", wheel, output_dir
            )
            shutil.copytree(
                payload_a_root / "versions" / "A" / "runtime",
                payload_b_root / "versions" / "B" / "runtime",
                symlinks=True,
            )
            browser_manifests: dict[str, Any] = {}
            for target, source in target_sources.items():
                destination = payload_a_root / "browsers" / target
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, destination, symlinks=True)
                browser_manifests[target] = _complete_tree_manifest(destination)
            _native_launcher(payload_a_root / "bin" / "roi-h")
            (payload_a_root / _OWNER_MARKER).write_text(
                json.dumps({"journey_id": journey_id}, sort_keys=True) + "\n", encoding="utf-8"
            )
            runtime_manifests = {
                "A": _complete_tree_manifest(payload_a_root / "versions" / "A" / "runtime"),
                "B": _complete_tree_manifest(payload_b_root / "versions" / "B" / "runtime"),
            }
            releases: dict[str, Any] = {}
            for version, payload_root in (("A", payload_a_root), ("B", payload_b_root)):
                release = {
                    "version": version,
                    "application_version": "0.1.3",
                    "runtime_manifest": runtime_manifests[version],
                    "browser_target_manifests": browser_manifests,
                }
                release_path = payload_root / "versions" / version / "release-manifest.json"
                _write_json_atomic(release_path, release)
                releases[version] = release
                _write_json_atomic(output_dir / f"release-manifest-{version}.json", release)
            trusted_runtime_a = work / "trusted-runtime-A"
            shutil.copytree(
                payload_a_root / "versions" / "A" / "runtime",
                trusted_runtime_a,
                symlinks=True,
            )
            managed_root_manifests = {
                "A": _complete_tree_manifest(payload_a_root),
                "B": _complete_tree_manifest(payload_b_root),
            }
            package_a = work / "roi-h-issue14-A-unsigned.pkg"
            package_b = work / "roi-h-issue14-B-unsigned.pkg"
            packages = {
                "A": _build_native_package(
                    package_a, work / "payload-a", "A", managed_root_manifests["A"]
                ),
                "B": _build_native_package(
                    package_b, work / "payload-b", "B", managed_root_manifests["B"]
                ),
            }
            result.update(
                portable_runtime_build=runtime_build,
                browser_target_manifests={
                    target: {
                        "entry_count": manifest["entry_count"],
                        "bytes": manifest["bytes"],
                        "sha256": manifest["sha256"],
                        "artifact": str(output_dir / f"browser-manifest-{target}.json"),
                    }
                    for target, manifest in browser_manifests.items()
                },
                runtime_manifests={
                    version: {
                        "entry_count": manifest["entry_count"],
                        "bytes": manifest["bytes"],
                        "sha256": manifest["sha256"],
                        "artifact": str(output_dir / f"release-manifest-{version}.json"),
                    }
                    for version, manifest in runtime_manifests.items()
                },
                managed_root_manifests={
                    version: {
                        "entry_count": manifest["entry_count"],
                        "bytes": manifest["bytes"],
                        "sha256": manifest["sha256"],
                        "artifact": str(output_dir / f"managed-root-manifest-{version}.json"),
                    }
                    for version, manifest in managed_root_manifests.items()
                },
            )
            for target, manifest in browser_manifests.items():
                _write_json_atomic(output_dir / f"browser-manifest-{target}.json", manifest)
            for version, manifest in managed_root_manifests.items():
                _write_json_atomic(output_dir / f"managed-root-manifest-{version}.json", manifest)
            _write_json_atomic(result_path, result)

            installs: dict[str, Any] = {}
            installed_checks: dict[str, Any] = {}
            verifications: dict[str, Any] = {}
            events: list[dict[str, Any]] = []
            if os.path.lexists(root) or any(
                _receipt_present(home, identifier) for identifier in _NATIVE_RECEIPTS.values()
            ):
                raise FileExistsError(
                    "native root or receipt appeared immediately before A install"
                )
            installs["A"] = _native_install(
                package_a,
                home,
                "A",
                int(packages["A"]["bytes"]),
                str(packages["A"]["sha256"]),
                tracked_receipts,
            )
            result.update(
                native_installer_executed=True,
                tracked_receipts=list(tracked_receipts),
                installs={"A": installs["A"]},
            )
            _write_json_atomic(result_path, result)
            artifact_a = output_dir / "roi-h-issue14-A-unsigned.pkg"
            shutil.copyfile(package_a, artifact_a)
            _verify_file(artifact_a, int(packages["A"]["bytes"]), str(packages["A"]["sha256"]))
            packages["A"]["path"] = str(artifact_a)
            result.update(
                native_installer_executed=True,
                tracked_receipts=list(tracked_receipts),
                installs={"A": installs["A"]},
                packages={"A": packages["A"]},
            )
            _write_json_atomic(result_path, result)
            installed_checks["runtime_A"] = _verify_tree_manifest(
                root / "versions" / "A" / "runtime", runtime_manifests["A"], "installed runtime A"
            )
            for target, manifest in browser_manifests.items():
                installed_checks[f"browser_{target}"] = _verify_tree_manifest(
                    root / "browsers" / target, manifest, f"installed browser {target}"
                )
            ownership_a = _assert_current_user_owned(root)
            verifications["A_before_activation"] = _native_version_probe(
                root, "A", roi_h_home, runtime_manifests["A"], browser_manifests
            )
            state_a = _native_state("A", None)
            events.append(
                {
                    "event": "activate_A",
                    **_native_activate(
                        root,
                        "A",
                        state_a,
                        journey_id,
                        runtime_manifests,
                        browser_manifests,
                    ),
                }
            )
            installs["B"] = _native_install(
                package_b,
                home,
                "B",
                int(packages["B"]["bytes"]),
                str(packages["B"]["sha256"]),
                tracked_receipts,
            )
            result.update(tracked_receipts=list(tracked_receipts), installs=installs)
            _write_json_atomic(result_path, result)
            artifact_b = output_dir / "roi-h-issue14-B-unsigned.pkg"
            shutil.copyfile(package_b, artifact_b)
            _verify_file(artifact_b, int(packages["B"]["bytes"]), str(packages["B"]["sha256"]))
            packages["B"]["path"] = str(artifact_b)
            result.update(
                tracked_receipts=list(tracked_receipts), installs=installs, packages=packages
            )
            _write_json_atomic(result_path, result)
            installed_checks["runtime_B"] = _verify_tree_manifest(
                root / "versions" / "B" / "runtime", runtime_manifests["B"], "installed runtime B"
            )
            for target, manifest in browser_manifests.items():
                installed_checks[f"retained_browser_{target}"] = _verify_tree_manifest(
                    root / "browsers" / target, manifest, f"retained browser {target}"
                )
            ownership_b = _assert_current_user_owned(root)
            verifications["B_before_activation"] = _native_version_probe(
                root, "B", roi_h_home, runtime_manifests["B"], browser_manifests
            )
            state_b = _native_state("B", "A")
            child_code = (
                "import json,sys; from pathlib import Path; "
                "from roi_h.harness.macos_native_package_prototype import _native_activate; "
                "_native_activate(Path(sys.argv[1]),sys.argv[2],json.loads(sys.argv[3]),"
                "sys.argv[4],{},{},crash_at=sys.argv[5])"
            )
            crash_cases = (
                ("before_pointer", "B", state_b, 75, "A", "prior"),
                ("after_pointer", "B", state_b, 76, "B", "desired"),
                ("after_native_state", "A", _native_state("A", "B"), 77, "A", "desired"),
                ("after_install_state", "B", state_b, 78, "B", "desired"),
            )
            activation_recovery: dict[str, Any] = {}
            for (
                boundary,
                version,
                desired_state,
                exit_code,
                expected_active,
                convergence,
            ) in crash_cases:
                child = _run_capture(
                    [
                        sys.executable,
                        "-c",
                        child_code,
                        str(root),
                        version,
                        json.dumps(desired_state),
                        journey_id,
                        boundary,
                    ]
                )
                if child["returncode"] != exit_code:
                    raise RuntimeError(
                        f"activation {boundary} child returned {child['returncode']}, "
                        f"expected {exit_code}"
                    )
                if not (root / "activation-journal.json").is_file():
                    raise RuntimeError(f"activation journal missing after {boundary} child exit")
                recovery = _native_reconcile(root, journey_id, runtime_manifests, browser_manifests)
                if recovery["active"] != expected_active or recovery["convergence"] != convergence:
                    raise RuntimeError(f"activation {boundary} recovered incorrectly: {recovery}")
                activation_recovery[boundary] = {
                    "child_returncode": child["returncode"],
                    **recovery,
                }
                events.append(
                    {
                        "event": f"recover_activation_{boundary}",
                        "child_returncode": child["returncode"],
                        **recovery,
                    }
                )

            tamper_sentinel = output_dir / "tampered-A-executed"
            tamper_target = root / "versions" / "A" / "runtime" / "bin" / "roi-h"
            tamper_target.write_text(
                f"#!/bin/sh\ntouch {str(tamper_sentinel)!r}\nexit 99\n", encoding="utf-8"
            )
            tamper_target.chmod(0o755)
            tamper_error = ""
            try:
                _native_version_probe(
                    root, "A", roi_h_home, runtime_manifests["A"], browser_manifests
                )
            except RuntimeError as error:
                tamper_error = str(error)
            if not tamper_error or tamper_sentinel.exists():
                raise RuntimeError("retained A tamper was not rejected before code execution")
            if (root / "current").readlink() != Path("versions/B"):
                raise RuntimeError("B did not remain active after retained A tamper rejection")
            installed_runtime_a = root / "versions" / "A" / "runtime"
            shutil.rmtree(installed_runtime_a)
            shutil.copytree(trusted_runtime_a, installed_runtime_a, symlinks=True)
            restored_a = _verify_tree_manifest(
                installed_runtime_a, runtime_manifests["A"], "restored trusted runtime A"
            )
            tamper_rejection = {
                "rejected": True,
                "error": tamper_error,
                "tampered_code_executed": False,
                "B_remained_active": True,
                "restored_from_journey_owned_trusted_copy": restored_a,
            }
            events.append({"event": "retained_A_tamper_rejected"})

            rollback = _native_rollback(
                root,
                "A",
                state_b,
                roi_h_home,
                journey_id,
                runtime_manifests,
                browser_manifests,
            )
            events.append(
                {"event": "offline_rollback_A", "active": rollback["activation"]["active"]}
            )
            retained = {
                "versions": {
                    version: _verify_tree_manifest(
                        root / "versions" / version / "runtime",
                        runtime_manifests[version],
                        f"final retained runtime {version}",
                    )
                    for version in ("A", "B")
                },
                "browser_targets": {
                    target: _verify_tree_manifest(
                        root / "browsers" / target,
                        browser_manifests[target],
                        f"final retained browser {target}",
                    )
                    for target in _NATIVE_BROWSER_TARGETS
                },
                "exact": True,
            }
            home_after = _tree_identity(roi_h_home)
            if home_after != home_before:
                raise RuntimeError("bounded ROI_H_HOME identity changed during native journey")
            result.update(
                installs=installs,
                installed_tree_verification=installed_checks,
                ownership={"after_A": ownership_a, "after_B": ownership_b},
                version_verifications=verifications,
                events=events,
                update_recovery={"activation_boundaries": activation_recovery},
                tamper_rejection=tamper_rejection,
                rollback=rollback,
                retained=retained,
                native_state=json.loads((root / "native-state.json").read_text(encoding="utf-8")),
                roi_h_home={"before": home_before, "after": home_after, "unchanged": True},
                ok=True,
            )
            _write_json_atomic(result_path, result)
    except Exception as error:  # noqa: BLE001
        result.update(ok=False, error_type=type(error).__name__, error=str(error))
        _write_json_atomic(result_path, result)
    finally:
        try:
            cleanup = _native_cleanup_locked(root, home, journey_id, tracked_receipts)
            result["cleanup"] = cleanup
            if result.get("ok") and (
                not cleanup["root_absent"]
                or cleanup["errors"]
                or not all(item["absent_after"] for item in cleanup["receipts"].values())
            ):
                result.update(
                    ok=False, error="native cleanup did not remove the owned root and receipts"
                )
            _write_json_atomic(result_path, result)
        finally:
            _release_native_lock(descriptor, lock)
    return result


def _cleanup_result(result_path: Path) -> dict[str, Any]:
    path = result_path.expanduser().resolve(strict=True)
    path_stat = path.lstat()
    if not stat.S_ISREG(path_stat.st_mode) or path.is_symlink() or path_stat.st_size > 5_000_000:
        raise ValueError("cleanup result must be a bounded regular non-symlink JSON file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("cleanup result JSON must be an object")
    journey_id = str(value.get("journey_id", ""))
    try:
        if str(uuid.UUID(journey_id)) != journey_id:
            raise ValueError
    except ValueError:
        raise ValueError("cleanup result has an invalid journey id") from None
    tracked = value.get("tracked_receipts")
    if not isinstance(tracked, list) or any(not isinstance(item, str) for item in tracked):
        raise ValueError("cleanup result has an invalid tracked receipt set")
    home = Path.home().resolve()
    root = home.joinpath(*_NATIVE_ROOT_PARTS)
    cleanup = _native_cleanup(root, home, journey_id, tracked)
    return {
        "ok": cleanup["root_absent"] and not cleanup["errors"],
        "mode": "cleanup",
        "journey_id": journey_id,
        "managed_root": str(root),
        "cleanup": cleanup,
    }


def _native_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unsigned native Installer journey for issue #14")
    parser.add_argument("--roi-h-home", type=Path, required=True)
    parser.add_argument("--browser-cache", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def _cleanup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean an owned issue #14 native journey")
    parser.add_argument("--result", type=Path, required=True)
    return parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PROTOTYPE for Wayfinder issue #14")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-pkg", type=Path, required=True)
    parser.add_argument("--roi-h-home", type=Path, required=True)
    for version in ("a", "b"):
        parser.add_argument(f"--browser-{version}", type=Path, required=True)
        parser.add_argument(f"--browser-{version}-bytes", type=int, required=True)
        parser.add_argument(f"--browser-{version}-sha256", required=True)
        parser.add_argument(f"--browser-{version}-target", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    command = arguments[0] if arguments else "scenario"
    native = command == "native-journey"
    cleanup = command == "cleanup"
    try:
        if native:
            evidence = _native_journey(_native_parser().parse_args(arguments[1:]))
        elif cleanup:
            evidence = _cleanup_result(_cleanup_parser().parse_args(arguments[1:]).result)
        else:
            evidence = _scenario(_parser().parse_args(arguments))
    except Exception as error:  # noqa: BLE001
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "prototype": "Wayfinder issue #14",
                    "evidence_scope": (
                        "unsigned-native-journey"
                        if native
                        else "owned-native-cleanup"
                        if cleanup
                        else "package-shape-and-transaction-scenario"
                    ),
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 1
    sys.stdout.write(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
