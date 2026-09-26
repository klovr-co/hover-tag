import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from scripts import tag_slack_backoff as backoff


class Limited(Exception):
    def __init__(self, seconds="30", status=429):
        self.response = SimpleNamespace(status_code=status, headers={"Retry-After": [seconds]})


class Clock:
    def __init__(self):
        self.now = 100.0
        self.delays = []

    def time(self):
        return self.now

    async def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


class BackoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.clock = Clock()
        self.gate = backoff.Gate(self.state, "TONE", "conversations.history",
                                 clock=self.clock.time, sleep=self.clock.sleep)

    async def test_retry_same_page_then_pace_next_request(self):
        request = AsyncMock(side_effect=[Limited("45"), {"page": 2}])
        self.assertEqual(await self.gate.call(request), {"page": 2})
        await self.gate.call(AsyncMock(return_value={"page": 3}))
        self.assertEqual(request.await_count, 2)
        self.assertEqual(self.clock.delays, [45, 1])
        self.assertEqual(backoff.cooldown(self.state, "TONE"), 0)

    async def test_interrupted_cooldown_survives_restart(self):
        async def cancel(delay):
            raise asyncio.CancelledError
        self.gate.sleep = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.gate.call(AsyncMock(side_effect=Limited("80")))
        restarted = backoff.Gate(self.state, "TONE", "conversations.history",
                                  clock=self.clock.time, sleep=self.clock.sleep)
        await restarted.call(AsyncMock(return_value="ok"))
        self.assertEqual(self.clock.delays, [80])
        self.assertEqual(backoff.cooldown(self.state, "TOTHER"), 0)

    async def test_competing_channels_share_cooldown(self):
        order = []
        first = True
        async def request(channel):
            nonlocal first
            order.append((channel, self.clock.now))
            if first:
                first = False
                raise Limited("20")
            return channel
        result = await asyncio.gather(
            self.gate.call(lambda: request("one")),
            self.gate.call(lambda: request("two")),
        )
        self.assertEqual(result, ["one", "two"])
        self.assertEqual(order, [("one", 100), ("one", 120), ("two", 121)])

    async def test_non_rate_errors_fail_without_retry(self):
        request = AsyncMock(side_effect=Limited(status=403))
        with self.assertRaises(Limited):
            await self.gate.call(request)
        request.assert_awaited_once()
        self.assertEqual(self.clock.delays, [])

    async def test_separate_methods_and_workspaces_have_separate_gates(self):
        coordinator = backoff.Coordinator(self.state)
        for team, method in (("T1", "list"), ("T1", "history"), ("T2", "list")):
            await coordinator.call(team, method, AsyncMock(return_value="ok"))
        self.assertEqual(len(coordinator.gates), 3)

    def test_malformed_headers_have_safe_fallback_and_valid_deadlines_are_not_capped(self):
        for value in ("broken", "", "nan", "-2", "inf"):
            self.assertEqual(backoff.retry_seconds({"Retry-After": value}), 60)
        self.assertEqual(backoff.retry_seconds({"retry-after": ["3600"]}), 3600)
        self.assertEqual(backoff.retry_seconds({}), 60)
        self.assertEqual(backoff.retry_seconds({"Retry-After": "0"}), 1)

    async def test_repeated_rate_limits_do_not_exhaust_object_retries(self):
        request = AsyncMock(side_effect=[*[Limited("2") for _ in range(6)], "ok"])
        self.assertEqual(await self.gate.call(request), "ok")
        self.assertEqual(self.clock.delays, [2] * 6)
        saved = json.loads(self.gate.path.read_text())
        self.assertEqual(set(saved), {"version", "retry_at"})
