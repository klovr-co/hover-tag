#!/usr/bin/env python3
"""Let a test Tag answer messages posted through the poster app's user token.

Usage: allow_test_poster.py <tag-id>

Slack marks such a message with the app's bot id, which Tag ignores unless the
app is listed in OPENTAG_TEST_APP_IDS. Take the app's ID from
~/.config/tag-sandbox/slack-poster-app-id (one line, kept outside the repo).
Restart the Tag afterwards.
"""
import json
import os
import sys
from pathlib import Path

KEY = "OPENTAG_TEST_APP_IDS"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    app_file = Path.home() / ".config/tag-sandbox/slack-poster-app-id"
    try:
        app_id = app_file.read_text(encoding="utf-8").strip()
    except OSError:
        print(f"Missing {app_file}", file=sys.stderr)
        return 1
    settings = Path.home() / "Tag" / sys.argv[1] / ".tag/config/settings.json"
    values = json.loads(settings.read_text(encoding="utf-8"))
    if values.get(KEY) == app_id:
        print(f"{sys.argv[1]} already allows the poster app.")
        return 0
    values[KEY] = app_id
    temporary = settings.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(settings.stat().st_mode & 0o777)
    os.replace(temporary, settings)
    print(f"{sys.argv[1]} now allows the poster app. Restart the Tag.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
