"""Local release bundle builder tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPOSITORY_ROOT / "scripts" / "build_release_bundle.py"


def _run_builder(
    wheelhouse: Path,
    output: Path,
    *,
    version: str = "1.2.3",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(BUILDER),
            "--wheelhouse",
            str(wheelhouse),
            "--output",
            str(output),
            "--version",
            version,
            "--installer-version",
            "0.4.0",
            "--python-version",
            "3.12.13",
            "--browser-revision",
            "chromium-1234",
            "--activegraph-version",
            "1.10.0",
            "--channel",
            "stable",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_builder_creates_hashed_self_contained_release_bundle(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    application_wheel = wheelhouse / "roi_h-1.2.3-py3-none-any.whl"
    dependency_wheel = wheelhouse / "dependency-4.5.6-py3-none-any.whl"
    installer_wheel = wheelhouse / "roi_h_installer-0.4.0-py3-none-any.whl"
    application_wheel.write_bytes(b"application-wheel")
    dependency_wheel.write_bytes(b"dependency-wheel")
    installer_wheel.write_bytes(b"installer-wheel")
    output = tmp_path / "roi-h-release-1.2.3.tar.gz"

    completed = _run_builder(wheelhouse, output)

    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result == {
        "bundle": str(output),
        "channel": "stable",
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "target_count": 3,
        "version": "1.2.3",
    }
    assert completed.stderr == ""

    with tarfile.open(output, "r:gz") as archive:
        assert archive.getnames() == [
            "release.json",
            dependency_wheel.name,
            application_wheel.name,
            installer_wheel.name,
        ]
        release_stream = archive.extractfile("release.json")
        assert release_stream is not None
        release = json.loads(release_stream.read())
        assert release["schema_version"] == "1.0"
        assert release["version"] == "1.2.3"
        assert release["application_target"] == application_wheel.name
        assert release["installer_target"] == installer_wheel.name
        assert release["installer_version"] == "0.4.0"
        assert release["python_version"] == "3.12.13"
        assert release["browser_revision"] == "chromium-1234"
        assert release["data_compatibility"] == {
            "activegraph_version": "1.10.0",
            "readable_home_layouts": [1],
            "writable_home_layout": 1,
        }
        assert release["targets"] == [
            {
                "length": len(b"dependency-wheel"),
                "name": dependency_wheel.name,
                "sha256": hashlib.sha256(b"dependency-wheel").hexdigest(),
            },
            {
                "length": len(b"application-wheel"),
                "name": application_wheel.name,
                "sha256": hashlib.sha256(b"application-wheel").hexdigest(),
            },
            {
                "length": len(b"installer-wheel"),
                "name": installer_wheel.name,
                "sha256": hashlib.sha256(b"installer-wheel").hexdigest(),
            },
        ]


def test_builder_rejects_wheelhouse_without_requested_application(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "roi_h-9.9.9-py3-none-any.whl").write_bytes(b"wrong-version")
    (wheelhouse / "roi_h_installer-0.4.0-py3-none-any.whl").write_bytes(b"installer")
    output = tmp_path / "release.tar.gz"

    completed = _run_builder(wheelhouse, output)

    assert completed.returncode == 1
    assert json.loads(completed.stderr)["code"] == "release.version_not_found"
    assert not output.exists()


def test_builder_rejects_wheelhouse_without_requested_installer(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "roi_h-1.2.3-py3-none-any.whl").write_bytes(b"application")
    output = tmp_path / "release.tar.gz"

    completed = _run_builder(wheelhouse, output)

    assert completed.returncode == 1
    assert json.loads(completed.stderr)["code"] == "release.installer_version_not_found"
    assert not output.exists()
