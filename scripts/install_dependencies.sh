#!/bin/sh
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MFS_SERVER_SPEC=$(awk '/^mfs-server(\[[^]]+\])?==/ { print; exit }' "$ROOT/requirements-runtime.txt")
MFS_VERSION=${MFS_SERVER_SPEC##*==}
RUNTIME_PYTHON="$ROOT/.venv/bin/python"
RUNTIME_BIN="$ROOT/.venv/bin"
PATH="$RUNTIME_BIN:$PATH"
export PATH

say() {
    printf '%s\n' "$*"
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

[ -n "$MFS_SERVER_SPEC" ] || fail "requirements-runtime.txt does not pin mfs-server."

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 is required. $2"
}

mfs_server_has_version() {
    "$RUNTIME_PYTHON" -c 'import importlib.metadata, sys; sys.exit(importlib.metadata.version("mfs-server") != sys.argv[1])' "$MFS_VERSION" 2>/dev/null
}

check_install() {
    check_mode=${1:-full}
    failed=0
    for command in mfs-server; do
        if command -v "$command" >/dev/null 2>&1; then
            say "✓ $command"
        else
            say "✗ $command"
            failed=1
        fi
    done
    if command -v mfs-server >/dev/null 2>&1 && ! mfs_server_has_version; then
        say "✗ mfs-server must be v$MFS_VERSION"
        failed=1
    fi
    if [ -x "$RUNTIME_PYTHON" ] && "$RUNTIME_PYTHON" -c 'import mfs_server, psutil, slack_bolt' >/dev/null 2>&1; then
        say "✓ Tag runtime"
    else
        say "✗ Tag runtime (run ./install.sh --dependencies-only)"
        failed=1
    fi
    if [ "$check_mode" = full ]; then
        if command -v codex >/dev/null 2>&1 || command -v claude >/dev/null 2>&1; then
            say "✓ agent backend"
        else
            say "✗ agent backend (install Codex or Claude Code)"
            failed=1
        fi
    fi
    [ -x "$ROOT/tag" ] || { say "✗ ./tag is not executable"; failed=1; }
    [ -f "$ROOT/slack-app-manifest.yaml" ] || { say "✗ Slack app manifest missing"; failed=1; }
    [ "$failed" -eq 0 ] || exit 1
    say "Tag prerequisites are installed."
}

if [ "${1:-}" = "--check" ]; then
    check_install full
    exit 0
fi
if [ "${1:-}" = "--check-dependencies" ]; then
    check_install dependencies
    exit 0
fi

if [ -z "${TAG_BOOTSTRAP_PYTHON:-}" ] || [ -z "${TAG_BOOTSTRAP_UV:-}" ]; then
    # Direct invocation follows the same Python-free bootstrap as install.sh.
    runtime_info=$(sh "$ROOT/install.sh" --runtime-info)
    TAG_BOOTSTRAP_PYTHON=$(printf '%s\n' "$runtime_info" | sed -n '1p')
    TAG_BOOTSTRAP_UV=$(printf '%s\n' "$runtime_info" | sed -n '2p')
fi
require_command curl "Install curl and run this command again."
# Prepare in an immutable directory. The source launcher only sees it after
# packages, model, and imports pass, just like a managed release activation.
mkdir -p "$ROOT/.runtime"
prepared_runtime=$(mktemp -d "$ROOT/.runtime/python.XXXXXX")
RUNTIME_PYTHON="$prepared_runtime/bin/python"
RUNTIME_BIN="$prepared_runtime/bin"
PATH="$RUNTIME_BIN:$PATH"
export PATH
say "Creating Tag runtime..."
"$TAG_BOOTSTRAP_UV" venv --python "$TAG_BOOTSTRAP_PYTHON" "$prepared_runtime"
say "Installing pinned Tag runtime dependencies..."
"$TAG_BOOTSTRAP_UV" pip install --python "$RUNTIME_PYTHON" -r "$ROOT/requirements-runtime.txt"

say "Preparing the local MFS embedding model..."
"$RUNTIME_PYTHON" "$ROOT/scripts/preload_mfs_model.py"

check_install dependencies
"$RUNTIME_PYTHON" - "$ROOT" "$prepared_runtime" <<'PYTHON'
import os, sys, uuid
from pathlib import Path
root, prepared = map(Path, sys.argv[1:])
active = root / ".venv"
pending = root / ".runtime" / ("activate-" + uuid.uuid4().hex)
pending.symlink_to(prepared)
legacy = None
try:
    if active.is_dir() and not active.is_symlink():
        legacy = root / ".runtime" / ("legacy-" + uuid.uuid4().hex)
        os.replace(active, legacy)
    os.replace(pending, active)
except BaseException:
    if legacy is not None and not active.exists():
        os.replace(legacy, active)
    raise
finally:
    pending.unlink(missing_ok=True)
PYTHON

if [ "${1:-}" = "--dependencies-only" ]; then
    check_install dependencies
    say "Pinned Tag dependencies are installed."
    exit 0
fi

if [ -f "$ROOT/.env" ]; then
    say "Keeping existing private configuration at $ROOT/.env"
else
    "$RUNTIME_PYTHON" "$ROOT/scripts/opentag_setup.py"
fi

say ""
say "Installation complete. Next run:"
say "  ./tag start"
say ""
say "Tag will start MFS, run all preflight checks, and then start the configured bot."
