"""Shared Slack request pacing and durable Retry-After deadlines for managed MFS."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from pathlib import Path

try:
    from .tag_config import save_config
except ImportError:
    from tag_config import save_config


def retry_seconds(headers) -> float:
    """Slack specifies seconds; malformed/missing values must not cause a tight loop."""
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    try:
        seconds = float(value)
        return max(1.0, seconds) if math.isfinite(seconds) and seconds >= 0 else 60.0
    except (TypeError, ValueError):
        return 60.0


def workspace_key(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()


def cooldown(state: Path, identity: str) -> float:
    """Read only this workspace's deadlines; no token, channel, or message data."""
    latest = 0.0
    for path in state.glob(f"{workspace_key(identity)}-*.json"):
        try:
            record = json.loads(path.read_text())
            deadline = float(record["retry_at"])
            if record.get("version") == 1 and math.isfinite(deadline):
                latest = max(latest, deadline)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return latest


class Gate:
    def __init__(self, state: Path, identity: str, method: str, *, clock=time.time, sleep=asyncio.sleep):
        self.lock = asyncio.Lock()
        self.clock, self.sleep = clock, sleep
        method_key = hashlib.sha256(method.encode()).hexdigest()
        self.path = state / f"{workspace_key(identity)}-{method_key}.json"
        self.retry_at = 0.0
        self.next_at = 0.0
        try:
            record = json.loads(self.path.read_text())
            deadline = float(record["retry_at"])
            if record.get("version") == 1 and math.isfinite(deadline):
                self.retry_at = deadline
        except FileNotFoundError:
            pass
        # Other storage failures fail closed: never silently discard a cooldown.

    async def call(self, request):
        async with self.lock:
            while True:
                delay = max(self.next_at, self.retry_at) - self.clock()
                if delay > 0:
                    await self.sleep(delay)
                    continue
                try:
                    result = await request()
                except Exception as error:
                    response = getattr(error, "response", None)
                    if getattr(response, "status_code", None) != 429:
                        raise
                    self.retry_at = self.clock() + retry_seconds(response.headers)
                    save_config(self.path, {"version": 1, "retry_at": self.retry_at})
                    # Retry this exact request/cursor. Do not restart read_records
                    # or let MFS's whole-object retry exhaust on a normal cooldown.
                    continue
                self.next_at = self.clock() + 1.0
                if self.retry_at:
                    save_config(self.path, {"version": 1, "retry_at": 0})
                    self.retry_at = 0.0
                return result


class Coordinator:
    def __init__(self, state: Path):
        self.state = state
        self.gates = {}

    async def call(self, identity: str, method: str, request):
        key = (identity, method)
        if key not in self.gates:
            self.gates[key] = Gate(self.state, identity, method)
        return await self.gates[key].call(request)
