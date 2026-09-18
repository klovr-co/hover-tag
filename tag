#!/bin/sh
# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0
set -eu
tag_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$tag_source/.venv/bin/python" ]; then
    exec "$tag_source/.venv/bin/python" "$tag_source/scripts/tag_cli.py" "$@"
fi
exec python3 "$tag_source/scripts/tag_cli.py" "$@"
