#!/bin/sh
# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

mode=${1:-full}
case "$mode" in
    full|--policy-only|--tests-only) ;;
    *) printf 'Usage: %s [--policy-only|--tests-only]\n' "$0" >&2; exit 2 ;;
esac

PY_YAML_SPEC=$(awk '/^PyYAML==/ { print; exit }' requirements-ci.txt)
SLACK_BOLT_SPEC=$(awk '/^slack-bolt==/ { print; exit }' requirements-runtime.txt)
[ -n "$PY_YAML_SPEC" ] && [ -n "$SLACK_BOLT_SPEC" ]

if [ "$mode" != --tests-only ]; then
    python3 scripts/release_check.py
    python3 scripts/check_docs.py
    python3 scripts/check_secrets.py
    python3 -m compileall -q scripts tests
    sh -n install.sh tag scripts/ci_check.sh
    uv run --with "$PY_YAML_SPEC" python scripts/check_manifest.py
    git diff --check
fi

if [ "$mode" != --policy-only ]; then
    uv run --with "$SLACK_BOLT_SPEC" --with "$PY_YAML_SPEC" --with psutil==7.0.0 --with tomli==2.2.1 \
        python -m unittest discover -s tests -v
fi

case "$mode" in
    full) printf 'Tag CI gate passed.\n' ;;
    --policy-only) printf 'Tag policy checks passed.\n' ;;
    --tests-only) printf 'Tag test suite passed.\n' ;;
esac
