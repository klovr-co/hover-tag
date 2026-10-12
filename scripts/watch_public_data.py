#!/usr/bin/env python3
"""Turn public, human-annotated multi-party chat into channel-watching cases.

A development tool: it is not part of Tag's runtime. Converted text stays in a
private directory, because the rights in the underlying chat text are unclear
even where the annotations are openly licensed.

    irc   Ubuntu IRC disentanglement (Kummerfeld et al., ACL 2019), data CC BY 4.0.
          Each annotation `p c` says message c replies to message p; a self-link
          starts a new conversation. One recent participant plays the Tag.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from scripts import watch_classifier, watch_eval, watch_real_data
except ImportError:
    import watch_classifier
    import watch_eval
    import watch_real_data


CHAT_RE = re.compile(r"^\[(\d\d:\d\d)\] <([^>]+)> (.*)$")
BOT_NICKS = {"ubotu", "ubottu", "ubot5", "ubuntulog", "chanserv", "nickserv", "floodbot1", "floodbot2", "floodbot3"}
INCLUSIVE_RE = re.compile(r"\?|\b(?:anyone|anybody|someone|somebody|everyone|any ?one|any idea|does any)\b", re.I)
ASSISTANT_NAME = "Tag"
LICENCE = ("Ubuntu IRC disentanglement data (Kummerfeld et al., ACL 2019; Gouravajhala et al., ALTA 2023), "
           "CC BY 4.0, https://github.com/jkkummerfeld/irc-disentanglement. Changed: sampled, nicknames "
           "pseudonymised, mapped to addressee labels.")
STRATA = {  # Weighted toward hard cases.
    "continuation": 350, "named": 150, "inclusive": 250, "another_person": 400,
    "third_person": 100, "unrelated": 150, "aside_start": 100,
}


def parse_log(text_lines: list[str]) -> dict[int, dict[str, Any]]:
    messages = {}
    for index, line in enumerate(text_lines):
        match = CHAT_RE.match(line.rstrip("\n"))
        if match:
            messages[index] = {"index": index, "nick": match.group(2), "text": match.group(3)}
    return messages


def parse_links(lines: list[str]) -> tuple[dict[int, set[int]], set[int]]:
    parents: dict[int, set[int]] = {}
    starts: set[int] = set()
    for line in lines:
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        parent, child = sorted((int(parts[0]), int(parts[1])))
        if parent == child:
            starts.add(child)
        else:
            parents.setdefault(child, set()).add(parent)
    return parents, starts


def addressees(index: int, messages: dict, parents: dict, depth: int = 0) -> set[str]:
    """Nicks a message is addressed to; a speaker continuing their own message inherits its addressees."""
    nick = messages[index]["nick"]
    found: set[str] = set()
    for parent in parents.get(index, ()):
        if parent not in messages:
            continue
        if messages[parent]["nick"] == nick and depth < 5:
            found |= addressees(parent, messages, parents, depth + 1)
        else:
            found.add(messages[parent]["nick"])
    return found


def conversation(index: int, parents: dict) -> set[int]:
    """Every earlier message linked to this one, transitively."""
    seen, stack = set(), [index]
    while stack:
        for parent in parents.get(stack.pop(), ()):
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return seen


def _rename(text: str, nicks: list[str], assistant: str, pseudonyms: watch_eval.Pseudonyms) -> str:
    for nick in sorted(nicks, key=len, reverse=True):
        target = ASSISTANT_NAME if nick == assistant else pseudonyms(nick)
        text = re.sub(rf"(?<![\w-]){re.escape(nick)}(?![\w-])", target, text, flags=re.I)
    return watch_eval.scrub(text)


def candidates(source: str, messages: dict, parents: dict, starts: set[int]) -> list[dict[str, Any]]:
    """Every (message, assistant) pair worth a case, with its stratum and label."""
    found = []
    ordered = sorted(messages)
    for position, index in enumerate(ordered):
        message = messages[index]
        if index < 1000 or message["nick"].lower() in BOT_NICKS or message["text"].lstrip().startswith("!"):
            continue  # Context lines, bots, and bot commands such as "!language | nick".
        if index not in starts and index not in parents:
            continue  # Not annotated.
        recent = [messages[i]["nick"] for i in ordered[max(0, position - 10):position]]
        recent = [n for n in dict.fromkeys(reversed(recent)) if n != message["nick"] and n.lower() not in BOT_NICKS]
        thread = conversation(index, parents)
        thread_nicks = {messages[i]["nick"] for i in thread if i in messages}
        targets = addressees(index, messages, parents)
        text = message["text"]
        prefixed = lambda nick: re.match(rf"\W*{re.escape(nick)}\b", text, re.I) is not None  # noqa: E731
        leading = " ".join(text.split()[:3])
        near_start = lambda nick: re.search(rf"(?<![\w-]){re.escape(nick)}(?![\w-])", leading, re.I) is not None  # noqa: E731
        options = []
        if index in starts and not targets:
            label = bool(INCLUSIVE_RE.search(text))
            options = [(nick, label, "inclusive" if label else "aside_start") for nick in recent[:3]]
        else:
            for nick in recent:
                if nick in targets:
                    options.append((nick, True, "named" if prefixed(nick) else "continuation"))
                elif near_start(nick):
                    continue  # Possibly addressed alongside someone else: too ambiguous to label.
                elif re.search(rf"(?<![\w-]){re.escape(nick)}(?![\w-])", text, re.I):
                    options.append((nick, False, "third_person"))
                elif nick in thread_nicks:
                    options.append((nick, False, "another_person"))
                else:
                    options.append((nick, False, "unrelated"))
        for nick, label, stratum in options:
            found.append({"source_file": source, "index": index, "assistant": nick, "expected": label,
                          "stratum": stratum, "thread": sorted(thread)})
    return found


def build_case(candidate: dict, messages: dict) -> dict[str, Any]:
    assistant = candidate["assistant"]
    message = messages[candidate["index"]]
    history_messages = [messages[i] for i in candidate["thread"] if i in messages]
    # Every nick seen in this log, so names mentioned in text are swapped too, each to a unique pseudonym.
    nicks = list(dict.fromkeys([m["nick"] for m in history_messages] + [message["nick"], assistant]
                               + [m["nick"] for m in messages.values()]))
    pseudonyms = watch_eval.Pseudonyms(f"{candidate['source_file']}|{candidate['index']}")
    for nick in nicks:
        if nick != assistant:
            pseudonyms(nick)
    window = watch_classifier.thread_window(
        [{"ts": str(m["index"]), "user": m["nick"], "text": m["text"],
          **({"bot_id": "B"} if m["nick"].lower() in BOT_NICKS else {})} for m in history_messages],
        str(message["index"]))
    state = watch_classifier.message_state(
        {"user": message["nick"], "text": message["text"], "ts": str(message["index"])}, window,
        assistant_name=ASSISTANT_NAME, assistant_user_id=assistant)
    state["current_message"] = _rename(state["current_message"], nicks, assistant, pseudonyms)
    for entry in state["thread_history"]:
        entry["text"] = _rename(entry["text"], nicks, assistant, pseudonyms)
    stratum = candidate["stratum"]
    identifier = "p-irc-" + hashlib.sha256(
        f"{candidate['source_file']}|{candidate['index']}|{assistant}".encode()).hexdigest()[:12]
    names = (ASSISTANT_NAME,)
    return {
        "id": identifier, "group": hashlib.sha256(f"{candidate['source_file']}|{candidate['index']}".encode()).hexdigest()[:12],
        "source": "C-public-irc", "category": stratum, "expected": candidate["expected"],
        # A conversation start has no reply-to label; its inclusive/aside label is a heuristic for the second
        # labeller to confirm. Openers without a question are often implicit help requests.
        "confidence": {"inclusive": "medium", "aside_start": "low"}.get(stratum, "high"),
        "borderline": stratum == "aside_start", "derived": None, "channel": "public-irc",
        "reachable": bool(re.search(r"(?<![\w'])Tag(?![\w])", state["current_message"], re.I))
        or any(e["author"] == "tag" for e in state["thread_history"]),
        "reason": f"from reply-to annotation ({stratum})", "state": state,
        "current_raw_noise": watch_real_data.is_noise(message["text"]),
        "current_test": False, "history_test": False, "flags": watch_real_data.flags(message["text"]),
    }


def sample(found: list[dict], strata: dict[str, int], seed: int) -> list[dict]:
    """Stratified sample, at most one case per message, so labels can't conflict across assistants."""
    rng = random.Random(seed)
    rng.shuffle(found)
    taken, used = [], set()
    counts: Counter = Counter()
    for candidate in found:
        key = (candidate["source_file"], candidate["index"])
        if key in used or counts[candidate["stratum"]] >= strata.get(candidate["stratum"], 0):
            continue
        used.add(key)
        counts[candidate["stratum"]] += 1
        taken.append(candidate)
    return taken


def load_irc(root: Path, splits: tuple[str, ...]) -> tuple[dict[str, dict], dict[str, tuple]]:
    logs, links = {}, {}
    for split in splits:
        for annotation in sorted((root / "data" / split).glob("*.annotation.txt")):
            stem = annotation.name[: -len(".annotation.txt")]
            log = annotation.with_name(stem + ".ascii.txt")
            if not log.exists():
                continue
            key = f"{split}/{stem}"
            logs[key] = parse_log(log.read_text(encoding="utf-8", errors="replace").splitlines())
            links[key] = parse_links(annotation.read_text(encoding="utf-8").splitlines())
    return logs, links


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    irc = commands.add_parser("irc")
    irc.add_argument("--data", type=Path, required=True, help="Checkout of jkkummerfeld/irc-disentanglement.")
    irc.add_argument("--out", type=Path, required=True, help="Private output file.")
    irc.add_argument("--splits", default="dev,test,channel-two,new-domains")
    irc.add_argument("--seed", type=int, default=13)
    args = parser.parse_args(argv)
    logs, links = load_irc(args.data, tuple(args.splits.split(",")))
    found = []
    for key, messages in logs.items():
        parents, starts = links[key]
        found += candidates(key, messages, parents, starts)
    chosen = sample(found, STRATA, args.seed)
    cases = [build_case(candidate, logs[candidate["source_file"]]) for candidate in chosen]
    watch_real_data._private_write(args.out, {
        "schema_version": 1, "assistant_name": ASSISTANT_NAME, "licence": LICENCE,
        "description": "Public IRC cases mapped to channel watching. Private: underlying chat text rights unclear.",
        "cases": cases})
    print(json.dumps({"logs": len(logs), "candidates": len(found),
                      "candidates_by_stratum": dict(Counter(c["stratum"] for c in found)),
                      "sampled_by_stratum": dict(Counter(c["category"] for c in cases)),
                      "positive": sum(c["expected"] for c in cases), "reachable": sum(c["reachable"] for c in cases)},
                     indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
