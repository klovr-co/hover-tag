#!/bin/sh
# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0
set -eu
command -v python3 >/dev/null 2>&1 || { printf 'Python 3.10+ is required.\n' >&2; exit 1; }
if [ -f "${0%/*}/scripts/tag_install.py" ]; then
    tag_source=$(CDPATH= cd -- "${0%/*}" && pwd)
    case "${1:-}" in
        --dependencies-only|--check-dependencies|--check)
            exec sh "$tag_source/scripts/install_dependencies.sh" "$@" ;;
        --version) exec python3 "$tag_source/scripts/tag_install.py" "$@" ;;
        *) exec python3 "$tag_source/scripts/tag_install.py" --source "$tag_source" "$@" ;;
    esac
fi
tag_download=$(mktemp -d "${TMPDIR:-/tmp}/tag-bootstrap.XXXXXX")
trap 'rm -f "$tag_download/tag_install.py"; rmdir "$tag_download"' EXIT HUP INT TERM
curl --proto '=https' --tlsv1.2 -fsSL \
    https://raw.githubusercontent.com/klovr-co/tag/main/scripts/tag_install.py \
    -o "$tag_download/tag_install.py"
python3 "$tag_download/tag_install.py" "$@"
