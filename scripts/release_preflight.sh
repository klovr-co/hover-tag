#!/bin/sh
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

./scripts/ci_check.sh

version=$(sed -n '1p' VERSION)

if ! git diff --quiet || ! git diff --cached --quiet; then
    printf 'Tracked changes must be committed before release.\n' >&2
    exit 1
fi

evidence="docs/release-evidence/v$version.md"
[ -f "$evidence" ] || {
    printf 'Missing release evidence: %s\n' "$evidence" >&2
    exit 1
}
# shellcheck disable=SC2016 # Backticks are literal Markdown delimiters in this regex.
candidate_commit=$(sed -n 's/^- Candidate merge commit: `\([0-9a-f]\{40\}\)`$/\1/p' "$evidence")
[ -n "$candidate_commit" ] && [ "$(printf '%s\n' "$candidate_commit" | wc -l | tr -d ' ')" -eq 1 ] || {
    printf 'Release evidence must identify one full candidate commit in %s.\n' "$evidence" >&2
    exit 1
}
git merge-base --is-ancestor "$candidate_commit" HEAD || {
    printf 'Evidence candidate %s is not an ancestor of the release commit.\n' "$candidate_commit" >&2
    exit 1
}
git diff --quiet "$candidate_commit" HEAD -- . ":(exclude)$evidence" || {
    printf 'Only %s may change after live candidate validation.\n' "$evidence" >&2
    exit 1
}
for required_gate in 'Local release gate' 'GitHub CI' 'Clean installation'; do
    grep -F "$required_gate: **PASS**" "$evidence" >/dev/null || {
        printf '%s is not PASS in %s.\n' "$required_gate" "$evidence" >&2
        exit 1
    }
done
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
    grep -F 'Release publication approval: **PASS**' "$evidence" >/dev/null || {
        printf 'Release publication approval is not PASS in %s.\n' "$evidence" >&2
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
