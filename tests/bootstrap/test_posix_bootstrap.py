from __future__ import annotations

import os
import subprocess
import tarfile
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="requires a POSIX shell")


def test_posix_bootstrap_has_a_complete_stable_release_identity() -> None:
    script = (Path(__file__).parents[2] / "install.sh").read_text(encoding="utf-8")

    assert (
        'DEFAULT_RELEASE_BUNDLE_URL="https://github.com/omerlefaruk/roi-h/releases/'
        'download/v0.1.8/roi-h-release-0.1.8.tar.gz"' in script
    )
    assert (
        'DEFAULT_RELEASE_BUNDLE_SHA256="'
        '3ec60498974a95eda648e3ad01143833c91a44a69bcd3f1b4c5199bb922a8b0a"' in script
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_posix_bootstrap_rejects_an_unqualified_platform(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "uname",
        """#!/bin/sh
if [ "${1-}" = "-s" ]; then
    printf 'Linux\n'
else
    printf 'x86_64\n'
fi
""",
    )

    completed = subprocess.run(
        ["/bin/sh", "install.sh"],
        cwd=Path(__file__).parents[2],
        env={**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "supports only macOS ARM64. Detected Linux:x86_64" in completed.stderr


def test_posix_bootstrap_rejects_root_install(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "uname",
        """#!/bin/sh
if [ "${1-}" = "-s" ]; then
    printf 'Darwin\\n'
else
    printf 'arm64\\n'
fi
""",
    )
    _write_executable(fake_bin / "id", "#!/bin/sh\nprintf '0\\n'\n")

    completed = subprocess.run(
        ["/bin/sh", "install.sh"],
        cwd=Path(__file__).parents[2],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "Do not run the user installer as root or with sudo" in completed.stderr


@pytest.mark.parametrize(
    ("managed", "expected_operation"),
    [(False, "install"), (True, "update")],
)
def test_posix_bootstrap_pins_tools_and_forwards_staging_release(  # noqa: PLR0915
    tmp_path: Path,
    *,
    managed: bool,
    expected_operation: str,
) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    install_root = tmp_path / "install-root"
    bin_root = tmp_path / "bin-root"
    data_home = tmp_path / "data-home"
    command_log = tmp_path / "commands.log"
    launch_home = tmp_path / "launch-home.txt"
    fake_uv_source = tmp_path / "fake-uv"
    fake_installer_source = tmp_path / "fake-installer"
    fake_bundle_source = tmp_path / "release-bundle.tar.gz"
    with tarfile.open(fake_bundle_source, "w:gz") as archive:
        for name, content in (
            ("release.json", b'{"schema_version":"1.0"}\n'),
            ("roi_h-0.1.0-py3-none-any.whl", b"wheel"),
            ("roi_h_installer-9.8.7-py3-none-any.whl", b"installer"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, BytesIO(content))
    bundle_sha256 = sha256(fake_bundle_source.read_bytes()).hexdigest()
    if managed:
        install_root.mkdir()
        (install_root / "install-state.json").write_text(
            '{"schema_version":1,"active_version":"0.0.9"}\n',
            encoding="utf-8",
        )

    _write_executable(
        fake_uv_source,
        """#!/bin/sh
set -eu
printf 'uv:%s\n' "$*" >> "$FAKE_COMMAND_LOG"
mkdir -p "$UV_TOOL_BIN_DIR"
cp "$FAKE_INSTALLER_SOURCE" "$UV_TOOL_BIN_DIR/roi-h-installer"
chmod +x "$UV_TOOL_BIN_DIR/roi-h-installer"
""",
    )
    _write_executable(
        fake_installer_source,
        """#!/bin/sh
set -eu
printf 'installer:%s\n' "$*" >> "$FAKE_COMMAND_LOG"
printf 'installer-home:%s\n' "${ROI_H_HOME-}" >> "$FAKE_COMMAND_LOG"
release_description=
while [ "$#" -gt 0 ]; do
    if [ "$1" = "--release-description" ]; then
        release_description=$2
        shift 2
    else
        shift
    fi
done
[ -f "$release_description" ]
release_root=$(dirname "$release_description")
wheel_found=false
for wheel in "$release_root"/*.whl; do
    if [ -f "$wheel" ]; then
        wheel_found=true
    fi
done
[ "$wheel_found" = true ]
mkdir -p "$FAKE_INSTALL_ROOT/current/bin"
printf '#!/bin/sh\nprintf "%%s" "${ROI_H_HOME-}" > "$FAKE_LAUNCH_HOME"\n' > "$FAKE_INSTALL_ROOT/current/bin/roi-h"
chmod +x "$FAKE_INSTALL_ROOT/current/bin/roi-h"
printf '{"changed":true}\n'
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
set -eu
output=
url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output)
            output=$2
            shift 2
            ;;
        *)
            url=$1
            shift
            ;;
    esac
done
printf 'curl:%s:%s\n' "$url" "$output" >> "$FAKE_COMMAND_LOG"
case "$url" in
    *astral.sh*)
        cat > "$output" <<'UV_INSTALLER'
#!/bin/sh
set -eu
mkdir -p "$UV_UNMANAGED_INSTALL"
cp "$FAKE_UV_SOURCE" "$UV_UNMANAGED_INSTALL/uv"
chmod +x "$UV_UNMANAGED_INSTALL/uv"
UV_INSTALLER
        ;;
    *)
        cp "$FAKE_BUNDLE_SOURCE" "$output"
        ;;
esac
""",
    )
    _write_executable(
        fake_bin / "shasum",
        """#!/bin/sh
set -eu
last=
for value in "$@"; do
    last=$value
done
case "$last" in
    *uv-installer.sh)
        digest=b9f925505899533f36a3acfdf8684c661ff2d5c8735f759fca768367b5996123
        ;;
    *)
        digest=$FAKE_BUNDLE_SHA256
        ;;
esac
printf '%s  %s\n' "$digest" "$last"
""",
    )

    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "temporary"),
        "ROI_H_HOME": str(data_home),
        "ROI_H_INSTALL_ROOT": str(install_root),
        "XDG_BIN_HOME": str(bin_root),
        "ROI_H_INSTALLER_VERSION": "9.8.7",
        "ROI_H_RELEASE_BUNDLE_URL": "https://staging.example/release-bundle.tar.gz",
        "ROI_H_RELEASE_BUNDLE_SHA256": bundle_sha256,
        "FAKE_COMMAND_LOG": str(command_log),
        "FAKE_LAUNCH_HOME": str(launch_home),
        "FAKE_UV_SOURCE": str(fake_uv_source),
        "FAKE_INSTALLER_SOURCE": str(fake_installer_source),
        "FAKE_INSTALL_ROOT": str(install_root),
        "FAKE_BUNDLE_SOURCE": str(fake_bundle_source),
        "FAKE_BUNDLE_SHA256": bundle_sha256,
    }
    Path(environment["TMPDIR"]).mkdir()

    completed = subprocess.run(
        ["/bin/sh", "install.sh"],
        cwd=Path(__file__).parents[2],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == '{"changed":true}\n'
    log = command_log.read_text(encoding="utf-8")
    assert "curl:https://astral.sh/uv/0.11.16/install.sh:" in log
    assert "curl:https://staging.example/release-bundle.tar.gz:" in log
    assert "roi-h-installer==9.8.7" in log
    assert "--no-index" in log
    assert "--find-links " in log
    assert "--python 3.12.13" in log
    assert f"installer:{expected_operation} --release-description " in log
    assert f"--install-root {install_root}" in log
    assert f"--data-home {data_home}" in log
    assert "--output json" in log
    assert f"installer-home:{data_home}" in log
    launcher = bin_root / "roi-h"
    assert launcher.is_file()
    assert not launcher.is_symlink()
    launcher_text = launcher.read_text(encoding="utf-8")
    assert "PLAYWRIGHT_BROWSERS_PATH" in launcher_text
    assert "PLAYWRIGHT_SKIP_BROWSER_GC=1" in launcher_text
    launch_environment = {**os.environ, "FAKE_LAUNCH_HOME": str(launch_home)}
    launch_environment.pop("ROI_H_HOME", None)
    assert (
        subprocess.run([launcher], env=launch_environment, check=False).returncode == 0  # noqa: S603
    )
    assert launch_home.read_text(encoding="utf-8") == str(data_home)
    updater = install_root / "installer" / "update.sh"
    assert updater.stat().st_mode & 0o111
    assert (
        "https://raw.githubusercontent.com/omerlefaruk/roi-h/main/install.sh"
        in updater.read_text(encoding="utf-8")
    )


def test_posix_bootstrap_rejects_an_unverified_uv_installer(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    install_root = tmp_path / "install-root"
    attempted_execution = tmp_path / "uv-installer-ran"
    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
set -eu
output=
while [ "$#" -gt 0 ]; do
    if [ "$1" = "--output" ]; then
        output=$2
        shift 2
    else
        shift
    fi
done
printf '#!/bin/sh\ntouch "$FAKE_ATTEMPTED_EXECUTION"\n' > "$output"
""",
    )
    _write_executable(
        fake_bin / "shasum",
        """#!/bin/sh
set -eu
last=
for value in "$@"; do
    last=$value
done
printf '%064d  %s\n' 0 "$last"
""",
    )
    temporary_root = tmp_path / "temporary"
    temporary_root.mkdir()
    completed = subprocess.run(
        ["/bin/sh", "install.sh"],
        cwd=Path(__file__).parents[2],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "TMPDIR": str(temporary_root),
            "ROI_H_INSTALL_ROOT": str(install_root),
            "ROI_H_RELEASE_BUNDLE_SHA256": "0" * 64,
            "FAKE_ATTEMPTED_EXECUTION": str(attempted_execution),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "The uv installer checksum is invalid." in completed.stderr
    assert not attempted_execution.exists()
    assert not install_root.exists()


def test_posix_bootstrap_rejects_release_bundle_traversal(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    malicious_bundle = tmp_path / "malicious.tar.gz"
    with tarfile.open(malicious_bundle, "w:gz") as archive:
        for name, content in (
            ("release.json", b"{}\n"),
            ("roi_h-0.1.0-py3-none-any.whl", b"wheel"),
            ("../escaped", b"unsafe"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, BytesIO(content))
    bundle_sha256 = sha256(malicious_bundle.read_bytes()).hexdigest()

    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
set -eu
output=
url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output)
            output=$2
            shift 2
            ;;
        *)
            url=$1
            shift
            ;;
    esac
done
case "$url" in
    *astral.sh*) printf '#!/bin/sh\nexit 99\n' > "$output" ;;
    *) cp "$FAKE_BUNDLE_SOURCE" "$output" ;;
esac
""",
    )
    _write_executable(
        fake_bin / "shasum",
        """#!/bin/sh
set -eu
last=
for value in "$@"; do
    last=$value
done
case "$last" in
    *uv-installer.sh)
        digest=b9f925505899533f36a3acfdf8684c661ff2d5c8735f759fca768367b5996123
        ;;
    *)
        digest=$FAKE_BUNDLE_SHA256
        ;;
esac
printf '%s  %s\n' "$digest" "$last"
""",
    )
    temporary_root = tmp_path / "temporary"
    temporary_root.mkdir()
    install_root = tmp_path / "install-root"
    completed = subprocess.run(
        ["/bin/sh", "install.sh"],
        cwd=Path(__file__).parents[2],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "TMPDIR": str(temporary_root),
            "ROI_H_INSTALL_ROOT": str(install_root),
            "ROI_H_RELEASE_BUNDLE_URL": "https://staging.example/malicious.tar.gz",
            "ROI_H_RELEASE_BUNDLE_SHA256": bundle_sha256,
            "FAKE_BUNDLE_SOURCE": str(malicious_bundle),
            "FAKE_BUNDLE_SHA256": bundle_sha256,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "The release bundle contains an unsafe entry." in completed.stderr
    assert not (tmp_path / "escaped").exists()
    assert not install_root.exists()
