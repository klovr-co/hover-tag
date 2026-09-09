#!/bin/sh
# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MFS_SERVER_SPEC=$(awk '/^mfs-server==/ { print; exit }' "$ROOT/requirements-runtime.txt")
MFS_VERSION=${MFS_SERVER_SPEC##*==}
MFS_RELEASE=https://github.com/zilliztech/mfs/releases/download/v${MFS_VERSION}

say() {
    printf '%s\n' "$*"
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

[ -n "$MFS_SERVER_SPEC" ] || fail "requirements-runtime.txt does not pin mfs-server."

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 is required. $2"
}

command_has_version() {
    version_output=$("$1" --version 2>/dev/null || true)
    case "$version_output" in
        *"$2"*) return 0 ;;
        *) return 1 ;;
    esac
}

mfs_server_has_version() {
    command -v uv >/dev/null 2>&1 || return 1
    uv tool list 2>/dev/null | grep -F "mfs-server v$MFS_VERSION" >/dev/null 2>&1
}

sha256_file() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        fail "shasum or sha256sum is required to verify downloads."
    fi
}

install_mfs_cli() {
    os=$(uname -s)
    arch=$(uname -m)
    case "$os/$arch" in
        Darwin/arm64)
            artifact=mfs-cli-aarch64-apple-darwin.tar.xz
            expected=1fd7c9fe38d5f27e72cde3fca8e895c2185eb6113d17d352bc33c1e661e18cfe
            ;;
        Darwin/x86_64)
            artifact=mfs-cli-x86_64-apple-darwin.tar.xz
            expected=807eeba5c7d35b02123a25bfc244ad8dae3b478d79757d30373d282f234bbd25
            ;;
        Linux/aarch64|Linux/arm64)
            artifact=mfs-cli-aarch64-unknown-linux-musl.tar.xz
            expected=a6a4cc90dc73118ae6f6b2c0fd779a43057ae1fd88d27e6cc32a3352ac3cc978
            ;;
        Linux/x86_64|Linux/amd64)
            artifact=mfs-cli-x86_64-unknown-linux-musl.tar.xz
            expected=2b4721bce6ebcea84d19a33d517d4963932d0696a106555753f145de6e767ae4
            ;;
        *)
            fail "No prebuilt MFS CLI is available for $os/$arch. See https://github.com/zilliztech/mfs#install-the-cli"
            ;;
    esac

    download_dir=$(mktemp -d "${TMPDIR:-/tmp}/tag-mfs.XXXXXX")
    trap 'rm -rf "$download_dir"' EXIT HUP INT TERM
    archive="$download_dir/$artifact"
    say "Downloading MFS CLI v$MFS_VERSION..."
    curl --proto '=https' --tlsv1.2 -fsSL "$MFS_RELEASE/$artifact" -o "$archive"
    actual=$(sha256_file "$archive")
    [ "$actual" = "$expected" ] || fail "MFS CLI checksum verification failed."
    tar -xJf "$archive" -C "$download_dir"
    install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
    mkdir -p "$install_dir"
    mfs_binary=$(find "$download_dir" -type f -name mfs -perm -u+x | head -n 1)
    [ -n "$mfs_binary" ] || fail "The MFS CLI archive did not contain an executable."
    cp "$mfs_binary" "$install_dir/mfs"
    chmod 0755 "$install_dir/mfs"
    PATH="$install_dir:$PATH"
    export PATH
    say "Installed MFS CLI to $install_dir/mfs"
}

check_install() {
    check_mode=${1:-full}
    failed=0
    for command in python3 uv mfs-server mfs; do
        if command -v "$command" >/dev/null 2>&1; then
            say "✓ $command"
        else
            say "✗ $command"
            failed=1
        fi
    done
    if command -v mfs-server >/dev/null 2>&1 && ! mfs_server_has_version; then
        say "✗ mfs-server must be v$MFS_VERSION"
        failed=1
    fi
    if command -v mfs >/dev/null 2>&1 && ! command_has_version mfs "$MFS_VERSION"; then
        say "✗ mfs must be v$MFS_VERSION"
        failed=1
    fi
    if [ "$check_mode" = full ]; then
        if command -v codex >/dev/null 2>&1 || command -v claude >/dev/null 2>&1; then
            say "✓ agent backend"
        else
            say "✗ agent backend (install Codex or Claude Code)"
            failed=1
        fi
    fi
    [ -x "$ROOT/tag" ] || { say "✗ ./tag is not executable"; failed=1; }
    [ -f "$ROOT/slack-app-manifest.yaml" ] || { say "✗ Slack app manifest missing"; failed=1; }
    [ "$failed" -eq 0 ] || exit 1
    say "Tag prerequisites are installed."
}

if [ "${1:-}" = "--check" ]; then
    check_install full
    exit 0
fi
if [ "${1:-}" = "--check-dependencies" ]; then
    check_install dependencies
    exit 0
fi

require_command python3 "Install Python 3.10 or newer."
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || fail "Python 3.10 or newer is required."
require_command curl "Install curl and run this command again."
require_command uv "Install uv from https://docs.astral.sh/uv/getting-started/installation/"

uv_bin_dir=$(uv tool dir --bin 2>/dev/null || true)
if [ -n "$uv_bin_dir" ]; then
    PATH="$uv_bin_dir:$PATH"
    export PATH
fi

if ! command -v mfs-server >/dev/null 2>&1 || ! mfs_server_has_version; then
    say "Installing MFS server v$MFS_VERSION..."
    uv tool install --force "$MFS_SERVER_SPEC"
fi
if ! command -v mfs >/dev/null 2>&1 || ! command_has_version mfs "$MFS_VERSION"; then
    install_mfs_cli
fi

if [ "${1:-}" = "--dependencies-only" ]; then
    check_install dependencies
    say "Pinned Tag dependencies are installed."
    exit 0
fi

if [ -f "$ROOT/.env" ]; then
    say "Keeping existing private configuration at $ROOT/.env"
else
    python3 "$ROOT/scripts/opentag_setup.py"
fi

say ""
say "Installation complete. Next run:"
say "  ./tag start"
say ""
say "Tag will start MFS, run all preflight checks, and then start the configured bot."
