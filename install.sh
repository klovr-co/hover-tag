#!/bin/sh
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
set -eu
command -v python3 >/dev/null 2>&1 || { printf 'Python 3.10+ is required.\n' >&2; exit 1; }
if [ -f "${0%/*}/scripts/tag_install.py" ]; then
    tag_source=$(CDPATH='' cd -- "${0%/*}" && pwd)
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
        exec python3 "$tag_source/scripts/tag_install.py" "$@"
    fi
    exec python3 "$tag_source/scripts/tag_install.py" --source "$tag_source" "$@"
fi
tag_download=$(mktemp -d "${TMPDIR:-/tmp}/tag-bootstrap.XXXXXX")
trap 'rm -f "$tag_download/tag_install.py" "$tag_download/release-channels.json"; rmdir "$tag_download"' EXIT HUP INT TERM
curl --proto '=https' --tlsv1.2 -fsSL \
    https://raw.githubusercontent.com/klovr-co/tag/main/scripts/tag_install.py \
    -o "$tag_download/tag_install.py"
curl --proto '=https' --tlsv1.2 -fsSL \
    https://raw.githubusercontent.com/klovr-co/tag/main/release-channels.json \
    -o "$tag_download/release-channels.json"
python3 "$tag_download/tag_install.py" "$@"
