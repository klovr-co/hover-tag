"""Decide whether a Slack message is a share worth digesting: mostly a link or files.

A free, deterministic check that runs before anything is fetched. It never stores
full URLs or access codes; callers get each link's host and type only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit


URL_RE = re.compile(r"<(https?://[^>|]+)(?:\|[^>]*)?>|(https?://[^\s<>]+)", re.I)
MENTION_RE = re.compile(r"<[@#!][^>]*>")
EMOJI_RE = re.compile(r":[\w+-]+:")
# Access codes for share links, such as Baidu's 提取码 (extraction code).
ACCESS_CODE_RE = re.compile(r"(?:提取码|提取碼|密码|密碼|access code|passcode|pwd|code)\s*[:：=]?\s*[A-Za-z0-9]{4}\b", re.I)
MAX_WORDS = 12  # A share is "mostly a link": at most this many words besides links.
MAX_WORDS_LIBRARY = 30  # Library links (folders, shares) may carry a short description.

# (host suffix, path pattern or None, link type). The first match wins.
LINK_TYPES: tuple[tuple[str, str | None, str], ...] = (
    ("giphy.com", None, "gif"), ("tenor.com", None, "gif"),
    ("slack.com", r"^/archives/", "slack_message"),
    ("meet.google.com", None, "meeting"), ("zoom.us", r"^/j/", "meeting"), ("teams.microsoft.com", None, "meeting"),
    ("calendar.google.com", None, "calendar"),
    ("drive.google.com", r"/folders/", "drive_folder"), ("drive.google.com", None, "drive_file"),
    ("docs.google.com", None, "google_doc"),
    ("pan.baidu.com", None, "baidu_share"),
    ("dropbox.com", None, "dropbox"), ("box.com", None, "box"),
    ("sharepoint.com", None, "sharepoint"), ("onedrive.live.com", None, "onedrive"), ("1drv.ms", None, "onedrive"),
    ("notion.so", None, "notion"), ("notion.site", None, "notion"),
    ("figma.com", None, "figma"),
    ("x.com", r"/status/", "x_post"), ("twitter.com", r"/status/", "x_post"),
    ("github.com", None, "github"),
    ("youtube.com", None, "video"), ("youtu.be", None, "video"), ("loom.com", None, "video"),
)
SKIP_TYPES = frozenset({"gif", "slack_message", "meeting", "calendar"})
LIBRARY_TYPES = frozenset({"drive_folder", "drive_file", "google_doc", "baidu_share", "dropbox", "box",
                           "sharepoint", "onedrive", "notion", "figma"})
LIBRARY_FILE_PREFIXES = ("application/", "video/", "text/")


@dataclass(frozen=True)
class Link:
    host: str
    type: str


@dataclass(frozen=True)
class Verdict:
    digest: bool
    reason: str
    links: tuple[Link, ...] = ()
    file_types: tuple[str, ...] = ()
    words: int = 0
    access_code: bool = False
    hosts: tuple[str, ...] = field(default=())


def link_type(url: str) -> Link:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    for suffix, path, kind in LINK_TYPES:
        if (host == suffix or host.endswith("." + suffix)) and (path is None or re.search(path, parts.path)):
            return Link(host, kind)
    return Link(host, "web_page")


def links_in(text: str) -> list[Link]:
    return [link_type(a or b) for a, b in URL_RE.findall(text or "")]


def _words(text: str) -> int:
    text = URL_RE.sub(" ", text or "")
    text = MENTION_RE.sub(" ", text)
    text = EMOJI_RE.sub(" ", text)
    text = ACCESS_CODE_RE.sub(" ", text)
    latin = re.findall(r"[A-Za-z0-9']+", text)
    # CJK has no spaces: count every two characters as a word.
    cjk = len(re.findall(r"[぀-ヿ㐀-鿿]", text))
    return len(latin) + (cjk + 1) // 2


def classify(text: str, files: list[dict[str, Any]] | None = None) -> Verdict:
    """Is this message a share to digest? Free; no network."""
    links = tuple(links_in(text))
    file_types = tuple((f.get("mimetype") or "unknown").lower() for f in files or [])
    words = _words(text)
    code = bool(ACCESS_CODE_RE.search(text or ""))
    content = [l for l in links if l.type not in SKIP_TYPES]
    common = dict(links=links, file_types=file_types, words=words, access_code=code,
                  hosts=tuple(sorted({l.host for l in links})))
    if not links and not file_types:
        return Verdict(False, "no link or file", **common)
    if links and not content and not file_types:
        return Verdict(False, "only " + "/".join(sorted({l.type for l in links})), **common)
    library = any(l.type in LIBRARY_TYPES for l in content) or code or any(
        t.startswith(LIBRARY_FILE_PREFIXES) for t in file_types) or len(file_types) >= 2
    limit = MAX_WORDS_LIBRARY if library else MAX_WORDS
    if words > limit:
        return Verdict(False, f"discussion ({words} words)", **common)
    if not library and file_types and all(t.startswith("image/") for t in file_types) and not content:
        return Verdict(False, "single image", **common)
    return Verdict(True, "library share" if library else "link share", **common)


# ---- Offline evaluation on Slack history (private files only; no message text stored) -------------------

def history_case(item: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    """Keep only what the evaluation needs: the rule's inputs as features, the verdict, and the label."""
    if item.get("channel") not in allowed:
        raise ValueError("channel is not on the allowed list")
    if not isinstance(item.get("worth"), bool) or item.get("confidence") not in {"high", "medium", "low"}:
        raise ValueError("worth (bool) and confidence (high, medium, low) are required")
    files = [{"mimetype": m} for m in item.get("files", [])]
    verdict = classify(str(item.get("text", "")), files)
    return {"channel": item["channel"], "ts": str(item.get("ts", "")), "worth": item["worth"],
            "confidence": item["confidence"], "borderline": bool(item.get("borderline")),
            "note": str(item.get("note", ""))[:80], "digest": verdict.digest, "reason": verdict.reason,
            "link_types": sorted({l.type for l in verdict.links}), "hosts": list(verdict.hosts),
            "file_types": sorted(set(verdict.file_types)), "files": len(verdict.file_types), "words": verdict.words, "access_code": verdict.access_code}


def evaluate_history(cases: list[dict[str, Any]], days: float) -> dict[str, Any]:
    from collections import Counter
    tp = sum(c["digest"] and c["worth"] for c in cases)
    fp = sum(c["digest"] and not c["worth"] for c in cases)
    fn = sum(not c["digest"] and c["worth"] for c in cases)
    per_channel = Counter(c["channel"] for c in cases if c["digest"])
    return {
        "messages_with_link_or_file": len(cases), "worth": sum(c["worth"] for c in cases),
        "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
        "tp": tp, "fp": fp, "fn": fn,
        "false_positive_reasons": dict(Counter(c["reason"] for c in cases if c["digest"] and not c["worth"])),
        "missed_reasons": dict(Counter(c["reason"] for c in cases if not c["digest"] and c["worth"])),
        "digests_per_week": {ch: round(n * 7 / days, 2) for ch, n in per_channel.items()} if days else {},
        "link_types_digested": dict(Counter(t for c in cases if c["digest"] for t in c["link_types"] or ["file"])),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import os
    import sys
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Digest-rule evaluation on private Slack history.")
    parser.add_argument("--dir", type=Path, default=Path.home() / ".config/tag-eval/digest")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("import", help="Add labelled messages from stdin JSON; stores features only.")
    add.add_argument("--allowed", type=Path, default=Path.home() / ".config/tag-eval/real/allowed-channels.txt")
    report = commands.add_parser("report")
    report.add_argument("--days", type=float, default=0.0, help="Days of history covered, for digests per week.")
    args = parser.parse_args(argv)
    args.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        args.dir.chmod(0o700)
    path = args.dir / "cases.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"cases": []}
    if args.command == "import":
        allowed = frozenset(line.strip() for line in args.allowed.read_text(encoding="utf-8").splitlines() if line.strip())
        by_key = {(c["channel"], c["ts"]): c for c in data["cases"]}
        errors = 0
        for item in json.load(sys.stdin):
            try:
                case = history_case(item, allowed)
            except ValueError as exc:
                errors += 1
                print(f"rejected: {exc}", file=sys.stderr)
                continue
            by_key[(case["channel"], case["ts"])] = case
        data["cases"] = list(by_key.values())
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        print(f"{len(data['cases'])} cases stored; {errors} rejected.", file=sys.stderr)
        return 0 if not errors else 1
    print(json.dumps(evaluate_history(data["cases"], args.days), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
