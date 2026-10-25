"""Private, cumulative usage snapshots and monthly advisory budgets (UTC)."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .tag_paths import instance_home
except ImportError:
    from tag_paths import instance_home


TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_tokens", "reasoning_output_tokens")


def count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def money(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if not isinstance(value, bool) and math.isfinite(number) and number >= 0 else None


def codex_event(params: dict[str, Any]) -> list[dict[str, Any]]:
    usage = params.get("tokenUsage")
    total = usage.get("total") if isinstance(usage, dict) else None
    if not isinstance(total, dict):
        return []
    event = {"type": "usage", "scope_id": params.get("threadId"), "input_tokens": count(total.get("inputTokens")),
             "output_tokens": count(total.get("outputTokens")), "cached_input_tokens": count(total.get("cachedInputTokens")),
             "reasoning_output_tokens": count(total.get("reasoningOutputTokens")), "cache_creation_tokens": count(total.get("cacheWriteInputTokens", 0))}
    return [event] if event["input_tokens"] is not None and event["output_tokens"] is not None else []


def claude_event(payload: dict[str, Any]) -> list[dict[str, Any]]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return []
    input_tokens, output_tokens = count(usage.get("input_tokens")), count(usage.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return []
    cached = count(usage.get("cache_read_input_tokens", 0))
    created = count(usage.get("cache_creation_input_tokens", 0))
    if cached is None or created is None:
        return []
    return [{"type": "usage", "scope_id": payload.get("session_id"),
             "input_tokens": input_tokens + cached + created, "output_tokens": output_tokens,
             "cached_input_tokens": cached, "cache_creation_tokens": created,
             "cost_usd": money(payload.get("total_cost_usd"))}]


def connection(home: Path | None = None) -> sqlite3.Connection:
    path = (home or instance_home()) / "state/usage.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Establish private permissions before SQLite opens a new database.
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    db = sqlite3.connect(path, timeout=10)
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1):
        db.close()
        raise ValueError("Usage ledger was created by a newer Tag; upgrade Tag before recording usage")
    # Additive schema v1. Existing Tags need no settings rewrite or sign-in.
    db.execute("CREATE TABLE IF NOT EXISTS usage (id TEXT PRIMARY KEY, month TEXT NOT NULL, backend TEXT NOT NULL, snapshot TEXT, status TEXT NOT NULL)")
    db.execute("PRAGMA user_version=1")
    db.commit()
    return db


def estimated_cost(backend: str, event: dict[str, Any]) -> float | None:
    if backend == "claude":
        return money(event.get("cost_usd"))
    prefix = f"OPENTAG_{backend.upper()}_"
    rates = [money(os.getenv(prefix + key)) for key in ("INPUT_USD_PER_MILLION", "OUTPUT_USD_PER_MILLION", "CACHED_INPUT_USD_PER_MILLION", "CACHE_WRITE_USD_PER_MILLION")]
    inp, out, cached = (event.get(key) for key in ("input_tokens", "output_tokens", "cached_input_tokens"))
    created = event.get("cache_creation_tokens", 0)
    if any(count(value) is None for value in (inp, out, cached, created)) or cached + created > inp:
        return None
    if rates[0] is None or rates[1] is None or (cached and rates[2] is None) or (created and rates[3] is None):
        return None
    return ((inp - cached - created) * rates[0] + out * rates[1] + cached * (rates[2] or 0) + created * (rates[3] or 0)) / 1_000_000


class Recorder:
    def __init__(self, backend: str, *, home: Path | None = None) -> None:
        self.backend = backend
        self.id = uuid.uuid4().hex
        self.month = datetime.now(timezone.utc).strftime("%Y-%m")
        self.home = home
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.save("running")

    def observe(self, event: dict[str, Any]) -> None:
        scope = event.get("scope_id")
        scope = scope if isinstance(scope, str) and scope else "task"
        snapshot = {key: count(event.get(key)) for key in TOKEN_FIELDS}
        snapshot["cost_usd"] = estimated_cost(self.backend, event)
        # Providers report cumulative totals; replace snapshots instead of adding
        # repeated notifications. Separate threads remain separate usage scopes.
        self.snapshots[scope] = snapshot
        self.save("running")

    def save(self, status: str) -> None:
        db = connection(self.home)
        try:
            with db:
                db.execute("INSERT INTO usage VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot, status=excluded.status",
                           (self.id, self.month, self.backend, json.dumps(list(self.snapshots.values())) if self.snapshots else None, status))
        finally:
            db.close()


def report(home: Path, values: dict[str, str]) -> dict[str, Any]:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    # Read-only status must not create a database or imply historical coverage.
    path = home / "state/usage.sqlite3"
    rows = []
    if path.exists():
        db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT backend, snapshot, status FROM usage WHERE month=?", (month,)).fetchall()
        finally:
            db.close()
    totals = {key: 0 for key in TOKEN_FIELDS}
    cost = 0.0
    missing_usage = missing_cost = incomplete = 0
    for _backend, raw, status in rows:
        incomplete += status == "running"
        if raw is None:
            missing_usage += 1
            missing_cost += 1
            continue
        snapshots = json.loads(raw)
        unknown = False
        for snapshot in snapshots:
            for key in TOKEN_FIELDS:
                totals[key] += snapshot.get(key) or 0
            amount = snapshot.get("cost_usd")
            unknown |= amount is None
            cost += amount or 0
        missing_cost += unknown
    budget = money(values.get("OPENTAG_MONTHLY_BUDGET_USD"))
    return {"schema_version": 1, "month_utc": month, "attempts": len(rows), **totals,
            "estimated_cost_usd": cost, "attempts_without_usage": missing_usage,
            "attempts_without_cost": missing_cost, "unfinished_attempts": incomplete,
            "monthly_budget_usd": budget, "recorded_cost_over_budget": budget is not None and cost >= budget,
            "enforcement": "advisory", "coverage": "Reported usage only; interrupted tasks may be incomplete. Costs are estimates, not invoices."}
