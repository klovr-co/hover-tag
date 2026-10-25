# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""hover-tag:// links that open Tag.app."""
from __future__ import annotations

import urllib.parse


def tag_link(tag_id: str) -> str:
    return f"hover-tag://tag/{urllib.parse.quote(tag_id, safe='')}"


def thread_link(tag_id: str, channel: str, thread_ts: str) -> str:
    """Open one Slack thread of a Tag in Tag.app."""
    quote = lambda value: urllib.parse.quote(value, safe="")
    return f"{tag_link(tag_id)}/thread/{quote(channel)}/{quote(thread_ts)}"
