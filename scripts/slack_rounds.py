# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
"""Several rounds of work in one Slack thread, with one live message.

Only the Tag's latest message in a thread is live. It is posted within about a
second of the request, edited at most every few seconds while the Tag works,
turned into the question when the Tag needs an answer, shrunk to one answered
line once someone answers, and edited into the outcome card at the end. The
outcome is therefore always the Tag's last message in the thread.

The backend ends its answer with a short structured block (``tag-question`` or
``tag-outcome``); without one, the answer is treated as the outcome and the
card's headline falls back to the TL;DR.

Thread state lives in ``state/slack-rounds.json`` so a question survives a
restart and an answer days later still resumes the work.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .slack_mrkdwn import to_mrkdwn
    from .tag_activity import reply_preview
    from .tag_links import thread_link
    from .tag_paths import instance_home, restrict_windows_acl
except ImportError:  # Direct script execution does not create a package context.
    from slack_mrkdwn import to_mrkdwn
    from tag_activity import reply_preview
    from tag_links import thread_link
    from tag_paths import instance_home, restrict_windows_acl


EDIT_INTERVAL_SECONDS = 3.0
MAX_RESULTS = 5
MAX_CHOICES = 5
MAX_ACTIONS = 3
MAX_HEADLINE_CHARS = 150
MAX_RESULT_CHARS = 200
MAX_QUESTION_CHARS = 1_500
MAX_ANSWER_CHARS = 120
MAX_BODY_BLOCKS = 40
SECTION_CHARS = 2_900
RETENTION_SECONDS = 30 * 24 * 60 * 60
CHOICE_ACTION_PREFIX = "opentag_round_choice_"
CONFIRM_ACTION_ID = "opentag_round_confirm"
DECLINE_ACTION_ID = "opentag_round_decline"
OPEN_APP_ACTION_ID = "opentag_open_tag_app"
STILL_WAITING = "Still waiting for an answer"
RESULT_MARKS = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
# Slack accepts a hover-tag:// URL on a link button (checked live in the sandbox).
# Set to False to fall back to a link in the context line.
LINK_BUTTONS = True
_BLOCK_RE = re.compile(r"```[ \t]*(tag-outcome|tag-question)[ \t]*\r?\n(.*?)```", re.S)
_MARK_TO_STATUS = {"✅": "ok", "⚠️": "warn", "⚠": "warn", "❌": "fail"}


# Structured blocks ----------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    label: str
    risky: bool = False


@dataclass(frozen=True)
class Question:
    text: str
    choices: tuple[Choice, ...] = ()
    risky: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "risky": self.risky,
                "choices": [{"label": choice.label, "risky": choice.risky} for choice in self.choices]}

    @classmethod
    def from_dict(cls, value: Any) -> "Question | None":
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            return None
        choices = tuple(Choice(str(item.get("label", ""))[:75], bool(item.get("risky")))
                        for item in value.get("choices") or [] if isinstance(item, dict) and item.get("label"))
        return cls(value["text"], choices, bool(value.get("risky")))


@dataclass(frozen=True)
class Result:
    status: str  # ok, warn, or fail
    text: str


@dataclass(frozen=True)
class Action:
    label: str
    url: str


@dataclass(frozen=True)
class Outcome:
    headline: str = ""
    results: tuple[Result, ...] = ()
    actions: tuple[Action, ...] = ()


@dataclass(frozen=True)
class ParsedAnswer:
    body: str
    outcome: Outcome | None = None
    question: Question | None = None


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2_000 or not re.fullmatch(r"https://[^\s<>|]+", value):
        return None
    return value


def _parse_outcome(payload: Any) -> Outcome | None:
    if not isinstance(payload, dict):
        return None
    results: list[Result] = []
    for item in payload.get("results") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            status = item.get("status") if item.get("status") in RESULT_MARKS else "warn"
            text = item["text"]
        elif isinstance(item, str):
            mark = next((mark for mark in _MARK_TO_STATUS if item.lstrip().startswith(mark)), None)
            status = _MARK_TO_STATUS[mark] if mark else "warn"
            text = item.lstrip()[len(mark):] if mark else item
        else:
            continue
        if text.strip():
            results.append(Result(status, _clip(text.strip(), MAX_RESULT_CHARS)))
    actions = []
    for item in payload.get("actions") or []:
        if isinstance(item, dict) and isinstance(item.get("label"), str) and (url := _safe_url(item.get("url"))):
            actions.append(Action(_clip(item["label"], 30), url))
    headline = payload.get("headline")
    return Outcome(_clip(headline, MAX_HEADLINE_CHARS) if isinstance(headline, str) else "",
                   tuple(results[:MAX_RESULTS]), tuple(actions[:MAX_ACTIONS]))


def _parse_question(payload: Any) -> Question | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("question"), str):
        return None
    text = payload["question"].strip()[:MAX_QUESTION_CHARS]
    if not text:
        return None
    choices: list[Choice] = []
    for item in payload.get("choices") or []:
        if isinstance(item, str) and item.strip():
            choices.append(Choice(_clip(item, 75)))
        elif isinstance(item, dict) and isinstance(item.get("label"), str) and item["label"].strip():
            choices.append(Choice(_clip(item["label"], 75), bool(item.get("risky"))))
    return Question(text, tuple(choices[:MAX_CHOICES]), bool(payload.get("risky")))


def parse_answer(answer: str) -> ParsedAnswer:
    """Split the backend's answer into prose and its last structured block, if any.

    A block that isn't valid JSON is left in the prose, so nothing is lost.
    """
    matches = list(_BLOCK_RE.finditer(answer))
    for match in reversed(matches):
        try:
            payload = json.loads(match.group(2))
        except ValueError:
            continue
        body = (answer[:match.start()] + answer[match.end():]).strip()
        if match.group(1) == "tag-question":
            question = _parse_question(payload)
            if question is not None:
                return ParsedAnswer(body, question=question)
        else:
            outcome = _parse_outcome(payload)
            if outcome is not None:
                return ParsedAnswer(body, outcome=outcome)
    return ParsedAnswer(answer.strip())


# Copy -----------------------------------------------------------------------


def slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def request_headline(text: str) -> str:
    """A short headline from the request itself, shown until the work has a better one."""
    text = re.sub(r"<@[^>]+>", "", text)
    text = re.sub(r"<(?:https?://|mailto:)[^>|]+\|([^>]+)>", r"\1", text)
    text = re.sub(r"<([^>|]+)>", r"\1", text)
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first = first[:1].upper() + first[1:]
    return _clip(first, 80) or "Your request"


def doing_text(label: str) -> str:
    """'Searching the web…' becomes 'searching the web'."""
    label = label.strip().rstrip("…").rstrip(".").strip()
    return label[:1].lower() + label[1:] if label and not label[:2].isupper() else label


def progress_text(steps: int, estimate: int | None, doing: str = "", *, paused: bool = False) -> str:
    if steps <= 0 and not paused:
        return f"Starting · {doing}" if doing else "Starting"
    step = max(steps, 1)
    total = f" of ~{max(estimate, step + (0 if paused else 1))}" if estimate else ""
    text = f"Paused at step {step}{total}" if paused else f"Step {step}{total}"
    return f"{text} · {doing}" if doing and not paused else text


def duration_text(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def context_line(*, files: Iterable[str] = (), remembered: Iterable[str] = (), steps: int = 0,
                 seconds: float | None = None) -> str:
    parts = []
    files = [slack_escape(name) for name in files if name]
    if files:
        shown = ", ".join(files[:3]) + (f" +{len(files) - 3}" if len(files) > 3 else "")
        parts.append(f"📄 {shown}")
    remembered = [item for item in remembered if item]
    if remembered:
        shown = "; ".join(remembered[:2]) + (f" +{len(remembered) - 2}" if len(remembered) > 2 else "")
        parts.append(f"🧠 Remembered: {shown}")
    timing = []
    if steps:
        timing.append(f"{steps} step{'s' if steps != 1 else ''}")
    if seconds is not None:
        timing.append(duration_text(seconds))
    if timing:
        parts.append(", ".join(timing))
    return " · ".join(parts)


# Blocks ---------------------------------------------------------------------


def _open_app(link: str | None) -> dict[str, Any] | None:
    if not link or not LINK_BUTTONS:
        return None
    return {"type": "button", "action_id": OPEN_APP_ACTION_ID,
            "text": {"type": "plain_text", "text": "Open in Tag.app"}, "url": link}


def _with_link(text: str, link: str | None) -> str:
    """Without link buttons, the link rides in the small line instead."""
    if not link or LINK_BUTTONS:
        return text
    return f"{text} · <{link}|Open in Tag.app>" if text else f"<{link}|Open in Tag.app>"


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3_000]}]}


def live_blocks(headline: str, progress: str, link: str | None = None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"⏳ *Working:* {slack_escape(headline)}"}},
        _context(_with_link(slack_escape(progress), link)),
    ]
    if (button := _open_app(link)) is not None:
        blocks.append({"type": "actions", "elements": [button]})
    return blocks


def choice_value(team: str, channel: str, thread_ts: str, question_id: str, index: int) -> str:
    return json.dumps({"team": team, "channel": channel, "thread_ts": thread_ts, "q": question_id, "i": index},
                      separators=(",", ":"))


def question_blocks(question: Question, *, progress: str, team: str, channel: str, thread_ts: str,
                    question_id: str, link: str | None = None, still_waiting: bool = False) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": to_mrkdwn(question.text)[:SECTION_CHARS]}}]
    elements = [{
        "type": "button", "action_id": f"{CHOICE_ACTION_PREFIX}{index}",
        "text": {"type": "plain_text", "text": choice.label},
        "value": choice_value(team, channel, thread_ts, question_id, index),
        **({"style": "primary"} if index == 0 and not choice.risky else {}),
    } for index, choice in enumerate(question.choices)]
    if (button := _open_app(link)) is not None and not elements:
        elements.append(button)
    if elements:
        blocks.append({"type": "actions", "elements": elements})
    small = f"⏸ {progress}"
    if elements and _open_app(link) is not None and question.choices:
        small = f"⏸ {progress} · <{link}|Open in Tag.app>"
    if still_waiting:
        small += f" · {STILL_WAITING}"
    blocks.append(_context(_with_link(small, link)))
    return blocks


def answered_text(question: str, answer: str, by_name: str) -> str:
    """'Which org unit? ✓ Leadership, from Maya' on one line."""
    asked = _clip(reply_preview(question) or question, 120)
    return f"{slack_escape(asked)} *✓ {slack_escape(_clip(answer, MAX_ANSWER_CHARS))}*, from {slack_escape(by_name)}"


def answered_blocks(question: str, answer: str, by_name: str) -> list[dict[str, Any]]:
    return [_context(answered_text(question, answer, by_name))]


def confirm_blocks(*, requester: str, answer: str, by_name: str, team: str, channel: str,
                   thread_ts: str, question_id: str) -> list[dict[str, Any]]:
    value = json.dumps({"team": team, "channel": channel, "thread_ts": thread_ts, "q": question_id},
                       separators=(",", ":"))
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"<@{requester}>, {slack_escape(by_name)} answered *{slack_escape(_clip(answer, MAX_ANSWER_CHARS))}*. "
            "That approves something risky, so I'll wait for you."}},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": CONFIRM_ACTION_ID, "style": "primary",
             "text": {"type": "plain_text", "text": "Go ahead"}, "value": value},
            {"type": "button", "action_id": DECLINE_ACTION_ID,
             "text": {"type": "plain_text", "text": "Don't"}, "value": value},
        ]},
    ]


def _sections(text: str) -> list[dict[str, Any]]:
    """Long text as several sections, split at line breaks where possible."""
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= SECTION_CHARS:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, SECTION_CHARS)
        if cut < SECTION_CHARS // 2:
            cut = remaining.rfind(" ", 0, SECTION_CHARS)
        if cut < SECTION_CHARS // 2:
            cut = SECTION_CHARS
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if len(chunks) > MAX_BODY_BLOCKS:
        chunks = chunks[:MAX_BODY_BLOCKS - 1] + ["_The rest of this reply is in Tag.app._"]
    return [{"type": "section", "text": {"type": "mrkdwn", "text": chunk}} for chunk in chunks]


def outcome_blocks(*, headline: str, results: Iterable[Result] = (), actions: Iterable[Action] = (),
                   body: str = "", link: str | None = None, small: str = "",
                   action_value: Callable[[Action], str] | None = None) -> list[dict[str, Any]]:
    """The outcome card: one headline, up to five marked results, needed actions, one small line."""
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{slack_escape(headline)}*"}}] if headline else []
    results = list(results)[:MAX_RESULTS]
    if results:
        lines = "\n".join(f"{RESULT_MARKS[item.status]} {to_mrkdwn(item.text)}" for item in results)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": lines[:SECTION_CHARS]}})
    elif body:
        blocks.extend(_sections(to_mrkdwn(body)))
    elements = []
    for action in list(actions)[:MAX_ACTIONS]:
        element = {"type": "button", "action_id": f"opentag_outcome_action_{len(elements)}",
                   "text": {"type": "plain_text", "text": action.label}, "url": action.url, "style": "primary"}
        if action_value is not None:
            element["value"] = action_value(action)
        elements.append(element)
    if (button := _open_app(link)) is not None:
        elements.append(button)
    if elements:
        blocks.append({"type": "actions", "elements": elements})
    small = _with_link(small, link)
    if small:
        blocks.append(_context(small))
    return blocks


def split_headline(body: str) -> tuple[str, str]:
    """A reply's own short first line, such as its title, and the rest; ("", body) when it has none."""
    first, _, rest = body.strip().partition("\n")
    title = re.sub(r"^#{1,6}\s+", "", first.strip())
    for mark in ("**", "__", "*", "_"):
        if len(title) > 2 * len(mark) and title.startswith(mark) and title.endswith(mark):
            title = title[len(mark):-len(mark)].strip()
            break
    if not title or len(title) > MAX_HEADLINE_CHARS or "\n" in title:
        return "", body.strip()
    return title, rest.strip()


def card_headline(parsed: ParsedAnswer) -> tuple[str, str]:
    """The card's first headline and the reply shown under it, until the TL;DR replaces the headline.

    Without a headline the card starts with the reply itself, rather than an excerpt that repeats it.
    """
    if parsed.outcome is not None:
        return parsed.outcome.headline or split_headline(parsed.body)[0], ""
    return split_headline(parsed.body)


def question_context(body: str, question: str = "") -> str:
    """What the Tag found so far, without closing paragraphs that ask the question again."""
    def plain(text: str) -> str:
        return " ".join(re.sub(r"[^\w?]+", " ", text.casefold()).split())

    asked = plain(question)
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", body.strip()) if item.strip()]
    while paragraphs and (paragraphs[-1].rstrip("*_ ").endswith("?") or (asked and asked in plain(paragraphs[-1]))):
        paragraphs.pop()
    return "\n\n".join(paragraphs)


# Throttled edits ------------------------------------------------------------


class EditThrottle:
    """Send the latest edit at most once per ``interval``; intermediate edits are dropped.

    ``submit`` never blocks: inside the interval the newest payload waits for a
    timer. ``send_now`` waits out the rest of the interval, then sends at once,
    so a final edit (a question or the outcome) is never older than progress.
    """

    def __init__(self, send: Callable[[Any], None], *, interval: float | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 timer: Callable[[float, Callable[[], None]], Any] | None = None) -> None:
        self.send = send
        self.interval = EDIT_INTERVAL_SECONDS if interval is None else interval
        self.clock = clock
        self.sleep = sleep
        self.timer_factory = timer or self._thread_timer
        self.last_sent = float("-inf")
        self.pending: Any = None
        self.has_pending = False
        self.timer: Any = None
        self.closed = False
        self.lock = threading.RLock()

    @staticmethod
    def _thread_timer(delay: float, callback: Callable[[], None]) -> Any:
        timer = threading.Timer(delay, callback)
        timer.daemon = True
        timer.start()
        return timer

    def mark_sent(self) -> None:
        """Count a send made elsewhere (the first post) toward the interval."""
        with self.lock:
            self.last_sent = self.clock()

    def submit(self, payload: Any) -> None:
        with self.lock:
            if self.closed:
                return
            self.pending, self.has_pending = payload, True
            wait = self.last_sent + self.interval - self.clock()
            if wait <= 0:
                self._send_locked()
            elif self.timer is None:
                self.timer = self.timer_factory(wait, self._fire)

    def _fire(self) -> None:
        with self.lock:
            self.timer = None
            if self.closed or not self.has_pending:
                return
            wait = self.last_sent + self.interval - self.clock()
            if wait > 0:
                self.timer = self.timer_factory(wait, self._fire)
                return
            self._send_locked()

    def _send_locked(self) -> None:
        payload, self.pending, self.has_pending = self.pending, None, False
        self.last_sent = self.clock()
        self.send(payload)

    def send_now(self, payload: Any) -> None:
        with self.lock:
            if self.timer is not None and hasattr(self.timer, "cancel"):
                self.timer.cancel()
            self.timer = None
            wait = self.last_sent + self.interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            self.pending, self.has_pending = payload, True
            self._send_locked()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.pending, self.has_pending = None, False
            if self.timer is not None and hasattr(self.timer, "cancel"):
                self.timer.cancel()
            self.timer = None


# The live message -----------------------------------------------------------


class LiveMessage:
    """The Tag's one live message in a thread for one round of work."""

    def __init__(self, client: Any, channel: str, thread_ts: str, logger: Any, *, headline: str,
                 link: str | None = None, steps: int = 0, estimate: int | None = None,
                 throttle: EditThrottle | None = None) -> None:
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.logger = logger
        self.headline = headline
        self.link = link
        self.steps = steps
        self.estimate = estimate
        self.doing = ""
        self.ts: str | None = None
        self.lock = threading.RLock()
        self.throttle = throttle or EditThrottle(self._update)

    def _update(self, payload: tuple[str, list[dict[str, Any]]]) -> None:
        if self.ts is None:
            return
        text, blocks = payload
        try:
            self.client.chat_update(channel=self.channel, ts=self.ts, text=text, blocks=blocks)
        except Exception as exc:  # noqa: BLE001 - progress is optional; the outcome still lands
            self.logger.warning("Could not update the live Slack message: %s", exc)

    def progress(self) -> str:
        return progress_text(self.steps, self.estimate, self.doing)

    def render(self) -> tuple[str, list[dict[str, Any]]]:
        return f"Working: {self.headline}", live_blocks(self.headline, self.progress(), self.link)

    def post(self) -> str | None:
        """Post the live message; a failure leaves the round without one, never without an answer."""
        text, blocks = self.render()
        try:
            response = self.client.chat_postMessage(channel=self.channel, thread_ts=self.thread_ts,
                                                    text=text, blocks=blocks, unfurl_links=False)
            self.ts = response["ts"]
            self.throttle.mark_sent()
        except Exception as exc:  # noqa: BLE001 - the work continues without live progress
            self.logger.warning("Could not post the live Slack message: %s", exc)
        return self.ts

    def adopt(self, ts: str) -> None:
        """Keep editing an existing message instead of posting a new one."""
        self.ts = ts

    def activity(self, event_type: str, _activity_id: str, label: str, _wait_label: str = "") -> None:
        with self.lock:
            if event_type == "activity_start":
                self.steps += 1
                self.doing = doing_text(label)
            self.throttle.submit(self.render())

    def status(self, text: str) -> None:
        with self.lock:
            self.doing = doing_text(text)
            self.throttle.submit(self.render())

    def show(self, text: str, blocks: list[dict[str, Any]]) -> bool:
        """Replace the live message now (question or outcome); False when Slack refused."""
        if self.ts is None:
            return False
        result = {"ok": False}

        def send(payload: tuple[str, list[dict[str, Any]]]) -> None:
            try:
                self.client.chat_update(channel=self.channel, ts=self.ts, text=payload[0], blocks=payload[1])
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001 - the caller posts a new message instead
                self.logger.warning("Could not edit the live Slack message: %s", exc)

        with self.lock:
            original, self.throttle.send = self.throttle.send, send
            try:
                self.throttle.send_now((text, blocks))
            finally:
                self.throttle.send = original
            self.throttle.close()
        return result["ok"]

    def close(self) -> None:
        self.throttle.close()


# Thread state ---------------------------------------------------------------


def thread_key(team: str, channel: str, thread_ts: str) -> str:
    return f"{team}:{channel}:{thread_ts}"


@dataclass
class RoundState:
    team: str
    channel: str
    thread_ts: str
    requester: str
    headline: str
    status: str = "working"  # working, waiting, confirming, or done
    live_ts: str | None = None
    steps: int = 0
    rounds: int = 1
    started_at: float = field(default_factory=time.time)
    question: dict[str, Any] | None = None
    question_id: str | None = None
    asked_at: float | None = None
    nudged: bool = False
    estimate: int | None = None
    confirm: dict[str, Any] | None = None
    # People the owner let run tasks in this thread, and nowhere else.
    runners: list[str] = field(default_factory=list)
    # Owner prompts by id: a teammate's task or instruction waiting for the owner.
    prompts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Tasks the owner approved while this work was still open; they run after it.
    deferred: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    remembered: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return thread_key(self.team, self.channel, self.thread_ts)

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Any) -> "RoundState | None":
        if not isinstance(value, dict):
            return None
        try:
            state = cls(**{name: value[name] for name in cls.__dataclass_fields__ if name in value})
        except TypeError:
            return None
        if not all(isinstance(getattr(state, name), str) for name in ("team", "channel", "thread_ts", "requester")):
            return None
        if state.status not in {"working", "waiting", "confirming", "done"}:
            return None
        for name, kind in (("runners", list), ("prompts", dict), ("deferred", list), ("files", list),
                           ("remembered", list)):
            if not isinstance(getattr(state, name), kind):
                setattr(state, name, kind())
        return state


class RoundStore:
    """Bounded per-thread round state, written atomically."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or instance_home() / "state/slack-rounds.json"
        self.lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        threads = payload.get("threads") if isinstance(payload, dict) and payload.get("schema_version") == 1 else None
        return threads if isinstance(threads, dict) else {}

    def _write(self, threads: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        restrict_windows_acl(self.path.parent)
        cutoff = time.time() - RETENTION_SECONDS
        threads = {key: value for key, value in threads.items()
                   if isinstance(value, dict) and isinstance(value.get("updated_at"), (int, float))
                   and value["updated_at"] >= cutoff}
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"schema_version": 1, "threads": threads}, stream, separators=(",", ":"))
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, team: str, channel: str, thread_ts: str) -> RoundState | None:
        with self.lock:
            return RoundState.from_dict(self._read().get(thread_key(team, channel, thread_ts)))

    def save(self, state: RoundState) -> None:
        with self.lock:
            threads = self._read()
            state.updated_at = time.time()
            threads[state.key] = state.as_dict()
            self._write(threads)

    def update(self, team: str, channel: str, thread_ts: str, **changes: Any) -> RoundState | None:
        with self.lock:
            state = self.get(team, channel, thread_ts)
            if state is None:
                return None
            for name, value in changes.items():
                setattr(state, name, value)
            self.save(state)
            return state

    def all(self) -> list[RoundState]:
        with self.lock:
            return [state for value in self._read().values() if (state := RoundState.from_dict(value))]


def idle_hours() -> float:
    value = os.getenv("OPENTAG_THREAD_IDLE_HOURS", "").strip()
    try:
        return float(value) if value else 4.0
    except ValueError:
        return 4.0


def due_for_nudge(state: RoundState, now: float | None = None, hours: float | None = None) -> bool:
    """A question nobody answered is marked "Still waiting" once, after the idle hours."""
    hours = idle_hours() if hours is None else hours
    if state.status != "waiting" or state.nudged or not state.asked_at or hours <= 0:
        return False
    return (time.time() if now is None else now) - state.asked_at >= hours * 3600


def step_estimate(step_counts: Iterable[int]) -> int | None:
    """A rough step total from this Tag's recent finished runs, or None without history."""
    counts = [count for count in step_counts if isinstance(count, int) and count > 0]
    if len(counts) < 3:
        return None
    return max(2, int(round(statistics.median(counts))))


def link_for(channel: str, thread_ts: str) -> str:
    return thread_link(os.getenv("TAG_ID", "default"), channel, thread_ts)


# For other bridge features (Tag.app replies) ---------------------------------


def update_card(client: Any, team: str, channel: str, thread_ts: str, *, headline: str,
                results: Iterable[Result | dict[str, str]] | None = None, store: RoundStore | None = None,
                small: str | None = None) -> bool:
    """Edit the thread's live message into an outcome card; False when there is none."""
    state = (store or RoundStore()).get(team, channel, thread_ts)
    if state is None or not state.live_ts:
        return False
    items = [item if isinstance(item, Result) else Result(item.get("status", "warn") if item.get("status") in RESULT_MARKS else "warn",
                                                          _clip(str(item.get("text", "")), MAX_RESULT_CHARS))
             for item in results or []]
    if small is None:
        small = context_line(steps=state.steps, seconds=time.time() - state.started_at)
    blocks = outcome_blocks(headline=_clip(headline, MAX_HEADLINE_CHARS), results=items,
                            link=link_for(channel, thread_ts), small=small)
    client.chat_update(channel=channel, ts=state.live_ts, text=headline, blocks=blocks)
    return True
