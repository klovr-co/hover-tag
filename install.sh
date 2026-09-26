#!/bin/sh
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
set -eu
# This bootstrap deliberately needs only POSIX tools, never a system Python.
tag_prepare_runtime() {
    case "${TAG_HOME:-}" in
        /*) tag_home=$TAG_HOME ;;
        '') case "$(uname -s)" in
            Darwin) tag_home="$HOME/Library/Application Support/Tag" ;;
            Linux) case "${XDG_DATA_HOME:-}" in
                /*) tag_home="$XDG_DATA_HOME/tag" ;;
                *) tag_home="$HOME/.local/share/tag" ;;
            esac ;;
            *) printf 'Automatic runtime setup supports macOS and Linux.\n' >&2; exit 1 ;;
        esac ;;
        *) printf 'TAG_HOME must be an absolute path.\n' >&2; exit 1 ;;
    esac
    tag_uv_version=0.12.19
    tag_python_version=3.12.14
    tag_uv=$(command -v uv || true)
    if [ -z "$tag_uv" ] || [ "$("$tag_uv" --version 2>/dev/null | awk '{print $2}')" != "$tag_uv_version" ]; then
        tag_uv="$tag_home/runtime/uv/$tag_uv_version/uv"
    fi
    if [ ! -x "$tag_uv" ] || [ "$("$tag_uv" --version 2>/dev/null | awk '{print $2}')" != "$tag_uv_version" ]; then
        case "$(uname -s)/$(uname -m)" in
            Darwin/arm64) tag_target=aarch64-apple-darwin; tag_sha=a9a8df1eedeb192f2e47e40e2faabfb387db4b850209118786d42f89dde3e0ba ;;
            Darwin/x86_64) tag_target=x86_64-apple-darwin; tag_sha=cb5fa57bafe68fc0fb94b17f06bee0b0b9a7feb94ccbd110445afa0696e39273 ;;
            Linux/aarch64|Linux/arm64) tag_target=aarch64-unknown-linux-musl; tag_sha=ad8d8448a2ff642ba62c2f684d7dd22a03f8eb3fc9918c2c3e8ec975f4ed6710 ;;
            Linux/x86_64|Linux/amd64) tag_target=x86_64-unknown-linux-musl; tag_sha=db7278c9f57981338fddff1fb250e11964bc0a4fafcb9eed8303fdb117dc067b ;;
            *) printf 'No managed runtime for this platform.\n' >&2; exit 1 ;;
        esac
        # Fixed hashes come from the corresponding upstream GitHub release assets.
        mkdir -p "$tag_home/runtime/uv/$tag_uv_version"
        tag_stage=$(mktemp -d "$tag_home/runtime/uv/$tag_uv_version/.download.XXXXXX")
        trap 'rm -rf "$tag_stage"' EXIT HUP INT TERM
        printf 'Downloading Tag installation tools…\n' >&2
        curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fL --progress-bar \
            "https://github.com/astral-sh/uv/releases/download/$tag_uv_version/uv-$tag_target.tar.gz" -o "$tag_stage/uv.tar.gz"
        if command -v shasum >/dev/null 2>&1; then
            tag_actual=$(shasum -a 256 "$tag_stage/uv.tar.gz" | awk '{print $1}')
        else
            tag_actual=$(sha256sum "$tag_stage/uv.tar.gz" | awk '{print $1}')
        fi
        [ "$tag_actual" = "$tag_sha" ] || { printf 'Installation tools checksum mismatch; retry installation.\n' >&2; exit 1; }
        tar -xzf "$tag_stage/uv.tar.gz" -C "$tag_stage" "uv-$tag_target/uv"
        "$tag_stage/uv-$tag_target/uv" --version >&2
        mv "$tag_stage/uv-$tag_target/uv" "$tag_uv"
        rm -rf "$tag_stage"
        trap - EXIT HUP INT TERM
    fi
    # Reuse an exact compatible uv-managed runtime, including an existing cache.
    tag_python=$(UV_PYTHON_INSTALL_DIR="$tag_home/runtime/python" "$tag_uv" python find --no-config --managed-python --no-python-downloads "$tag_python_version" 2>/dev/null || true)
    if [ -z "$tag_python" ]; then
        tag_python=$("$tag_uv" python find --no-config --managed-python --no-python-downloads "$tag_python_version" 2>/dev/null || true)
    fi
    if [ -z "$tag_python" ]; then
        printf 'Preparing Tag Python %s…\n' "$tag_python_version" >&2
        UV_PYTHON_INSTALL_DIR="$tag_home/runtime/python" UV_PYTHON_BIN_DIR="$tag_home/runtime/bin" \
            "$tag_uv" python install --no-config --no-bin "$tag_python_version" >&2
        tag_python=$(UV_PYTHON_INSTALL_DIR="$tag_home/runtime/python" \
            "$tag_uv" python find --managed-python --no-python-downloads "$tag_python_version")
    fi
    "$tag_python" -c 'import sys; assert sys.version_info[:3] == (3, 12, 14)' || exit 1
    TAG_BOOTSTRAP_PYTHON=$tag_python
    TAG_BOOTSTRAP_UV=$tag_uv
    export TAG_BOOTSTRAP_PYTHON TAG_BOOTSTRAP_UV
}

if [ "${1:-}" = --runtime-info ]; then
    tag_prepare_runtime
    printf '%s\n%s\n' "$tag_python" "$tag_uv"
    exit 0
fi
case "${1:-}" in
    --check|--check-dependencies) ;;
    *) tag_prepare_runtime ;;
esac

if [ -f "$(dirname -- "$0")/scripts/tag_install.py" ]; then
    tag_source=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
    case "${1:-}" in
        --dependencies-only|--check-dependencies|--check)
            exec sh "$tag_source/scripts/install_dependencies.sh" "$@" ;;
    esac
    tag_remote=false
    for tag_argument in "$@"; do
        case "$tag_argument" in
            --version|--version=*|--channel|--channel=*) tag_remote=true ;;
        esac
    done
    if [ "$tag_remote" = true ]; then
        exec "$TAG_BOOTSTRAP_PYTHON" "$tag_source/scripts/tag_install.py" "$@"
    fi
    exec "$TAG_BOOTSTRAP_PYTHON" "$tag_source/scripts/tag_install.py" --source "$tag_source" "$@"
fi
tag_download=$(mktemp -d "${TMPDIR:-/tmp}/tag-bootstrap.XXXXXX")
trap 'rm -f "$tag_download/tag_install.py" "$tag_download/release-channels.json"; rmdir "$tag_download"' EXIT HUP INT TERM
curl --proto '=https' --tlsv1.2 -fsSL \
    https://raw.githubusercontent.com/klovr-co/hover-tag/main/scripts/tag_install.py \
    -o "$tag_download/tag_install.py"
curl --proto '=https' --tlsv1.2 -fsSL \
    https://raw.githubusercontent.com/klovr-co/hover-tag/main/release-channels.json \
    -o "$tag_download/release-channels.json"
"$TAG_BOOTSTRAP_PYTHON" "$tag_download/tag_install.py" "$@"
