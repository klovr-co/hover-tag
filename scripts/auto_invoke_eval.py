#!/usr/bin/env python3
"""Offline evaluation for the untagged-reply classifier.

``plan`` (the default) and ``report`` never use the network. ``plan`` estimates
the paid cost of scoring the fixtures with each provider. ``report`` reads
recorded scores and prints precision, recall and a threshold sweep per provider.
``score`` is the only paid command: it needs ``--confirm-paid`` and a
``--max-usd`` cap. It logs a running cost, stops before the cap, stops on any
authentication, quota or rate-limit error, and resumes from its output file.

    python3 scripts/auto_invoke_eval.py plan
    python3 scripts/auto_invoke_eval.py score --provider openai-decisions \\
        --out evals/auto_invoke/scores.jsonl --max-usd 0.50 --confirm-paid
    python3 scripts/auto_invoke_eval.py report evals/auto_invoke/scores.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

try:
    from scripts import auto_invoke
except ImportError:
    import auto_invoke


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/auto_invoke/conversations.json"
SWEEP = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.93, 0.95, 0.97, 0.99)
# Without the provider's tokenizer, assume about four characters per token.
CHARS_PER_TOKEN = 4
# Authentication, billing, quota and rate-limit responses stop a paid run.
STOP_STATUSES = {401, 402, 403, 429}
REQUESTER, TAG, BOT = "UREQUESTER", "UTAG", "UOTHERBOT"
MENTION_RE = re.compile(r"<@[^>]+>|\{(?:tag|requester|participant)\}")


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    expected: bool
    event: dict[str, Any]
    thread: list[dict[str, Any]]


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
        if not isinstance(item["expected"], bool):
            raise ValueError(f"{case_id}: expected must be true or false")
        if MENTION_RE.search(item["message"]):
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
        current_ts = f"{len(thread) + 1}.000"
        event = {"ts": current_ts, "user": REQUESTER, "text": item["message"]}
        if thread:
            event["thread_ts"] = thread[0]["ts"]
            thread.append(dict(event))
        cases.append(Case(case_id, item["category"], item["expected"], event, thread))
    return data.get("assistant_name", "Tag"), cases


def case_state(case: Case, assistant_name: str) -> dict[str, Any]:
    return auto_invoke.message_state(
        case.event, case.thread, assistant_name=assistant_name,
        assistant_user_id=TAG if case.thread else "",
    )


def estimated_tokens(provider: str, state: dict[str, Any]) -> int:
    spec = auto_invoke.PROVIDERS[provider]
    payload = spec.payload(state, spec.default_model)
    return -(-len(json.dumps(payload, ensure_ascii=False)) // CHARS_PER_TOKEN)


def cost_usd(provider: str, tokens: int) -> float:
    return tokens * auto_invoke.PROVIDERS[provider].usd_per_million_input / 1_000_000


def plan(cases: list[Case], assistant_name: str, out: TextIO) -> dict[str, dict[str, float]]:
    """Estimate one scoring run per provider without any network call."""
    totals: dict[str, dict[str, float]] = {}
    print(f"{len(cases)} cases. Estimates assume ~{CHARS_PER_TOKEN} characters per token "
          "and count the whole request body as input.", file=out)
    for provider, spec in auto_invoke.PROVIDERS.items():
        tokens = sum(estimated_tokens(provider, case_state(case, assistant_name)) for case in cases)
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
    scorer: Callable[..., auto_invoke.Score] = auto_invoke.score,
) -> float:
    """Score each case once, appending to ``out_path``; returns this run's spend."""
    done = {(row["provider"], row["id"]) for row in _recorded(out_path)}
    spent = last_call_usd = 0.0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as handle:
        for case in cases:
            if (provider, case.id) in done:
                continue
            state = case_state(case, assistant_name)
            # Assume the next call costs at least as much as the last one actually did.
            estimate = max(cost_usd(provider, estimated_tokens(provider, state)), last_call_usd)
            if spent + estimate > max_usd:
                raise BudgetStop(f"Stopped before ${max_usd:.2f}: spent ${spent:.4f}, next call ~${estimate:.4f}")
            row: dict[str, Any] = {"provider": provider, "id": case.id}
            try:
                answer = scorer(state, provider=provider, api_key=api_key, model=model, timeout=timeout)
            except auto_invoke.AutoInvokeRefusal:
                row["outcome"] = "refusal"
                spent += estimate
            except auto_invoke.AutoInvokeError as exc:
                cause = exc.__cause__
                if isinstance(cause, urllib.error.HTTPError) and cause.code in STOP_STATUSES:
                    raise BudgetStop(f"Stopped on HTTP {cause.code} from {provider}; fix the key, quota or "
                                     f"rate limit before retrying. Spent ${spent:.4f}") from None
                row.update(outcome="error", error=str(exc))
            else:
                tokens = answer.input_tokens
                call_usd = cost_usd(provider, tokens) if tokens is not None else estimate
                spent += call_usd
                last_call_usd = call_usd
                row.update(outcome="score", probability=answer.probability, model=answer.model,
                           input_tokens=tokens, usd=round(call_usd, 8))
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            print(f"{provider} {case.id}: {row['outcome']} "
                  f"{row.get('probability', '')} (running ${spent:.4f} / ${max_usd:.2f})", file=log)
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


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.3f}"


def report(cases: list[Case], rows: list[dict[str, Any]], out: TextIO) -> dict[str, list[Metrics]]:
    expected = {case.id: case.expected for case in cases}
    categories = {case.id: case.category for case in cases}
    results: dict[str, list[Metrics]] = {}
    for provider in sorted({row["provider"] for row in rows}):
        spec = auto_invoke.PROVIDERS.get(provider)
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
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("plan", help="Estimate cost per provider; no network.")
    score_parser = commands.add_parser("score", help="PAID: score fixtures with one provider.")
    score_parser.add_argument("--provider", required=True, choices=sorted(auto_invoke.PROVIDERS))
    score_parser.add_argument("--out", type=Path, required=True)
    score_parser.add_argument("--max-usd", type=float, required=True)
    score_parser.add_argument("--model", default="")
    score_parser.add_argument("--confirm-paid", action="store_true",
                              help="Required: acknowledge that this calls a paid API.")
    report_parser = commands.add_parser("report", help="Precision, recall and threshold sweep; no network.")
    report_parser.add_argument("scores", type=Path)
    args = parser.parse_args(argv)

    assistant_name, cases = load_cases(args.fixtures)
    if args.command in {None, "plan"}:
        plan(cases, assistant_name, sys.stdout)
        return 0
    if args.command == "report":
        report(cases, _recorded(args.scores), sys.stdout)
        return 0
    if not args.confirm_paid:
        parser.error("score calls a paid API; pass --confirm-paid after agreeing a budget")
    if not 0 < args.max_usd <= 5:
        parser.error("--max-usd must be greater than 0 and at most 5")
    key_setting = auto_invoke.PROVIDERS[args.provider].key_setting
    api_key = os.environ.get(key_setting, "").strip()
    if not api_key:
        parser.error(f"set {key_setting} to a spend-capped key")
    try:
        spent = score_run(cases, assistant_name, provider=args.provider, api_key=api_key,
                          out_path=args.out, max_usd=args.max_usd, model=args.model)
    except BudgetStop as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"Spent about ${spent:.4f} with {args.provider}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
