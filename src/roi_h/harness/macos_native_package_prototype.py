"""PROTOTYPE: collect package-shape and transaction-scenario evidence.

This throwaway Wayfinder issue #14 module is not a public API or native acceptance proof.
Run it only as ``python -m roi_h.harness.macos_native_package_prototype``.
"""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

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


def _signature_check(package: Path, signing_identity: str | None) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        ["/usr/sbin/pkgutil", "--check-signature", str(package)],
        check=False,
        capture_output=True,
        text=True,
    )
    confirmed = signing_identity is not None and completed.returncode == 0
    if signing_identity is not None and not confirmed:
        raise RuntimeError(
            "pkgutil did not confirm the requested package signature: "
            f"{(completed.stdout + completed.stderr).strip()}"
        )
    return {
        "status": "signed-and-confirmed" if confirmed else "unsigned",
        "confirmed": confirmed,
        "check_returncode": completed.returncode,
        "check_output": (completed.stdout + completed.stderr).strip(),
    }


def _build_package(output: Path, signing_identity: str | None) -> dict[str, Any]:
    if signing_identity is not None:
        signing_identity = signing_identity.strip()
        if not signing_identity:
            raise ValueError("signing identity must not be blank")
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
        if signing_identity is not None:
            product_command.extend(["--sign", signing_identity])
        product_command.append(str(output))
        commands.append(_run_native(product_command))
    signature = _signature_check(output, signing_identity)
    length, digest = _file_identity(output)
    return {
        "path": str(output.resolve()),
        "bytes": length,
        "sha256": digest,
        "identifier": _PACKAGE_ID,
        "version": _PACKAGE_VERSION,
        "format": "flat-product-pkg",
        "signed": signature["confirmed"],
        "signing_identity": signing_identity,
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
            raise RuntimeError(  # noqa: TRY301
                f"managed browser executable is not executable: {executable}"
            )
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
    if args.signing_identity is not None and not args.signing_identity.strip():
        raise ValueError("signing identity must not be blank")
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"managed prototype root must be absent or empty: {root}")
    if not _NAME.fullmatch(args.browser_a_target) or not _NAME.fullmatch(args.browser_b_target):
        raise ValueError("browser targets must be simple managed directory names")
    if args.browser_a_target == args.browser_b_target:
        raise ValueError("A and B must use distinct browser targets")

    _verify_file(archive_a, args.browser_a_bytes, args.browser_a_sha256)
    _verify_file(archive_b, args.browser_b_bytes, args.browser_b_sha256)
    home_before = _tree_identity(roi_h_home)
    package = _build_package(output, args.signing_identity)
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
                "package signing and notarization",
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
    parser.add_argument("--signing-identity")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        evidence = _scenario(_parser().parse_args(argv))
    except Exception as error:  # noqa: BLE001
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "prototype": "Wayfinder issue #14",
                    "evidence_scope": "package-shape-and-transaction-scenario",
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
