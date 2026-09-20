"""Private, one-shot Slack CLI deploy hook; never starts or deploys a service."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> int:
    try:
        path = Path(os.environ["TAG_SLACK_HANDOFF_FILE"])
        credentials = {key: os.environ.get(key, "") for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN")}
        for key, prefix in (("SLACK_APP_TOKEN", "xapp-"), ("SLACK_BOT_TOKEN", "xoxb-")):
            value = credentials[key]
            if not value.startswith(prefix) or len(value) <= len(prefix) or any(c.isspace() for c in value):
                return 1
        # The caller owns the fresh private directory. Never replace an existing file.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(credentials, handle)
            handle.flush()
            os.fsync(handle.fileno())
        return 0
    except (OSError, KeyError, ValueError):
        # Never echo environment values, credentials, or exception details.
        return 1


if __name__ == "__main__":
    sys.exit(main())
