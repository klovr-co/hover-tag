#!/usr/bin/env python3
"""Download an earlier Slack attachment shared in the channel that invoked Open Tag."""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API_URL = "https://slack.com/api/files.info"
SLACK_FILE_HOSTS = {"files.slack.com"}
MAX_FILE_BYTES = 15 * 1024 * 1024
FILE_ID_RE = re.compile(r"^F[A-Z0-9]{2,}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the bot token is never forwarded to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Slack returned an unexpected redirect.")


_OPENER = urllib.request.build_opener(_NoRedirect)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def slack_request(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})


def file_info(file_id: str, token: str) -> dict:
    query = urllib.parse.urlencode({"file": file_id})
    try:
        with _OPENER.open(slack_request(f"{API_URL}?{query}", token), timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Slack file lookup failed ({exc.code})") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Slack file lookup failed: {result.get('error', 'unknown error')}")
    return result.get("file") or {}


def shared_in_channel(file: dict, channel: str) -> bool:
    shares = file.get("shares") or {}
    return any(channel in (shares.get(kind) or {}) for kind in ("public", "private")) or channel in (
        file.get("channels") or []
    ) + (file.get("groups") or []) + (file.get("ims") or [])


def safe_name(file: dict, file_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", file.get("name") or "").strip(".-") or "slack-file"
    stem, separator, suffix = name.rpartition(".")
    extension = f".{suffix}" if separator and stem else ""
    stem = stem if extension else name
    return f"{stem[: 200 - len(extension)]}-{file_id}{extension}"


def download(file_id: str, attachments_dir: Path) -> dict:
    """Download one file shared in the current channel, within the per-file size limit."""
    if not FILE_ID_RE.match(file_id):
        raise ValueError(f"Not a Slack file ID: {file_id}")
    if not attachments_dir.is_dir():
        raise ValueError(f"Attachments directory does not exist: {attachments_dir}")
    token = require_env("SLACK_BOT_TOKEN")
    channel = require_env("OPENTAG_CURRENT_CHANNEL_ID")
    file = file_info(file_id, token)
    if not shared_in_channel(file, channel):
        raise PermissionError("That file was not shared in this channel.")
    url = file.get("url_private_download") or file.get("url_private") or ""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in SLACK_FILE_HOSTS:
        raise RuntimeError("Slack did not return a downloadable file URL.")
    chunks: list[bytes] = []
    total = 0
    with _OPENER.open(slack_request(url, token), timeout=30) as response:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise RuntimeError("This file exceeds Tag's 15 MB attachment limit.")
            chunks.append(chunk)
    target = attachments_dir / safe_name(file, file_id)
    target.write_bytes(b"".join(chunks))
    return {"id": file_id, "name": file.get("name"), "mimetype": file.get("mimetype"),
            "path": str(target), "bytes": total}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download earlier Slack thread attachments shared in Open Tag's current channel."
    )
    parser.add_argument("--file-id", action="append", required=True, help="Slack file ID, e.g. F0123ABCD")
    parser.add_argument("--attachments-dir", required=True, type=Path, help="This invocation's attachments directory")
    args = parser.parse_args()
    results = []
    for file_id in args.file_id:
        try:
            results.append({"ok": True, **download(file_id.strip(), args.attachments_dir)})
        except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
            results.append({"ok": False, "id": file_id, "error": str(exc)})
    print(json.dumps(results))
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
