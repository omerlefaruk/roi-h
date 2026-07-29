"""Release identity gate tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY_ROOT / "scripts" / "check_release_identity.py"


def _write_project(root: Path, version: str) -> Path:
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        f'[project]\nname = "roi-h"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    return pyproject


def _write_wheel(root: Path, version: str, metadata_version: str | None = None) -> Path:
    wheel = root / f"roi_h-{version}-py3-none-any.whl"
    metadata = f"Metadata-Version: 2.4\nName: roi-h\nVersion: {metadata_version or version}\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"roi_h-{version}.dist-info/METADATA", metadata)
    return wheel


def _write_sdist(root: Path, version: str, metadata_version: str | None = None) -> Path:
    sdist = root / f"roi_h-{version}.tar.gz"
    metadata = (
        f"Metadata-Version: 2.4\nName: roi-h\nVersion: {metadata_version or version}\n"
    ).encode()
    info = tarfile.TarInfo(f"roi_h-{version}/PKG-INFO")
    info.size = len(metadata)
    with tarfile.open(sdist, "w:gz") as archive:
        archive.addfile(info, BytesIO(metadata))
    return sdist


def _run_checker(project: Path, wheel: Path, sdist: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(CHECKER),
            "--project",
            str(project),
            "--artifact",
            str(wheel),
            "--artifact",
            str(sdist),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_identity_accepts_matching_project_and_archives(tmp_path: Path) -> None:
    project = _write_project(tmp_path, "1.2.3")
    wheel = _write_wheel(tmp_path, "1.2.3")
    sdist = _write_sdist(tmp_path, "1.2.3")

    completed = _run_checker(project, wheel, sdist)

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "artifacts": [wheel.name, sdist.name],
        "name": "roi-h",
        "ok": True,
        "version": "1.2.3",
    }
    assert completed.stderr == ""


def test_release_identity_rejects_archive_metadata_mismatch(tmp_path: Path) -> None:
    project = _write_project(tmp_path, "1.2.3")
    wheel = _write_wheel(tmp_path, "1.2.3", metadata_version="1.2.4")
    sdist = _write_sdist(tmp_path, "1.2.3")

    completed = _run_checker(project, wheel, sdist)

    assert completed.returncode == 1
    error = json.loads(completed.stderr)
    assert error["code"] == "release.version_mismatch"
    assert "1.2.4" in error["message"]


def test_release_identity_rejects_wheel_filename_mismatch(tmp_path: Path) -> None:
    project = _write_project(tmp_path, "1.2.3")
    wheel = _write_wheel(tmp_path, "1.2.4", metadata_version="1.2.3")
    sdist = _write_sdist(tmp_path, "1.2.3")

    completed = _run_checker(project, wheel, sdist)

    assert completed.returncode == 1
    error = json.loads(completed.stderr)
    assert error["code"] == "release.version_mismatch"
    assert wheel.name in error["message"]
