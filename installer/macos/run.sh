#!/bin/sh
# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
# One command for the Tag Mac app prototype: build it, then open it.
#
#   installer/macos/run.sh             build and open Tag, driving this branch's `tag` code
#   installer/macos/run.sh --installed build and open Tag, driving your installed `tag`
#   installer/macos/run.sh --demo      build and open with sample data; nothing real changes
#   installer/macos/run.sh --check     run every check (repo tests + app tests), then build
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH='' cd -- "$here/../.." && pwd)
app="$here/build/Tag.app"

mode=${1:-}
case "$mode" in
    ''|--installed|--demo|--check) ;;
    *) printf 'Usage: %s [--installed|--demo|--check]\n' "$0" >&2; exit 2 ;;
esac

if [ "$mode" = --check ]; then
    printf '› Repo checks\n'
    sh "$root/scripts/ci_check.sh"
    printf '› App tests\n'
    sh "$here/tests/run.sh"
fi

printf '› Building Tag.app\n'
sh "$here/build.sh"

# Quit a copy that's already running (it lives on in the menu bar) so the new build opens.
pkill -f "$app/Contents/MacOS/Tag" 2>/dev/null || true

if [ "$mode" = --check ]; then
    printf 'All checks passed. Open the app with: %s\n' "$0"
elif [ "$mode" = --demo ]; then
    printf '› Opening Tag with sample data\n'
    TAG_INSTALLER_DEMO=1 open -n "$app" --env TAG_INSTALLER_DEMO=1
elif [ "$mode" = --installed ]; then
    printf '› Opening Tag with your installed tag command\n'
    open "$app"
else
    # Run this branch's CLI with the Python environment of the installed Tag,
    # which already has every dependency. Your real Tags and settings are used.
    python=$(ls -d "$HOME/Library/Application Support/Tag/releases/"*/.venv/bin/python 2>/dev/null | tail -n 1)
    [ -n "$python" ] || python="$root/.venv/bin/python"
    if [ ! -x "$python" ]; then
        printf 'Install Tag first (./install.sh), or run ./install.sh --dependencies-only.\n' >&2
        exit 1
    fi
    cli="$here/build/tag-dev"
    printf '#!/bin/sh\nexec "%s" "%s/scripts/tag_cli.py" "$@"\n' "$python" "$root" > "$cli"
    chmod +x "$cli"
    printf '› Opening Tag with this branch'"'"'s tag command (%s)\n' "$cli"
    printf '  Note: this uses your real Tags. Starting or setting up a Tag still named\n'
    printf '  "default" renames it after its Slack IDs, as the upgrade will.\n'
    open -n "$app" --env TAG_CLI="$cli"
fi
