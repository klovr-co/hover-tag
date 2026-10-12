#!/usr/bin/env python3
"""Clean a private, real labelled set for the channel-watching classifier.

A development tool: it is not part of Tag's runtime. Real cases live only in a
private directory outside the repository; nothing here prints message text.

    rebuild  Rebuild each case's context from complete raw thread dumps on stdin,
             exactly as production would see it, removing noise as it goes.
    clean    Drop noise, test traffic and duplicates; flag hard-to-judge messages.
    blind    Write cases for an independent second labeller, without the first labels.
    agree    Compare the two labellings: agreement, Cohen's kappa, and the final set.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from scripts import watch_classifier, watch_eval
except ImportError:
    import watch_classifier
    import watch_eval


DERIVATION_ORDER = (None, "tag-mention-removed", "tag-mention-to-name", "person-mention-to-name")
NOISE_RE = re.compile(
    r"has (?:joined|left) the channel|set the channel (?:topic|purpose|description)|renamed the channel"
    r"|pinned a message|(?:started|ended|joined|left) (?:a |the )?huddle|huddle (?:started|ended)"
    r"|was added to the channel|archived the channel|\bstarted a call\b|\bcall ended\b",
    re.I)
TEST_RE = re.compile(
    r"^\W*(?:re)?test(?:ing)?\W*\d*\W*$|^\W*(?:re)?test(?:ing)? \S+(?: \S+)?\W*$"
    r"|\b(?:steer|queue|retry|stop|approval|stream|smoke|stress|handoff|dm|thread|button|memory) test\b"
    r"|^\W*\d+\s*[-+*/x]\s*\d+\W*$|^\W*(?:ping|asdf|foo|bar|hello\?)\W*$",
    re.I)
PLACEHOLDER_RE = re.compile(r"<(?:link|email|phone|member|participant|tag|requester|date)>")
EMOJI_ONLY_RE = re.compile(r"^(?:\s|:[\w+-]+:|[☀-➿\U0001f000-\U0001faff️‍]|[!?.,])*$")
MALAY = {"saya", "kamu", "awak", "tak", "nak", "boleh", "ini", "itu", "dan", "yang", "untuk", "dengan",
         "ada", "tidak", "sudah", "belum", "apa", "macam", "lagi", "juga", "kita", "dia", "mereka", "kenapa"}
LONG_CHARS, LONG_LINES = 1200, 15


def _private_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=1)
    os.replace(temporary, path)
    path.chmod(0o600)


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def case_id(channel: str, ts: str, derived: str | None) -> str:
    return "r-" + hashlib.sha256(f"{channel}|{ts}|{derived}".encode()).hexdigest()[:12]


def is_noise(text: str) -> str | None:
    """Why a message is noise, or None."""
    scrubbed = watch_eval.scrub(text or "")
    if NOISE_RE.search(scrubbed):
        return "system message"
    bare = PLACEHOLDER_RE.sub("", re.sub(r"<link>|<@[^>]*>", "", scrubbed)).strip()
    if not bare:
        return "empty or link only"
    if EMOJI_ONLY_RE.fullmatch(bare):
        return "emoji only"
    return None


def is_test(text: str) -> bool:
    return bool(TEST_RE.search(" ".join((text or "").split())))


def flags(text: str) -> list[str]:
    """Hard-to-judge messages are flagged for review, never silently kept."""
    found = []
    if "```" in text or "Traceback (most recent call last)" in text or len(
            [line for line in text.splitlines() if re.search(r"[{};=]|^\s+at \S+\(|\w+Error\b", line)]) >= 3:
        found.append("code or log")
    if len(text) > LONG_CHARS or len(text.splitlines()) > LONG_LINES:
        found.append("long document")
    letters = [c for c in text if c.isalpha()]
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if (letters and sum(not c.isascii() for c in letters) / len(letters) > 0.2) or (
            len(words) >= 3 and sum(w in MALAY for w in words) / len(words) >= 0.25):
        found.append("non-English")
    tokens = text.split()
    if tokens and sum(bool(PLACEHOLDER_RE.search(t)) for t in tokens) / len(tokens) >= 0.5:
        found.append("mostly scrubbed")
    return found


def rebuild(cases: dict[str, dict], dumps: list[dict], bots: dict[str, str],
            done: dict[str, dict], removed: Counter) -> int:
    """Rebuild matching cases from complete raw dumps. Returns how many were rebuilt."""
    names_to_ids: dict[str, list[str]] = {}
    for user, name in bots.items():
        names_to_ids.setdefault(name, []).append(user)
    count = 0
    for dump in dumps:
        channel = dump.get("channel", "")
        raw = [m for m in dump.get("messages", []) if isinstance(m, dict) and m.get("ts")]
        in_thread = bool(dump.get("thread"))
        conversational = []
        for message in raw:
            user = message.get("user", "")
            if message.get("bot") and user not in bots:
                removed["integration post in history"] += 1
                continue
            if is_noise(message.get("text", "")):
                removed["system or empty message in history"] += 1
                continue
            conversational.append({**message, "text": str(message.get("text", ""))[:watch_classifier.MAX_MESSAGE_CHARS]})
        for message in raw:
            for derived in DERIVATION_ORDER:
                identifier = case_id(channel, message["ts"], derived)
                if identifier not in cases:
                    continue
                case = cases[identifier]
                name = case["state"]["assistant"]["name"]
                present = {m.get("user") for m in raw} | set(re.findall(r"<@([A-Z0-9]+)", json.dumps(raw)))
                candidates = names_to_ids.get(name, [])
                assistant = next((u for u in candidates if u in present), candidates[0] if candidates else "")
                if not assistant:
                    continue
                history = []
                if in_thread and message.get("index", 0) != 0:
                    window = _complete_window(raw, conversational, message)
                    if window is None:
                        removed["thread context incomplete"] += 1
                        continue
                    history = window
                item = {"channel": channel, "ts": message["ts"], "requester": message.get("user", ""),
                        "assistant": {"user_id": assistant, "name": name},
                        "bots": [u for u in bots if u != assistant],
                        "history": [{"user": m.get("user", ""), "text": m["text"]} for m in history],
                        "message": message.get("text", ""), "derived": derived,
                        "expected": case["expected"], "confidence": case["confidence"],
                        "borderline": case.get("borderline", False), "category": case.get("category"),
                        "reason": case.get("reason", "")}
                try:
                    fresh = watch_eval.real_case(item, frozenset({channel}))
                except ValueError:
                    continue
                fresh.update(
                    group=hashlib.sha256(f"{channel}|{message['ts']}".encode()).hexdigest()[:12],
                    current_raw_noise=is_noise(message.get("text", "")),
                    current_test=is_test(message.get("text", "")),
                    history_test=any(is_test(m["text"]) for m in history),
                    flags=flags(message.get("text", "")),
                )
                done[identifier] = fresh
                count += 1
    return count


def _complete_window(raw: list[dict], conversational: list[dict], message: dict) -> list[dict] | None:
    """The production window, but only when the dump provably contains everything it needs.

    Each dumped message carries its position in the thread (0 = parent). The parent and
    an unbroken run of messages ending just before the current one must be present, and
    that run must either reach the parent or hold enough conversation to fill the window.
    """
    positions = {m.get("index") for m in raw if isinstance(m.get("index"), int)}
    current = message.get("index")
    if not isinstance(current, int) or 0 not in positions:
        return None
    lowest = current
    while lowest - 1 >= 1 and lowest - 1 in positions:
        lowest -= 1
    allowed = {0, *range(lowest, current)}
    usable = [m for m in conversational if m.get("index") in allowed]
    window = watch_classifier.thread_window(usable, message["ts"])
    if lowest > 1 and len([m for m in usable if m.get("index") != 0]) < watch_classifier.MAX_CONTEXT_MESSAGES - 1:
        return None
    return window


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w<>]+", " ", text.lower()).split())


def clean(rebuilt: list[dict], raw_ids: list[str]) -> tuple[list[dict], list[dict], dict[str, int]]:
    """Apply the cleaning rules. Returns kept, flagged, and removals by rule."""
    removed: Counter = Counter()
    rebuilt_ids = {case["id"] for case in rebuilt}
    removed["context could not be rebuilt"] = sum(identifier not in rebuilt_ids for identifier in raw_ids)
    survivors = []
    for case in rebuilt:
        if case.get("current_raw_noise"):
            removed[f"current message: {case['current_raw_noise']}"] += 1
        elif case.get("current_test") or case.get("history_test"):
            removed["test traffic"] += 1
        else:
            survivors.append(case)
    by_group: dict[str, dict] = {}
    for case in sorted(survivors, key=lambda c: DERIVATION_ORDER.index(c.get("derived"))):
        if case["group"] in by_group:
            removed["same message reused across cases"] += 1
        else:
            by_group[case["group"]] = case
    kept: list[dict] = []
    for case in by_group.values():
        current = _normal(case["state"]["current_message"])
        context = [_normal(h["text"]) for h in case["state"]["thread_history"][-2:]]
        duplicate = None
        for other in kept:
            other_current = _normal(other["state"]["current_message"])
            other_context = [_normal(h["text"]) for h in other["state"]["thread_history"][-2:]]
            if current == other_current and context == other_context:
                duplicate = "exact duplicate"
            elif (len(current) >= 15 and difflib.SequenceMatcher(None, current, other_current).ratio() >= 0.92
                  and difflib.SequenceMatcher(None, " ".join(context), " ".join(other_context)).ratio() >= 0.9):
                duplicate = "near duplicate"
            if duplicate:
                break
        if duplicate:
            removed[duplicate] += 1
        else:
            kept.append(case)
    flagged = [case for case in kept if case.get("flags")]
    kept = [case for case in kept if not case.get("flags")]
    return kept, flagged, dict(removed)


def blind(cases: list[dict]) -> list[dict]:
    """What the second labeller sees: no first labels, categories, reasons or confidence."""
    return [{"id": c["id"], "assistant": c["state"]["assistant"]["name"],
             "thread_history": c["state"]["thread_history"], "current_message": c["state"]["current_message"],
             "derived": c.get("derived")} for c in cases]


def kappa(first: list[bool], second: list[bool]) -> float | None:
    total = len(first)
    if not total:
        return None
    observed = sum(a == b for a, b in zip(first, second)) / total
    p1, p2 = sum(first) / total, sum(second) / total
    expected = p1 * p2 + (1 - p1) * (1 - p2)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def agree(cases: list[dict], labels: dict[str, dict]) -> dict[str, Any]:
    """Keep agreed, clear, natural-sounding cases; disagreements go to the user's review list."""
    removed: Counter = Counter()
    compared, agreed, review = [], [], []
    for case in cases:
        second = labels.get(case["id"])
        if not second:
            removed["no second label"] += 1
            continue
        if second.get("unclear"):
            removed["meaning lost in scrubbing or missing context"] += 1
            continue
        if case.get("derived") and second.get("derived_natural") is False:
            removed["unnatural derived message"] += 1
            continue
        compared.append((case["expected"], bool(second["expected"])))
        if case["expected"] == bool(second["expected"]):
            agreed.append(case | {"second_confidence": second.get("confidence")})
        else:
            review.append(case["id"])
    first, other = [a for a, _ in compared], [b for _, b in compared]
    borderline = sorted({c["id"] for c in agreed if c.get("borderline") or c.get("confidence") == "low"
                         or c.get("second_confidence") == "low"})
    return {"final": agreed, "disagreements": review, "borderline": borderline, "removed": dict(removed),
            "agreement": (sum(a == b for a, b in compared) / len(compared)) if compared else None,
            "kappa": kappa(first, other), "compared": len(compared)}


def coverage(cases: list[dict]) -> dict[str, Any]:
    hard = {"inclusive": "inclusive question", "about_tag": "talk about Tag", "tag_verb_or_product": "tag as verb or product",
            "another_person": "reply to another person", "another_bot": "reply to another bot",
            "continuation": "continuation", "resume": "continuation", "direct": "direct", "aside": "aside",
            "easy_negative": "easy negative"}
    return {
        "total": len(cases),
        "natural_vs_derived": dict(Counter("natural" if c.get("derived") is None else "derived" for c in cases)),
        "positive_vs_negative": dict(Counter("positive" if c["expected"] else "negative" for c in cases)),
        "channel": dict(Counter(c["channel"] for c in cases)),
        "category": dict(Counter(hard.get(c.get("category"), c.get("category")) for c in cases)),
        "category_natural": dict(Counter(hard.get(c.get("category"), c.get("category"))
                                         for c in cases if c.get("derived") is None)),
        "reachable": sum(bool(c.get("reachable")) for c in cases),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path.home() / ".config/tag-eval/real")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rebuild", help="Rebuild cases from raw thread dumps on stdin.")
    commands.add_parser("status", help="Which raw cases are not rebuilt yet, by group file.")
    commands.add_parser("clean")
    commands.add_parser("blind")
    agree_parser = commands.add_parser("agree")
    agree_parser.add_argument("labels", type=Path, nargs="+")
    args = parser.parse_args(argv)
    root = args.dir
    raw_cases = {c["id"]: c for path in sorted(root.glob("cases-?.json")) for c in _read(path, {"cases": []})["cases"]}
    rebuilt_path = root / "work/rebuilt.json"
    state = _read(rebuilt_path, {"cases": {}, "removed": {}})

    if args.command == "rebuild":
        allowed = frozenset(line.strip() for line in (root / "allowed-channels.txt").read_text().splitlines() if line.strip())
        dumps = json.load(sys.stdin)
        dumps = dumps if isinstance(dumps, list) else [dumps]
        outside = [d for d in dumps if d.get("channel") not in allowed]
        if outside:
            print(f"Refused {len(outside)} dump(s) from channels that aren't allowed.", file=sys.stderr)
        removed = Counter(state["removed"])
        count = rebuild(raw_cases, [d for d in dumps if d not in outside],
                        _read(root / "bots.json", {})["conversational"], state["cases"], removed)
        state["removed"] = dict(removed)
        _private_write(rebuilt_path, state)
        print(f"Rebuilt {count} case(s); {len(state['cases'])} of {len(raw_cases)} done.", file=sys.stderr)
        return 0
    if args.command == "status":
        for path in sorted(root.glob("cases-?.json")):
            ids = [c["id"] for c in _read(path, {"cases": []})["cases"]]
            missing = [i for i in ids if i not in state["cases"]]
            print(f"{path.name}: {len(ids) - len(missing)}/{len(ids)} rebuilt; missing: {' '.join(missing) or 'none'}")
        return 0
    if args.command == "clean":
        kept, flagged, removed = clean(list(state["cases"].values()), list(raw_cases))
        for name, value in state["removed"].items():
            removed[f"(messages) {name}"] = value
        _private_write(root / "work/clean.json", {"cases": kept})
        _private_write(root / "work/flagged.json", {"cases": flagged})
        report = {"raw": len(raw_cases), "removed": removed, "flagged": len(flagged),
                  "flags": dict(Counter(f for c in flagged for f in c["flags"])), "kept": len(kept)}
        _private_write(root / "work/clean-report.json", report)
        print(json.dumps(report, indent=1))
        return 0
    if args.command == "blind":
        cases = _read(root / "work/clean.json", {"cases": []})["cases"] + _read(root / "work/flagged.json", {"cases": []})["cases"]
        _private_write(root / "work/blind.json", {"cases": blind(cases)})
        print(f"Wrote {len(cases)} blinded case(s).", file=sys.stderr)
        return 0
    labels: dict[str, dict] = {}
    for path in args.labels:
        for row in _read(path, {"labels": []})["labels"]:
            labels[row["id"]] = row
    result = agree(_read(root / "work/clean.json", {"cases": []})["cases"], labels)
    flagged = agree(_read(root / "work/flagged.json", {"cases": []})["cases"], labels)
    _private_write(root / "final/cases.json", {"schema_version": 1, "assistant_name": "Tag",
                                               "description": "Cleaned real cases. Private.", "cases": result["final"]})
    _private_write(root / "final/flagged-cases.json", {"schema_version": 1, "assistant_name": "Tag",
                                                       "description": "Flagged real cases. Private.", "cases": flagged["final"]})
    review = {"disagreements": sorted(result["disagreements"] + flagged["disagreements"]),
              "borderline": sorted(set(result["borderline"]) | set(flagged["borderline"])),
              "flagged": sorted(c["id"] for c in flagged["final"])}
    _private_write(root / "final/review-ids.json", review)
    summary = {"compared": result["compared"], "agreement": result["agreement"], "kappa": result["kappa"],
               "removed": result["removed"], "final": coverage(result["final"]),
               "flagged_final": len(flagged["final"]), "flagged_agreement": flagged["agreement"],
               "review_counts": {k: len(v) for k, v in review.items()}}
    _private_write(root / "final/summary.json", summary)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
