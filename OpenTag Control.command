#!/bin/zsh
# Modified by klovr.co in 2026 for Tag. See NOTICE and repository history.
set -eu
tag_source="$(cd "$(dirname "$0")" && pwd)"
tag_launcher=$(PYTHONPATH="$tag_source/scripts" python3 -c 'from tag_paths import tag_home; print(tag_home() / "bin/tag-launch.py")')
if [[ ! -f "$tag_launcher" ]]; then
  print 'Install TAG with ./install.sh first.'
  exit 1
fi
PS3='Choose a TAG command: '
select action in status start stop logs doctor quit; do
  [[ "$action" == quit ]] && break
  [[ -n "$action" ]] && python3 "$tag_launcher" "$action"
done
