#!/bin/sh
# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

python3 scripts/release_check.py
python3 scripts/check_docs.py
python3 scripts/check_secrets.py
python3 -m compileall -q scripts tests
sh -n install.sh tag scripts/ci_check.sh
if command -v zsh >/dev/null 2>&1; then
    zsh -n "OpenTag Control.command" "OpenTag Setup.command"
fi

PY_YAML_SPEC=$(awk '/^PyYAML==/ { print; exit }' requirements-ci.txt)
SLACK_BOLT_SPEC=$(awk '/^slack-bolt==/ { print; exit }' requirements-runtime.txt)
[ -n "$PY_YAML_SPEC" ] && [ -n "$SLACK_BOLT_SPEC" ]

uv run --with "$PY_YAML_SPEC" python3 scripts/check_manifest.py
uv run --with "$SLACK_BOLT_SPEC" --with "$PY_YAML_SPEC" \
    python3 -m unittest discover -s tests -v

git diff --check
printf 'Tag CI gate passed.\n'
