"""A read-only look at a watched message's attachments, prepared by the bridge.

Watched runs have no tools, so the bridge does the reading. The classifier gets a short text
description of each attachment, which is free and has no URLs. A draft run, after the classifier
said yes, also gets up to a few images and a video's one preview frame passed natively, plus its
Slack captions as text. Everything is size-capped, and nothing is kept after the run.
"""
from __future__ import annotations

import re
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_IMAGES = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CAPTION_BYTES = 256 * 1024
MAX_CAPTION_CHARS = 2_000
MAX_TEXT_CHARS = 300
MAX_ATTACHMENTS = 10
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
VTT_TIMING_RE = re.compile(r"^\s*(?:\d{2}:)?\d{2}:\d{2}\.\d{3}\s+-->")
VTT_TAG_RE = re.compile(r"<[^>]+>")

Downloader = Callable[[str, str, int, str], bytes]


def _short(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _kind(mimetype: str) -> str:
    if mimetype.startswith("image/"):
        return "image"
    if mimetype.startswith("video/"):
        return "video"
    if mimetype.startswith("audio/"):
        return "audio"
    return "file"


def files(event: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in event.get("files") or [] if isinstance(f, dict)][:MAX_ATTACHMENTS]


def describe(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Text-only descriptions of uploads and link previews; never URLs or file contents."""
    items: list[dict[str, Any]] = []
    for file in files(event):
        mimetype = str(file.get("mimetype") or "")
        item: dict[str, Any] = {"kind": _kind(mimetype), "name": _short(file.get("title") or file.get("name"), 120)}
        if mimetype:
            item["type"] = mimetype
        duration = file.get("duration_ms")
        if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
            item["seconds"] = round(duration / 1000)
        items.append(item)
    for preview in event.get("attachments") or []:
        if not isinstance(preview, dict) or not (preview.get("title") or preview.get("text")):
            continue
        if preview.get("files"):
            continue  # A forwarded message, not a link preview.
        item = {"kind": "link preview", "site": _short(preview.get("service_name"), 60),
                "title": _short(preview.get("title"), 200), "description": _short(preview.get("text"))}
        items.append({key: value for key, value in item.items() if value})
        if len(items) >= MAX_ATTACHMENTS:
            break
    return items


def captions_text(vtt: str) -> str:
    """Plain caption text from WebVTT: no header, cue numbers, timings or tags; repeats collapsed."""
    lines: list[str] = []
    for line in vtt.splitlines():
        line = line.strip()
        if (not line or line.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")) or line.isdigit()
                or VTT_TIMING_RE.match(line)):
            continue
        line = VTT_TAG_RE.sub("", line).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    text = " ".join(lines)
    return text if len(text) <= MAX_CAPTION_CHARS else text[:MAX_CAPTION_CHARS - 1] + "…"


def prepare(event: dict[str, Any], directory: Path, token: str, download: Downloader,
            ) -> tuple[list[dict[str, Any]], tuple[Path, ...]]:
    """Descriptions with captions added, and the images to pass to a draft run.

    ``download(url, token, max_bytes, expected_prefix)`` returns the bytes or raises. Each failure
    only leaves that attachment's description without its contents.
    """
    descriptions = describe(event)
    images: list[Path] = []
    for index, file in enumerate(files(event)):
        mimetype = str(file.get("mimetype") or "")
        description = descriptions[index]
        sources: list[tuple[str, str]] = []
        if mimetype in IMAGE_TYPES:
            url = file.get("thumb_1024") or file.get("thumb_720") or file.get("url_private_download") \
                or file.get("url_private")
            sources.append((str(url or ""), "image"))
        elif mimetype.startswith("video/"):
            sources.append((str(file.get("thumb_video") or ""), "frame"))
        for url, what in sources:
            if not url or len(images) >= MAX_IMAGES:
                continue
            try:
                data = download(url, token, MAX_IMAGE_BYTES, "image/")
            except (OSError, urllib.error.URLError, ValueError):
                description["unavailable"] = True
                continue
            kind = _image_type(data)
            if kind is None:
                description["unavailable"] = True
                continue
            path = directory / f"attachment-{index + 1}-{what}{EXTENSIONS[kind]}"
            path.write_bytes(data)
            path.chmod(0o600)
            images.append(path)
            description["shown"] = "preview frame" if what == "frame" else "image"
        vtt = file.get("vtt")
        text = ""
        # Slack documents `vtt` on video files; whether it holds the captions or a link to them is unverified.
        if mimetype.startswith("video/") and isinstance(vtt, str) and vtt.lstrip().startswith("WEBVTT"):
            text = captions_text(vtt[:MAX_CAPTION_BYTES])
        elif mimetype.startswith("video/") and isinstance(vtt, str) and vtt.startswith("https://"):
            try:
                text = captions_text(download(vtt, token, MAX_CAPTION_BYTES, "").decode("utf-8", "replace"))
            except (OSError, urllib.error.URLError, ValueError):
                text = ""
        if text:
            description["captions"] = text
    return descriptions, tuple(images)


SLACK_FILE_HOSTS = ("https://files.slack.com/", "https://files-edu.slack.com/", "https://files.slack-gov.com/")


def slack_download(url: str, token: str, max_bytes: int, expected_prefix: str, *,
                   opener: Callable[..., Any] | None = None) -> bytes:
    """Download a private Slack file with the bot token, only from Slack's file hosts, within a cap."""
    import urllib.request
    if not url.startswith(SLACK_FILE_HOSTS):
        raise ValueError("not a Slack file URL")  # The bot token never goes anywhere else.
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with (opener or urllib.request.urlopen)(request, timeout=15) as response:
        content_type = response.headers.get_content_type()
        if expected_prefix and content_type and not content_type.startswith(expected_prefix):
            raise ValueError(f"Slack returned {content_type}")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("attachment is over the size cap")
    return data


def _image_type(data: bytes) -> str | None:
    """The image type from its first bytes; anything else is refused."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
