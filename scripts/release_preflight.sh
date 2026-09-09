#!/bin/sh
# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

./scripts/ci_check.sh

version=$(sed -n '1p' VERSION)
[ "$version" = "0.1.0-alpha" ] || {
    printf 'Unexpected release version: %s\n' "$version" >&2
    exit 1
}

git diff --quiet && git diff --cached --quiet || {
    printf 'Tracked changes must be committed before release.\n' >&2
    exit 1
}

evidence="docs/release-evidence/v$version.md"
grep -F 'Live Slack sandbox: **PASS**' "$evidence" >/dev/null || {
    printf 'Live Slack sandbox evidence is not PASS in %s.\n' "$evidence" >&2
    exit 1
}
remote=false
publish=false
for argument in "$@"; do
    case "$argument" in
        --remote) remote=true ;;
        --publish) publish=true ;;
        *) printf 'Unknown argument: %s\n' "$argument" >&2; exit 2 ;;
    esac
done

if [ "$publish" = true ]; then
    grep -F 'Public visibility approval: **PASS**' "$evidence" >/dev/null || {
        printf 'Public visibility approval is not PASS in %s.\n' "$evidence" >&2
        exit 1
    }
fi

if [ "$remote" = true ]; then
    command -v gh >/dev/null 2>&1 || {
        printf 'gh is required for remote preflight.\n' >&2
        exit 1
    }
    gh pr checks
fi

if [ "$publish" = true ]; then
    printf 'Tag v%s publication preflight passed.\n' "$version"
else
    printf 'Tag v%s release-candidate preflight passed.\n' "$version"
fi
