#!/bin/sh
# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0
set -eu
tag_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$tag_source/.venv/bin/python" ]; then
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
