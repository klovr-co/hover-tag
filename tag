#!/bin/sh
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
set -eu
tag_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$tag_source/.venv/bin/python" ]; then
    if [ "${1-}" = app ]; then
        # Tag.app from this checkout, driving this checkout's CLI. Set TAG_HOME to keep real Tags out.
        shift
        # One Tag.app runs at a time; a second copy only brings the first forward.
        if pgrep -f '/Tag\.app/Contents/MacOS/' >/dev/null 2>&1; then
            printf '%s\n' 'Error: Tag.app is already open. Quit it from the menu bar, then run ./tag app again.' >&2
            exit 1
        fi
        cd "$tag_source/desktop"
        if [ ! -d node_modules ] || [ package-lock.json -nt node_modules ]; then
            npm install
        fi
        TAG_CLI="$tag_source/tag" exec npm run tauri -- dev "$@"
    fi
    exec "$tag_source/.venv/bin/python" "$tag_source/scripts/tag_cli.py" "$@"
fi
printf '%s\n' \
    'Error: this Tag source checkout is not prepared.' \
    '' \
    'For development, run:' \
    '  ./install.sh --dependencies-only' \
    '' \
    'For a normal managed installation, run:' \
    '  ./install.sh' >&2
exit 2
