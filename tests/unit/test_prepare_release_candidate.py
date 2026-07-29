"""Local release candidate preparation tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PREPARER = REPOSITORY_ROOT / "scripts" / "prepare_release_candidate.py"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_preparer_builds_locked_wheelhouse_and_bundle(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "uv",
        """#!/bin/sh
set -eu
printf 'uv:%s\n' "$*" >> "$FAKE_COMMAND_LOG"
command=$1
shift
case "$command" in
    export)
        output=
        while [ "$#" -gt 0 ]; do
            if [ "$1" = "--output-file" ]; then
                output=$2
                shift 2
            else
                shift
            fi
        done
        printf 'dependency==4.5.6 --hash=sha256:abc\n' > "$output"
        ;;
    build)
        output=
        directory=
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --out-dir)
                    output=$2
                    shift 2
                    ;;
                --directory)
                    directory=$2
                    shift 2
                    ;;
                *)
                    shift
                    ;;
            esac
        done
        mkdir -p "$output"
        if [ -n "$directory" ]; then
            printf 'installer-wheel' > "$output/roi_h_installer-0.4.0-py3-none-any.whl"
        else
            printf 'application-wheel' > "$output/roi_h-1.2.3-py3-none-any.whl"
        fi
        ;;
esac
""",
    )
    _write_executable(
        fake_bin / "uvx",
        """#!/bin/sh
set -eu
printf 'uvx:%s\n' "$*" >> "$FAKE_COMMAND_LOG"
destination=
while [ "$#" -gt 0 ]; do
    if [ "$1" = "--dest" ]; then
        destination=$2
        shift 2
    else
        shift
    fi
done
printf 'dependency-wheel' > "$destination/dependency-4.5.6-py3-none-any.whl"
""",
    )
    output = tmp_path / "candidate"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "FAKE_COMMAND_LOG": str(command_log),
    }

    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(PREPARER),
            "--repository",
            str(REPOSITORY_ROOT),
            "--output-dir",
            str(output),
            "--version",
            "1.2.3",
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
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["version"] == "1.2.3"
    assert result["target_count"] == 3
    assert len(result["sha256"]) == 64
    assert Path(result["bundle"]) == output / "roi-h-release-1.2.3.tar.gz"
    assert (output / "runtime.lock").is_file()
    assert sorted(path.name for path in (output / "wheelhouse").iterdir()) == [
        "dependency-4.5.6-py3-none-any.whl",
        "roi_h-1.2.3-py3-none-any.whl",
        "roi_h_installer-0.4.0-py3-none-any.whl",
    ]
    with tarfile.open(result["bundle"], "r:gz") as archive:
        assert "release.json" in archive.getnames()

    log = command_log.read_text(encoding="utf-8")
    assert "uv:export --frozen --no-dev --no-emit-project" in log
    assert "uv:build --wheel --no-sources --out-dir" in log
    assert "uv:build --directory " in log
    assert "packages/roi-h-installer --wheel --no-sources --out-dir" in log
    assert "uvx:--python 3.12.13 --from pip==26.0.1 pip download --require-hashes" in log


def test_preparer_refuses_to_replace_a_candidate(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    output.mkdir()

    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(PREPARER),
            "--repository",
            str(REPOSITORY_ROOT),
            "--output-dir",
            str(output),
            "--version",
            "1.2.3",
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

    assert completed.returncode == 1
    assert json.loads(completed.stderr)["code"] == "release.output_exists"
