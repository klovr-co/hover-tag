#!/usr/bin/env python3
"""Offline evaluation and per-deployment tuning for the channel-watching classifier.

``plan`` (the default), ``export`` and ``report`` never use the network.
``plan`` estimates the paid cost of scoring the fixtures with each provider.
``export`` turns a Tag's own score log into labelled cases and recorded scores:
👎, "stop" and "Don't do this here" are negative labels; "Take this on" and 👍
are positive. ``report`` prints precision, recall and a threshold sweep per
provider, and recommends the lowest threshold that meets a precision target once
at least 200 messages are labelled. ``score`` is the only paid command: it needs
``--confirm-paid`` and a ``--max-usd`` cap. It logs a running cost, stops before
the cap, stops on any authentication, quota or rate-limit error, and resumes
from its output file.

    python3 scripts/watch_eval.py plan
    python3 scripts/watch_eval.py score --provider openai-decisions \\
        --out evals/channel_watch/scores.jsonl --max-usd 0.50 --confirm-paid
    python3 scripts/watch_eval.py report evals/channel_watch/scores.jsonl
    python3 scripts/watch_eval.py export ~/Tag/NAME/.tag/state/channel-watch-scores.jsonl \\
        --cases mine.json --scores mine-scores.jsonl
    python3 scripts/watch_eval.py --fixtures mine.json report mine-scores.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

try:
    from scripts import channel_watch, watch_classifier
except ImportError:
    import channel_watch
    import watch_classifier


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/channel_watch/conversations.json"
SWEEP = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.93, 0.95, 0.97, 0.99)
FINE_SWEEP = tuple(round(0.5 + step / 100, 2) for step in range(50))
MIN_LABELLED = 200
DEFAULT_TARGET_PRECISION = 0.95
# Without the provider's tokenizer, assume about four characters per token.
CHARS_PER_TOKEN = 4
# Authentication, billing, quota and rate-limit responses stop a paid run.
STOP_STATUSES = {401, 402, 403, 429}
# Quota and billing failures can also arrive with other statuses; their bodies say so.
QUOTA_RE = re.compile(rb"insufficient_quota|quota|budget|billing|credit|balance|payment", re.I)
MAX_CONSECUTIVE_ERRORS = 3
REQUESTER, TAG, BOT = "UREQUESTER", "UTAG", "UOTHERBOT"
MENTION_RE = re.compile(r"<@[^>]+>|\{(?:tag|requester|participant)\}")


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    expected: bool
    event: dict[str, Any]
    thread: list[dict[str, Any]]
    state: dict[str, Any] | None = None
    # Whether today's narrow scope sends it to the classifier: it names Tag, or
    # continues a thread Tag is in. Other cases wait for channel-wide watching.
    reachable: bool = True
    derived: str | None = None
    borderline: bool = False
    # Text descriptions of uploads and link previews, as the bridge gives the classifier.
    attachments: tuple[dict[str, Any], ...] = ()


def _participant_id(author: str) -> str:
    name = author.partition(":")[2] or "someone"
    return "UP" + re.sub(r"[^A-Z0-9]", "", name.upper())


def load_cases(path: Path = FIXTURES) -> tuple[str, list[Case]]:
    """Turn role-based fixtures into Slack-shaped events and thread messages."""
    data = json.loads(path.read_text(encoding="utf-8"))
    cases: list[Case] = []
    seen: set[str] = set()
    for item in data["cases"]:
        case_id = item["id"]
        if case_id in seen:
            raise ValueError(f"Duplicate case id {case_id}")
        seen.add(case_id)
        if item["expected"] is None:
            continue  # Exported but not labelled yet.
        if not isinstance(item["expected"], bool):
            raise ValueError(f"{case_id}: expected must be true, false or null")
        if isinstance(item.get("state"), dict):
            cases.append(Case(case_id, item.get("category", "deployment"), item["expected"], {}, [], item["state"],
                              reachable=item.get("reachable", True) is True, derived=item.get("derived"),
                              borderline=bool(item.get("borderline")) or item.get("confidence") == "low"))
            continue
        # Chime-in sets may mention people: such posts still get the chime-in question.
        chime_set = data.get("question") == watch_classifier.CHIME_QUESTION_NAME
        if MENTION_RE.search(item["message"]) and not chime_set:
            raise ValueError(f"{case_id}: a message with a member mention never reaches the classifier")
        thread: list[dict[str, Any]] = []
        for index, entry in enumerate(item.get("thread", [])):
            author = entry["author"]
            text = (entry["text"].replace("{tag}", f"<@{TAG}>")
                    .replace("{requester}", f"<@{REQUESTER}>")
                    .replace("{participant}", "<@UPSOMEONE>"))
            message: dict[str, Any] = {"ts": f"{index + 1}.000", "text": text}
            if author == "requester":
                message["user"] = REQUESTER
            elif author == "tag":
                message.update(user=TAG, bot_id="BTAG")
            elif author == "another_bot":
                message.update(user=BOT, bot_id="BOTHER")
            elif author.startswith("participant"):
                message["user"] = _participant_id(author)
            else:
                raise ValueError(f"{case_id}: unknown author {author!r}")
            thread.append(message)
        names = channel_watch.watch_names({"OPENTAG_BOT_NAME": data.get("assistant_name", "Tag")})
        # In chime-in channels every top-level post reaches the classifier.
        reachable = (chime_set and not thread) or channel_watch.names_tag(item["message"], names) or any(
            message.get("user") == TAG or f"<@{TAG}>" in message["text"] for message in thread)
        current_ts = f"{len(thread) + 1}.000"
        event = {"ts": current_ts, "user": REQUESTER, "text": item["message"]
                 .replace("{participant}", "<@UPSOMEONE>").replace("{requester}", f"<@{REQUESTER}>")}
        if thread:
            event["thread_ts"] = thread[0]["ts"]
            thread.append(dict(event))
        cases.append(Case(case_id, item["category"], item["expected"], event, thread, reachable=reachable,
                          borderline=bool(item.get("borderline")),
                          attachments=tuple(a for a in item.get("attachments", []) if isinstance(a, dict))))
    return data.get("assistant_name", "Tag"), cases


def case_state(case: Case, assistant_name: str) -> dict[str, Any]:
    if case.state is not None:
        return case.state
    state = watch_classifier.message_state(
        case.event, case.thread, assistant_name=assistant_name,
        assistant_user_id=TAG if case.thread else "",
    )
    if case.attachments:
        state["current_attachments"] = list(case.attachments)
    return state


def estimated_tokens(provider: str, state: dict[str, Any], variant: str = "",
                     question: str = watch_classifier.QUESTION_NAME) -> int:
    spec = watch_classifier.PROVIDERS[provider]
    if variant:
        payload = _variant(variant, provider)[0](state, spec.default_model)
    else:
        payload = spec.payload(state, spec.default_model, (watch_classifier.QUESTIONS[question],))
    return -(-len(json.dumps(payload, ensure_ascii=False)) // CHARS_PER_TOKEN)


def cost_usd(provider: str, tokens: int) -> float:
    return tokens * watch_classifier.PROVIDERS[provider].usd_per_million_input / 1_000_000


def _variant(variant: str, provider: str):
    try:
        from scripts import watch_prompt_variants
    except ImportError:
        import watch_prompt_variants
    return watch_prompt_variants.get(variant, provider)


def plan(cases: list[Case], assistant_name: str, out: TextIO,
         question: str = watch_classifier.QUESTION_NAME) -> dict[str, dict[str, float]]:
    """Estimate one scoring run per provider without any network call."""
    totals: dict[str, dict[str, float]] = {}
    print(f"{len(cases)} cases. Estimates assume ~{CHARS_PER_TOKEN} characters per token "
          "and count the whole request body as input.", file=out)
    for provider, spec in watch_classifier.PROVIDERS.items():
        tokens = sum(estimated_tokens(provider, case_state(case, assistant_name), question=question)
                     for case in cases)
        usd = cost_usd(provider, tokens)
        totals[provider] = {"calls": len(cases), "tokens": tokens, "usd": usd}
        print(f"  {provider}: {len(cases)} calls × ~{tokens // len(cases):,} input tokens "
              f"= ~{tokens:,} tokens × ${spec.usd_per_million_input}/1M = ~${usd:.4f} "
              f"(price: {spec.price_source})", file=out)
    return totals


def _recorded(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class BudgetStop(RuntimeError):
    """The run stopped before exceeding the budget or after a quota/auth failure."""


MAX_WORKERS = 4  # The shared machine's limit for parallel API calls.


def score_run(
    cases: list[Case],
    assistant_name: str,
    *,
    provider: str,
    api_key: str,
    out_path: Path,
    max_usd: float,
    model: str = "",
    timeout: float = 20.0,
    log: TextIO = sys.stderr,
    scorer: Callable[..., watch_classifier.Score] = watch_classifier.score,
    variant: str = "",
    question: str = watch_classifier.QUESTION_NAME,
    workers: int = 1,
) -> float:
    """Score each case once for one question, appending to ``out_path``; returns this run's spend.

    Up to ``workers`` calls run at once. Before each call its cost is reserved: the larger of its
    estimate and the largest call so far. Nothing is dispatched once spent plus reserved would pass
    ``max_usd``. A quota, auth, budget or rate-limit error stops dispatching at once; calls already
    in flight are still recorded.
    """
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers must be 1 to {MAX_WORKERS}")
    if variant and question != watch_classifier.QUESTION_NAME:
        raise ValueError("prompt variants exist only for the addressed question")
    extra: dict[str, Any] = {} if question == watch_classifier.QUESTION_NAME else {"question": question}
    done = {(row["provider"], row["id"]) for row in _recorded(out_path)
            if row.get("question", watch_classifier.QUESTION_NAME) == question}
    hooks = dict(zip(("payload", "parse"), _variant(variant, provider))) if variant else {}
    if extra:
        hooks["questions"] = (watch_classifier.QUESTIONS[question],)
    pending = [case for case in cases if (provider, case.id) not in done]
    spent = reserved = largest = 0.0
    errors = 0
    stop: BudgetStop | None = None
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def call(case: Case) -> Any:
        try:
            return scorer(case_state(case, assistant_name), provider=provider, api_key=api_key, model=model,
                          timeout=timeout, **hooks)
        except watch_classifier.ClassifierError as exc:
            return exc

    with out_path.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(max_workers=workers) as pool:
        running: dict[Future, tuple[Case, float]] = {}
        while (pending and stop is None) or running:
            while pending and stop is None and len(running) < workers:
                case = pending[0]
                estimate = max(cost_usd(provider, estimated_tokens(
                    provider, case_state(case, assistant_name), variant, question)), largest)
                if spent + reserved + estimate > max_usd:
                    stop = BudgetStop(f"Stopped before ${max_usd:.2f}: spent ${spent:.4f}, "
                                      f"next call ~${estimate:.4f}")
                    break
                pending.pop(0)
                reserved += estimate
                running[pool.submit(call, case)] = (case, estimate)
            if not running:
                break
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                case, estimate = running.pop(future)
                reserved -= estimate
                answer = future.result()
                row: dict[str, Any] = {"provider": provider, "id": case.id, **extra,
                                       **({"variant": variant} if variant else {})}
                if isinstance(answer, watch_classifier.ClassifierRefusal):
                    row["outcome"] = "refusal"
                    spent += estimate
                elif isinstance(answer, watch_classifier.ClassifierError):
                    cause = answer.__cause__
                    if isinstance(cause, urllib.error.HTTPError):
                        # Classify the body without printing it: some providers echo part of the key.
                        try:
                            body = cause.read(4096) or b""
                        except Exception:  # noqa: BLE001 - an unreadable body still has a status
                            body = b""
                        if cause.code in STOP_STATUSES or QUOTA_RE.search(body):
                            stop = stop or BudgetStop(
                                f"Stopped on HTTP {cause.code} from {provider} (auth, quota, budget or "
                                f"rate limit); not retrying. Spent ${spent:.4f}")
                            continue
                    errors += 1
                    row.update(outcome="error", error=str(answer))
                    if errors >= MAX_CONSECUTIVE_ERRORS:
                        stop = stop or BudgetStop(f"Stopped after {errors} errors in a row from {provider}: "
                                                  f"{answer}. Spent ${spent:.4f}")
                else:
                    errors = 0
                    tokens = answer.input_tokens
                    call_usd = cost_usd(provider, tokens) if tokens is not None else estimate
                    spent += call_usd
                    largest = max(largest, call_usd)
                    row.update(outcome="score", probability=answer.probability, model=answer.model,
                               input_tokens=tokens, usd=round(call_usd, 8),
                               **({"cached_tokens": answer.cached_tokens} if answer.cached_tokens is not None else {}))
                handle.write(json.dumps(row) + "\n")
                handle.flush()
                state = case_state(case, assistant_name)
                usage = (f" usage {row['input_tokens']} tokens vs "
                         f"~{estimated_tokens(provider, state, variant, question)} estimated"
                         + (f" ({row['cached_tokens']} cached)" if row.get("cached_tokens") is not None else "")
                         if row.get("input_tokens") is not None else
                         " (no usage reported; counted the estimate)" if row["outcome"] == "score" else "")
                print(f"{provider} {case.id}: {row['outcome']} {row.get('probability', '')}{usage} "
                      f"(running ${spent:.6f} / ${max_usd:.2f})", file=log)
    if stop is not None:
        raise stop
    return spent


@dataclass(frozen=True)
class Metrics:
    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p and r else None


def metrics(expected: dict[str, bool], rows: Iterable[dict[str, Any]], threshold: float) -> Metrics:
    """Refusals, errors and missing scores count as "stay silent", the bridge's behavior."""
    probabilities = {row["id"]: row.get("probability") for row in rows if row.get("outcome") == "score"}
    tp = fp = fn = tn = 0
    for case_id, positive in expected.items():
        probability = probabilities.get(case_id)
        invoked = probability is not None and probability >= threshold
        if invoked and positive:
            tp += 1
        elif invoked:
            fp += 1
        elif positive:
            fn += 1
        else:
            tn += 1
    return Metrics(threshold, tp, fp, fn, tn)


def export_log(log_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Turn a Tag's score log into cases (label null when nobody gave feedback) and scores."""
    scores: dict[str, dict[str, Any]] = {}
    labels: dict[str, dict[str, Any]] = {}
    for row in _recorded(log_path):
        if row.get("type") == "score" and isinstance(row.get("state"), dict):
            scores[row["id"]] = row
        elif row.get("type") == "label" and isinstance(row.get("expected"), bool):
            labels[row["id"]] = row  # The latest feedback wins.
    cases, rows = [], []
    for entry_id, row in scores.items():
        label = labels.get(entry_id)
        cases.append({"id": entry_id, "category": f"deployment:{row.get('trigger', 'unknown')}",
                      "expected": label["expected"] if label else None,
                      "note": f"label from {label['source']}" if label else "unlabelled: set expected by hand",
                      "outcome": row.get("outcome"), "state": row["state"]})
        if isinstance(row.get("probability"), (int, float)) and row.get("provider"):
            rows.append({"provider": row["provider"], "id": entry_id, "outcome": "score",
                         "probability": row["probability"], "model": row.get("model")})
    assistant = next((row["state"].get("assistant", {}).get("name") for row in scores.values()), None)
    fixture = {"schema_version": 1, "assistant_name": assistant or "Tag",
               "description": f"Exported from {log_path.name}. Label every null case before tuning.",
               "cases": cases}
    return fixture, rows


PSEUDONYMS = ("Alex", "Sam", "Priya", "Jordan", "Mei", "Omar", "Lena", "Tomas", "Ana", "Kofi", "Ravi", "Noor",
              "Ines", "Hugo", "Yara", "Dmitri", "Leila", "Mateo", "Aiko", "Bram", "Zara", "Felix", "Nadia", "Oskar",
              "Rhea", "Soren", "Talia", "Viktor", "Wren", "Ezra", "Lucia", "Idris", "Maren", "Cyrus", "Freya",
              "Juno", "Kiran", "Elif", "Rafa", "Sunniva")
DERIVATIONS = ("tag-mention-to-name", "tag-mention-removed", "person-mention-to-name", "stand-in")
SOURCES = ("A-tag-channels", "B-sandbox", "E-stand-in")
CONFIDENCE = ("high", "medium", "low")
_LINK_RE = re.compile(r"<(?:https?://|www\.)[^>]*>|https?://\S+|www\.\S+", re.I)
_MAILTO_RE = re.compile(r"<mailto:[^>]*>", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d ()-]{6,}\d(?!\w|\.\d)")
_MEMBER_ID_RE = re.compile(r"\b[UWB][A-Z0-9]{8,}\b")
_CHANNEL_REF_RE = re.compile(r"<#[CG][A-Z0-9]+\|([^>]*)>")


def scrub(text: str) -> str:
    """Strip contact details and leftover IDs after identity minimization."""
    text = _MAILTO_RE.sub("<email>", text)
    text = _LINK_RE.sub("<link>", text)
    text = _EMAIL_RE.sub("<email>", text)
    text = _PHONE_RE.sub("<phone>", text)
    text = _CHANNEL_REF_RE.sub(lambda m: "#" + (m.group(1) or "channel"), text)
    return _MEMBER_ID_RE.sub("<member>", text)


class Pseudonyms:
    """Names unique within one case, so two people never share a pseudonym."""

    def __init__(self, seed: str) -> None:
        self.names = sorted(PSEUDONYMS, key=lambda name: hashlib.sha256(f"{seed}|{name}".encode()).hexdigest())
        self.mapping: dict[str, str] = {}

    def __call__(self, key: str) -> str:
        key = key.lower()
        if key not in self.mapping:
            index = len(self.mapping)
            self.mapping[key] = self.names[index] if index < len(self.names) else f"Person{index + 1}"
        return self.mapping[key]


def _derive(text: str, how: str | None, assistant_id: str, assistant_name: str, pseudonym=None) -> str:
    pseudonym = pseudonym or Pseudonyms("")
    mention = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
    if how in {"tag-mention-to-name", "tag-mention-removed"}:
        replacement = assistant_name if how == "tag-mention-to-name" else ""
        text = mention.sub(lambda m: replacement if m.group(1) == assistant_id else m.group(0), text)
    if how == "person-mention-to-name" or how in {"tag-mention-to-name", "tag-mention-removed"}:
        # Without mentions, people write names; use stable pseudonyms rather than real ones.
        text = mention.sub(lambda m: m.group(0) if m.group(1) == assistant_id else pseudonym(m.group(1)), text)
    return re.sub(r"\s+", " ", text).strip(" ,")


def real_case(item: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    """One minimized, scrubbed, labelled case from a real Slack message. Raises ValueError."""
    channel = item.get("channel")
    if channel not in allowed:
        raise ValueError("channel is not on the allowed list")
    assistant = item.get("assistant") or {}
    assistant_id, assistant_name = assistant.get("user_id", ""), assistant.get("name", "")
    requester, ts, how = item.get("requester", ""), item.get("ts", ""), item.get("derived")
    if not (assistant_id and assistant_name and requester and ts):
        raise ValueError("assistant, requester and ts are required")
    if how is not None and how not in DERIVATIONS:
        raise ValueError(f"derived must be one of {DERIVATIONS}")
    if not isinstance(item.get("expected"), bool) or item.get("confidence") not in CONFIDENCE:
        raise ValueError("expected (bool) and confidence (high, medium, low) are required")
    source = item.get("source", "A-tag-channels")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    raw_texts = [str(item.get("message", ""))] + [str(e.get("text", "")) for e in item.get("history", [])]
    pseudonyms = Pseudonyms(f"{channel}|{ts}")
    if (how == "stand-in") != (source == "E-stand-in"):
        raise ValueError("stand-in cases use derived 'stand-in' and source 'E-stand-in'")
    if how == "stand-in":
        # A person plays the Tag: their plain-text name becomes the Tag's, other names become pseudonyms.
        swap = _name_swapper(item.get("names") or {}, assistant_id, assistant_name, pseudonyms)
        item = {**item, "message": swap(item.get("message", "")),
                "history": [{**e, "text": swap(e.get("text", ""))} for e in item.get("history", [])]}
        if requester == assistant_id:
            raise ValueError("the stand-in can't be the requester")
    message = _derive(str(item.get("message", "")), how, assistant_id, assistant_name, pseudonyms)
    if not message or "<@" in message:
        raise ValueError("the message is empty or still mentions a member, so watching would never classify it")
    bots = set(item.get("bots", [])) | ({assistant_id} if how != "stand-in" else set())
    history = []
    for entry in item.get("history", [])[-watch_classifier.MAX_CONTEXT_MESSAGES:]:
        user = entry.get("user", "")
        history.append({"user": user, "text": str(entry.get("text", "")), "ts": "h",
                        **({"bot_id": "B"} if user in bots and user != assistant_id else {})})
    state = watch_classifier.message_state(
        {"user": requester, "text": message, "ts": "current"}, history,
        assistant_name=assistant_name, assistant_user_id=assistant_id)
    state["current_message"] = scrub(state["current_message"])
    for entry in state["thread_history"]:
        entry["text"] = scrub(entry["text"])
    names = channel_watch.watch_names({"OPENTAG_BOT_NAME": assistant_name})
    reachable = channel_watch.names_tag(message, names) or any(e["author"] == "tag" for e in state["thread_history"])
    try:
        from scripts import watch_real_data as cleaning
    except ImportError:
        import watch_real_data as cleaning
    return {
        "id": "r-" + hashlib.sha256(f"{channel}|{ts}|{how}".encode()).hexdigest()[:12],
        "group": hashlib.sha256(f"{channel}|{ts}".encode()).hexdigest()[:12],
        "source": source, "category": str(item.get("category", "real")), "expected": item["expected"],
        "confidence": item["confidence"], "borderline": bool(item.get("borderline")),
        "derived": how, "channel": channel, "reachable": reachable,
        "reason": scrub(str(item.get("reason", "")))[:200], "state": state,
        # Judged on the raw text before scrubbing; only the verdicts are stored.
        "current_raw_noise": cleaning.is_noise(raw_texts[0]), "current_test": cleaning.is_test(raw_texts[0]),
        "history_test": any(cleaning.is_test(text) for text in raw_texts[1:]),
        "flags": cleaning.flags(raw_texts[0]),
    }


def _name_swapper(names: dict[str, list[str]], standin_id: str, assistant_name: str, pseudonym=None):
    """Replace real first names (and variants) given by the labeller; longest first."""
    pseudonym = pseudonym or Pseudonyms("")
    pairs = []
    for user, variants in names.items():
        target = assistant_name if user == standin_id else pseudonym(user)
        pairs += [(variant, target) for variant in variants if isinstance(variant, str) and variant.strip()]
    pairs.sort(key=lambda pair: -len(pair[0]))

    def swap(text: str) -> str:
        text = str(text)
        for variant, target in pairs:
            text = re.sub(rf"(?<![\w]){re.escape(variant.strip())}(?![\w])", target, text, flags=re.I)
        return text

    return swap


def import_real(items: list[dict[str, Any]], out: Path, allowed: frozenset[str]) -> tuple[int, list[str]]:
    """Add cases to a private fixture file (dir 700, file 600), replacing same-ID cases."""
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.parent.chmod(0o700)
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {
        "schema_version": 1, "assistant_name": "Tag",
        "description": "Real, minimized Slack cases. Private: never commit or share.", "cases": []}
    by_id = {case["id"]: case for case in data["cases"]}
    errors = []
    for index, item in enumerate(items):
        try:
            case = real_case(item, allowed)
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append(f"item {index}: {exc}")
            continue
        by_id[case["id"]] = case
    data["cases"] = list(by_id.values())
    descriptor = os.open(out.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    os.replace(out.with_suffix(".tmp"), out)
    out.chmod(0o600)
    return len(items) - len(errors), errors


def recommend(expected: dict[str, bool], rows: list[dict[str, Any]], target: float) -> Metrics | None:
    """The lowest threshold meeting the precision target, which keeps the most recall."""
    for threshold in FINE_SWEEP:
        item = metrics(expected, rows, threshold)
        if item.precision is not None and item.precision >= target:
            return item
    return None


FULL_SWEEP = tuple(round(step / 100, 2) for step in range(100))
INCLUSIVE_RE = re.compile(r"\b(?:anyone|anybody|any one|someone|somebody|everyone|everybody|you all|y'all|"
                          r"all of you|team|folks|guys)\b", re.I)


def rule_name(state: dict[str, Any]) -> bool:
    """R1: the message names the Tag as a word, outside code."""
    names = channel_watch.watch_names({"OPENTAG_BOT_NAME": state.get("assistant", {}).get("name", "Tag")})
    return channel_watch.names_tag(state.get("current_message", ""), names)


def rule_continuation(state: dict[str, Any]) -> bool:
    """R2: Tag wrote the latest earlier message other than the requester's own run, and the requester spoke before it."""
    history = state.get("thread_history", [])
    position = len(history) - 1
    while position >= 0 and history[position]["author"] == "requester":
        position -= 1
    return (position >= 0 and history[position]["author"] == "tag"
            and any(entry["author"] == "requester" for entry in history[:position]))


def classifier_only_type(state: dict[str, Any]) -> str:
    """Why a positive needed the classifier: an inclusive question, implicit address in a Tag thread, or other."""
    if INCLUSIVE_RE.search(state.get("current_message", "")):
        return "inclusive"
    if any(entry["author"] == "tag" for entry in state.get("thread_history", [])):
        return "implicit address"
    return "other"


def rules_table(cases: list[Case], assistant_name: str, scores: list[dict[str, Any]],
                thresholds: tuple[float, ...] = (0.9, 0.95)) -> dict[str, Any]:
    """Precision/recall for R1, R2, R1 or R2, the classifier, and rules OR classifier. Counts only."""
    probability = {row["id"]: row.get("probability") for row in scores if row.get("outcome") == "score"}
    states = {case.id: case_state(case, assistant_name) for case in cases}
    expected = {case.id: case.expected for case in cases}

    def evaluate(fires: dict[str, bool]) -> dict[str, Any]:
        tp = sum(fires[i] and expected[i] for i in expected)
        fp = sum(fires[i] and not expected[i] for i in expected)
        fn = sum(not fires[i] and expected[i] for i in expected)
        return {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
                "tp": tp, "fp": fp, "fn": fn}

    r1 = {i: rule_name(states[i]) for i in expected}
    r2 = {i: rule_continuation(states[i]) for i in expected}
    rules = {i: r1[i] or r2[i] for i in expected}
    table: dict[str, Any] = {"cases": len(expected), "positives": sum(expected.values()),
                             "scored": sum(probability.get(i) is not None for i in expected),
                             "R1 name": evaluate(r1), "R2 continuation": evaluate(r2), "R1 or R2": evaluate(rules)}
    for threshold in thresholds:
        clf = {i: probability.get(i) is not None and probability[i] >= threshold for i in expected}
        table[f"classifier @{threshold}"] = evaluate(clf)
        table[f"rules or classifier @{threshold}"] = evaluate({i: rules[i] or clf[i] for i in expected})
        only = [i for i in expected if expected[i] and clf[i] and not rules[i]]
        table[f"classifier-only true positives @{threshold}"] = dict(Counter(classifier_only_type(states[i]) for i in only))
        table[f"classifier-only false positives @{threshold}"] = sum(
            1 for i in expected if not expected[i] and clf[i] and not rules[i])
    return table


def operating_points(expected: dict[str, bool], rows: list[dict[str, Any]],
                     min_recall: float = 0.5, min_precision: float = 0.95) -> dict[str, Any]:
    """Precision at recall >= min_recall, and recall at precision >= min_precision (with its threshold)."""
    sweep = [metrics(expected, rows, threshold) for threshold in FULL_SWEEP]
    by_precision = [m for m in sweep if m.recall is not None and m.recall >= min_recall and m.precision is not None]
    by_recall = [m for m in sweep if m.precision is not None and m.precision >= min_precision]
    best_p = max(by_precision, key=lambda m: (m.precision, m.recall), default=None)
    best_r = max(by_recall, key=lambda m: (m.recall, -m.threshold), default=None)
    return {
        "precision_at_recall": best_p.precision if best_p else None,
        "precision_threshold": best_p.threshold if best_p else None,
        "recall_at_precision": best_r.recall if best_r else 0.0,
        "best_threshold": best_r.threshold if best_r else None,
        "scored": sum(r.get("outcome") == "score" for r in rows if r["id"] in expected),
    }


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.3f}"


def report(cases: list[Case], rows: list[dict[str, Any]], out: TextIO, *,
           target: float = DEFAULT_TARGET_PRECISION) -> dict[str, list[Metrics]]:
    expected = {case.id: case.expected for case in cases}
    print(f"{len(expected)} labelled messages ({sum(expected.values())} should get a reply).", file=out)
    categories = {case.id: case.category for case in cases}
    results: dict[str, list[Metrics]] = {}
    for provider in sorted({row["provider"] for row in rows}):
        spec = watch_classifier.PROVIDERS.get(provider)
        provider_rows = [row for row in rows if row["provider"] == provider and row["id"] in expected]
        outcomes = {name: sum(row.get("outcome") == name for row in provider_rows)
                    for name in ("score", "refusal", "error")}
        missing = len(expected) - len({row["id"] for row in provider_rows})
        print(f"\n{provider}: {outcomes['score']} scored, {outcomes['refusal']} refused, "
              f"{outcomes['error']} errors, {missing} not scored (all count as silent)", file=out)
        print("  threshold  precision  recall     f1    tp  fp  fn  tn", file=out)
        thresholds = sorted({*SWEEP, *( [spec.default_threshold] if spec else [])})
        sweep = [metrics(expected, provider_rows, threshold) for threshold in thresholds]
        results[provider] = sweep
        for item in sweep:
            marker = " *" if spec and item.threshold == spec.default_threshold else ""
            print(f"  {item.threshold:9.2f}  {_fmt(item.precision):>9}  {_fmt(item.recall):>6}  "
                  f"{_fmt(item.f1):>5}  {item.tp:3} {item.fp:3} {item.fn:3} {item.tn:3}{marker}", file=out)
        positives = [row.get("probability") for row in provider_rows
                     if row.get("outcome") == "score" and expected[row["id"]]]
        negatives = [row.get("probability") for row in provider_rows
                     if row.get("outcome") == "score" and not expected[row["id"]]]
        print("  score distribution     " + " ".join(f"{low:.1f}+" for low in [i / 10 for i in range(10)]), file=out)
        for label, values in (("should reply", positives), ("should not", negatives)):
            buckets = [sum(min(int(v * 10), 9) == i for v in values) for i in range(10)]
            print(f"  {label:<21}" + " ".join(f"{count:4}" for count in buckets), file=out)
        if spec:
            threshold = spec.default_threshold
            scored = {row["id"]: row.get("probability") for row in provider_rows if row.get("outcome") == "score"}
            wrong = [case_id for case_id, positive in expected.items()
                     if (scored.get(case_id) is not None and scored[case_id] >= threshold) != positive]
            print(f"  * default threshold {threshold}. Wrong at default:", file=out)
            for case_id in wrong:
                print(f"    {case_id} [{categories[case_id]}] expected={expected[case_id]} "
                      f"probability={scored.get(case_id)}", file=out)
            if not wrong:
                print("    none", file=out)
        if len(expected) < MIN_LABELLED:
            print(f"  Not enough to tune: {len(expected)} labelled messages; label at least "
                  f"{MIN_LABELLED} from this deployment before changing the threshold.", file=out)
        elif best := recommend(expected, provider_rows, target):
            print(f"  Recommended threshold for precision ≥ {target}: {best.threshold:.2f} "
                  f"(precision {_fmt(best.precision)}, recall {_fmt(best.recall)})", file=out)
        else:
            print(f"  No threshold reaches precision {target}; keep watching off or improve the labels.", file=out)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument("--scope", choices=("current", "all"), default="current",
                        help="current: only cases today's watching sends to the classifier (default)")
    parser.add_argument("--natural-only", action="store_true", help="Leave out cases derived from mentions.")
    parser.add_argument("--derived-only", action="store_true", help="Only cases derived from mentions.")
    parser.add_argument("--confident-only", action="store_true", help="Leave out borderline and low-confidence labels.")
    commands = parser.add_subparsers(dest="command")
    plan_parser = commands.add_parser("plan", help="Estimate cost per provider; no network.")
    plan_parser.add_argument("--question", default=watch_classifier.QUESTION_NAME,
                             choices=sorted(watch_classifier.QUESTIONS))
    score_parser = commands.add_parser("score", help="PAID: score fixtures with one provider.")
    score_parser.add_argument("--provider", required=True, choices=sorted(watch_classifier.PROVIDERS))
    score_parser.add_argument("--out", type=Path, required=True)
    score_parser.add_argument("--max-usd", type=float, required=True)
    score_parser.add_argument("--model", default="")
    score_parser.add_argument("--key-file", type=Path, default=os.environ.get("TAG_EVAL_KEY_FILE") or None,
                              help="Owner-only file holding the provider key (or TAG_EVAL_KEY_FILE).")
    score_parser.add_argument("--limit", type=int, default=0, help="Score at most this many new cases.")
    score_parser.add_argument("--cases", default="", help="Comma-separated case IDs to score.")
    score_parser.add_argument("--variant", default="", help="Prompt variant from watch_prompt_variants.")
    score_parser.add_argument("--question", default=watch_classifier.QUESTION_NAME,
                              choices=sorted(watch_classifier.QUESTIONS),
                              help="Which classifier question to score; the cases' labels must be for it.")
    score_parser.add_argument("--workers", type=int, default=MAX_WORKERS, choices=range(1, MAX_WORKERS + 1),
                              help=f"Parallel calls, at most {MAX_WORKERS}.")
    score_parser.add_argument("--confirm-paid", action="store_true",
                              help="Required: acknowledge that this calls a paid API.")
    report_parser = commands.add_parser("report", help="Precision, recall and threshold sweep; no network.")
    report_parser.add_argument("scores", type=Path)
    report_parser.add_argument("--target-precision", type=float, default=DEFAULT_TARGET_PRECISION)
    rules_parser = commands.add_parser("rules", help="Deterministic rules alone and with the classifier; no network.")
    rules_parser.add_argument("scores", type=Path)
    rules_parser.add_argument("--provider", default="openai-decisions")
    compare_parser = commands.add_parser("compare", help="Operating points per prompt variant; no network.")
    compare_parser.add_argument("scores", nargs="+", help="name=path pairs of score files.")
    export_parser = commands.add_parser("export", help="Labelled cases and scores from a Tag's score log; no network.")
    export_parser.add_argument("log", type=Path)
    export_parser.add_argument("--cases", type=Path, required=True)
    export_parser.add_argument("--scores", type=Path, required=True)
    import_parser = commands.add_parser("import-real", help="Add real Slack cases from stdin JSON; no network.")
    import_parser.add_argument("--out", type=Path, required=True)
    import_parser.add_argument("--allowed", type=Path, required=True, help="File with one allowed channel ID per line.")
    merge_parser = commands.add_parser("merge", help="Merge fixture files into one; no network.")
    merge_parser.add_argument("--out", type=Path, required=True)
    merge_parser.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args(argv)

    if args.command == "import-real":
        allowed = frozenset(line.strip() for line in args.allowed.read_text(encoding="utf-8").splitlines() if line.strip())
        items = json.load(sys.stdin)
        added, errors = import_real(items if isinstance(items, list) else [items], args.out, allowed)
        print(f"Imported {added} case(s); {len(errors)} rejected.", file=sys.stderr)
        for error in errors:
            print("  " + error, file=sys.stderr)
        return 0 if not errors else 1
    if args.command == "merge":
        merged: dict[str, Any] = {}
        for path in args.inputs:
            for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
                merged[case["id"]] = case
        allowed = frozenset(case["channel"] for case in merged.values())
        out = {"schema_version": 1, "assistant_name": "Tag",
               "description": "Real, minimized Slack cases. Private: never commit or share.",
               "cases": list(merged.values())}
        args.out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(out, handle, ensure_ascii=False, indent=1)
        print(f"Merged {len(merged)} cases from {len(allowed)} channel(s).", file=sys.stderr)
        return 0
    if args.command == "export":
        fixture, rows = export_log(args.log)
        for path in (args.cases, args.scores):
            path.parent.mkdir(parents=True, exist_ok=True)
        args.cases.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        args.scores.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        labelled = sum(case["expected"] is not None for case in fixture["cases"])
        print(f"Exported {len(fixture['cases'])} messages, {labelled} labelled by feedback, "
              f"{len(rows)} with scores.", file=sys.stderr)
        return 0
    assistant_name, cases = load_cases(args.fixtures)
    if args.scope == "current":
        cases = [case for case in cases if case.reachable]
    if args.natural_only:
        cases = [case for case in cases if case.derived is None]
    if args.derived_only:
        cases = [case for case in cases if case.derived is not None]
    if args.confident_only:
        cases = [case for case in cases if not case.borderline]
    if args.command in {None, "plan"}:
        plan(cases, assistant_name, sys.stdout, getattr(args, "question", watch_classifier.QUESTION_NAME))
        return 0
    if args.command == "rules":
        rows = [row for row in _recorded(args.scores) if row["provider"] == args.provider]
        print(json.dumps(rules_table(cases, assistant_name, rows), indent=1))
        return 0
    if args.command == "compare":
        expected = {case.id: case.expected for case in cases}
        print(f"{len(expected)} cases ({sum(expected.values())} should reply). P@R>=0.5 | R@P>=0.95 @ threshold")
        for pair in args.scores:
            name, _, path = pair.partition("=")
            rows = [row for row in _recorded(Path(path)) if row["id"] in expected]
            for provider in sorted({row["provider"] for row in rows}):
                point = operating_points(expected, [r for r in rows if r["provider"] == provider])
                cost = sum(r.get("usd", 0) for r in rows if r["provider"] == provider)
                print(f"  {name:14} {provider:17} n={point['scored']:4}  P@R.5 {_fmt(point['precision_at_recall'])}"
                      f"  R@P.95 {_fmt(point['recall_at_precision'])} @ {point['best_threshold']}  ${cost:.4f}")
        return 0
    if args.command == "report":
        report(cases, _recorded(args.scores), sys.stdout, target=args.target_precision)
        return 0
    if not args.confirm_paid:
        parser.error("score calls a paid API; pass --confirm-paid after agreeing a budget")
    if not 0 < args.max_usd <= 5:
        parser.error("--max-usd must be greater than 0 and at most 5")
    key_setting = watch_classifier.PROVIDERS[args.provider].key_setting
    if args.key_file:
        try:
            if os.name != "nt" and args.key_file.stat().st_mode & 0o077:  # POSIX permission bits only.
                parser.error(f"{args.key_file} must be readable only by its owner (chmod 600)")
            api_key = args.key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            parser.error(f"could not read the key file: {type(exc).__name__}")
    else:
        api_key = os.environ.get(key_setting, "").strip()
    if not api_key:
        parser.error(f"pass --key-file with a spend-capped key, or set {key_setting}")
    if args.cases:
        wanted = {item.strip() for item in args.cases.split(",") if item.strip()}
        unknown = wanted - {case.id for case in cases}
        if unknown:
            parser.error("unknown case IDs: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case.id in wanted]
    if args.limit:
        done = {row["id"] for row in _recorded(args.out) if row.get("provider") == args.provider
                and row.get("question", watch_classifier.QUESTION_NAME) == args.question}
        cases = [case for case in cases if case.id not in done][:args.limit]
    try:
        spent = score_run(cases, assistant_name, provider=args.provider, api_key=api_key,
                          out_path=args.out, max_usd=args.max_usd, model=args.model, variant=args.variant,
                          question=args.question, workers=args.workers)
    except BudgetStop as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"Spent about ${spent:.4f} with {args.provider}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
