#!/bin/zsh
# Guided first-run setup for a local Open Tag installation.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
tag_launcher=$(PYTHONPATH="$SCRIPT_DIR/scripts" python3 -c 'from tag_paths import tag_home; print(tag_home() / "bin/tag-launch.py")')
if [[ ! -f "$tag_launcher" ]]; then
  print 'Install TAG with ./install.sh first.'
  exit 1
fi
exec python3 "$tag_launcher" setup
