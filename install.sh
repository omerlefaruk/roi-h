#!/bin/sh

set -eu

UV_VERSION="0.11.16"
UV_INSTALLER_SHA256="b9f925505899533f36a3acfdf8684c661ff2d5c8735f759fca768367b5996123"
PYTHON_VERSION="3.12.13"
DEFAULT_INSTALLER_VERSION="0.1.1"
DEFAULT_RELEASE_BUNDLE_URL="https://github.com/omerlefaruk/roi-h/releases/download/v0.1.1/roi-h-release-0.1.1.tar.gz"
DEFAULT_RELEASE_BUNDLE_SHA256="089bd6a7cd9f56b65d2e9dcef209aeb63f0f8c93a4dc9c057f479f7bcd90872d"

fail() {
    printf 'ROI-H install failed: %s\n' "$1" >&2
    exit 1
}

installer_version=${ROI_H_INSTALLER_VERSION:-$DEFAULT_INSTALLER_VERSION}
release_bundle_url=${ROI_H_RELEASE_BUNDLE_URL:-$DEFAULT_RELEASE_BUNDLE_URL}
release_bundle_sha256=${ROI_H_RELEASE_BUNDLE_SHA256:-$DEFAULT_RELEASE_BUNDLE_SHA256}

platform=$(uname -s):$(uname -m)
case "$platform" in
    Darwin:arm64 | Darwin:aarch64)
        ;;
    *)
        fail "ROI-H 0.1.1 supports only macOS ARM64. Detected $platform."
        ;;
esac

case "$installer_version" in
    '' | *[!0-9A-Za-z.+-]*)
        fail "ROI_H_INSTALLER_VERSION is not a valid exact version."
        ;;
esac
case "$release_bundle_url" in
    https://*)
        ;;
    *)
        fail "ROI_H_RELEASE_BUNDLE_URL must use HTTPS."
        ;;
esac
[ "${#release_bundle_sha256}" -eq 64 ] ||
    fail "ROI_H_RELEASE_BUNDLE_SHA256 must contain 64 hexadecimal characters."
case "$release_bundle_sha256" in
    *[!0-9A-Fa-f]*)
        fail "ROI_H_RELEASE_BUNDLE_SHA256 must contain 64 hexadecimal characters."
        ;;
esac
release_bundle_sha256=$(printf '%s' "$release_bundle_sha256" | tr 'A-F' 'a-f')

if [ -n "${ROI_H_INSTALL_ROOT:-}" ]; then
    install_root=$ROI_H_INSTALL_ROOT
elif [ -n "${XDG_DATA_HOME:-}" ]; then
    install_root=$XDG_DATA_HOME/roi-h
else
    install_root=$HOME/.local/share/roi-h
fi
data_home=${ROI_H_HOME:-$HOME/.roi-h}
if [ -n "${XDG_BIN_HOME:-}" ]; then
    bin_root=$XDG_BIN_HOME
else
    bin_root=$HOME/.local/bin
fi

temporary_parent=${TMPDIR:-/tmp}
mkdir -p "$temporary_parent"
temporary_root=$(mktemp -d "$temporary_parent/roi-h-install.XXXXXX") ||
    fail "Cannot create a temporary directory."

cleanup() {
    rm -rf -- "$temporary_root"
}
trap cleanup 0 1 2 15

uv_installer=$temporary_root/uv-installer.sh
release_bundle=$temporary_root/release-bundle.tar.gz
release_root=$temporary_root/release
release_description=$release_root/release.json
uv_root=$install_root/bootstrap
installer_root=$install_root/installer/versions/$installer_version
installer_bin_root=$installer_root/bin

curl \
    --proto '=https' \
    --tlsv1.2 \
    --fail \
    --location \
    --silent \
    --show-error \
    --output "$uv_installer" \
    "https://astral.sh/uv/$UV_VERSION/install.sh"

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1"
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1"
    else
        fail "A SHA-256 tool is required."
    fi
}

uv_installer_actual_sha256=$(sha256_of "$uv_installer")
uv_installer_actual_sha256=${uv_installer_actual_sha256%% *}
[ "$uv_installer_actual_sha256" = "$UV_INSTALLER_SHA256" ] ||
    fail "The uv installer checksum is invalid."

curl \
    --proto '=https' \
    --tlsv1.2 \
    --fail \
    --location \
    --silent \
    --show-error \
    --output "$release_bundle" \
    "$release_bundle_url"
release_bundle_actual_sha256=$(sha256_of "$release_bundle")
release_bundle_actual_sha256=${release_bundle_actual_sha256%% *}
[ "$release_bundle_actual_sha256" = "$release_bundle_sha256" ] ||
    fail "The release bundle checksum is invalid."

members_file=$temporary_root/release-members.txt
member_types_file=$temporary_root/release-member-types.txt
tar -tzf "$release_bundle" >"$members_file" ||
    fail "The release bundle cannot be read."
tar -tvzf "$release_bundle" >"$member_types_file" ||
    fail "The release bundle cannot be inspected."

while IFS=' ' read -r member_mode _member_details; do
    case "$member_mode" in
        -*)
            ;;
        *)
            fail "The release bundle contains a non-regular entry."
            ;;
    esac
done <"$member_types_file"

duplicate_members=$(sort "$members_file" | uniq -d)
[ -z "$duplicate_members" ] || fail "The release bundle contains duplicate entries."

release_description_count=0
wheel_count=0
while IFS= read -r member_name; do
    case "$member_name" in
        release.json)
            release_description_count=$((release_description_count + 1))
            ;;
        [A-Za-z0-9][A-Za-z0-9_.-]*.whl)
            wheel_count=$((wheel_count + 1))
            ;;
        *)
            fail "The release bundle contains an unsafe entry."
            ;;
    esac
done <"$members_file"
[ "$release_description_count" -eq 1 ] ||
    fail "The release bundle must contain one release.json file."
[ "$wheel_count" -ge 1 ] || fail "The release bundle must contain at least one wheel."

mkdir -p "$release_root"
while IFS= read -r member_name; do
    tar -xOzf "$release_bundle" "$member_name" >"$release_root/$member_name" ||
        fail "The release bundle entry cannot be extracted."
done <"$members_file"

mkdir -p "$uv_root"
UV_UNMANAGED_INSTALL="$uv_root" UV_NO_MODIFY_PATH=1 sh "$uv_installer"
uv_binary=$uv_root/uv
[ -x "$uv_binary" ] || fail "The pinned uv installer did not create uv."

mkdir -p "$installer_bin_root"
UV_TOOL_DIR="$installer_root/tool" \
UV_TOOL_BIN_DIR="$installer_bin_root" \
UV_PYTHON_INSTALL_DIR="$install_root/python/versions" \
UV_CACHE_DIR="$install_root/cache/uv" \
    "$uv_binary" --no-config tool install \
        --python "$PYTHON_VERSION" \
        --python-preference only-managed \
        --no-index \
        --find-links "$release_root" \
        --force \
        "roi-h-installer==$installer_version"

installer_binary=$installer_bin_root/roi-h-installer
[ -x "$installer_binary" ] || fail "The exact ROI-H installer was not installed."

installer_operation=install
if [ -f "$install_root/install-state.json" ]; then
    installer_operation=update
fi

"$installer_binary" "$installer_operation" \
    --release-description "$release_description" \
    --install-root "$install_root" \
    --data-home "$data_home" \
    --output json

updater_helper=$install_root/installer/update.sh
mkdir -p "$install_root/installer"
temporary_updater_helper=$install_root/installer/.update.sh.tmp
cat >"$temporary_updater_helper" <<'ROI_H_UPDATER'
#!/bin/sh
set -eu
temporary_parent=${TMPDIR:-/tmp}
temporary_script=$(mktemp "$temporary_parent/roi-h-update.XXXXXX")
cleanup_update() {
    rm -f -- "$temporary_script"
}
trap cleanup_update 0 1 2 15
curl \
    --proto '=https' \
    --tlsv1.2 \
    --fail \
    --location \
    --silent \
    --show-error \
    --output "$temporary_script" \
    https://raw.githubusercontent.com/omerlefaruk/roi-h/main/install.sh
/bin/sh "$temporary_script"
ROI_H_UPDATER
chmod 0755 "$temporary_updater_helper"
mv -f "$temporary_updater_helper" "$updater_helper"

active_cli=$install_root/current/bin/roi-h
[ -x "$active_cli" ] || fail "The active ROI-H command is not executable."
mkdir -p "$bin_root"
launcher=$bin_root/roi-h
if [ -e "$launcher" ] && [ ! -L "$launcher" ] && \
    ! grep -q '^# ROI-H managed launcher$' "$launcher"; then
    fail "The ROI-H launcher path contains a file that is not managed by ROI-H."
fi
root_pointer=$bin_root/.roi-h-install-root
printf '%s\n' "$install_root" > "$root_pointer.tmp"
mv -f "$root_pointer.tmp" "$root_pointer"
temporary_launcher=$bin_root/.roi-h-launcher.tmp
cat > "$temporary_launcher" <<'ROI_H_LAUNCHER'
#!/bin/sh
# ROI-H managed launcher
set -eu
launcher_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IFS= read -r install_root < "$launcher_root/.roi-h-install-root"
export ROI_H_INSTALL_ROOT="$install_root"
export PLAYWRIGHT_BROWSERS_PATH="$install_root/browsers"
export PLAYWRIGHT_SKIP_BROWSER_GC=1
exec "$install_root/current/bin/roi-h" "$@"
ROI_H_LAUNCHER
chmod 0755 "$temporary_launcher"
mv -f "$temporary_launcher" "$launcher"

case ":${PATH:-}:" in
    *":$bin_root:"*)
        ;;
    *)
        printf 'Add %s to PATH to run roi-h from any directory.\n' "$bin_root" >&2
        ;;
esac
