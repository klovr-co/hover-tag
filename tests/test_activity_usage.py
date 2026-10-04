from pathlib import Path
from unittest.mock import patch
from scripts import opentag_agent
import tempfile
import unittest

from scripts.agent_activity import token_usage
from scripts.codex_agent_backend import CodexEventMapper
from scripts.claude_agent_backend import ClaudeEventMapper, message_payload
from scripts.tag_activity import ActivityStore, recent_activity
from tests.test_claude_agent_backend import ResultMessage


class UsageTests(unittest.TestCase):
    def test_thinking_level_is_saved_per_run_and_reported_effort_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            old = store.create(team='T', channel='C', thread_ts='1', request_ts='1', requester='U')
            self.assertNotIn('reasoning_effort', recent_activity(store.root)[0])
            for backend in ('codex', 'claude'):
                run = store.create(team='T', channel='C', thread_ts='1', request_ts='1', requester='U', reasoning_effort='medium')
                store.save_model(run, backend, 'model')
                self.assertEqual(store.get(run)['reasoning_effort'], 'medium')
                store.save_model(run, backend, 'model', reasoning_effort='high')
                store.finish(run, 'completed')
                item = next(r for r in recent_activity(store.root) if r['run_id'] == run)
                self.assertEqual(item['reasoning_effort'], 'high')
            self.assertNotIn('reasoning_effort', store.get(old))

    def test_codex_cumulative_usage_does_not_add_cache_or_reasoning_twice(self):
        event = CodexEventMapper().map({"method": "thread/tokenUsage/updated", "params": {
            "tokenUsage": {"total": {"inputTokens": 1000, "cachedInputTokens": 800,
                "outputTokens": 200, "reasoningOutputTokens": 100, "totalTokens": 1200},
                "last": {"inputTokens": 10, "outputTokens": 2}}}})[0]
        self.assertEqual(event['usage']['total_tokens'], 1200)
        self.assertEqual(event['usage']['cache_read_input_tokens'], 800)

    def test_claude_sdk_result_includes_cache_usage_once_even_on_failure(self):
        for failed in (False, True):
            mapper = ClaudeEventMapper()
            payload = message_payload(ResultMessage(is_error=failed, usage={
                'input_tokens': 100, 'output_tokens': 200,
                'cache_read_input_tokens': 800, 'cache_creation_input_tokens': 100}))
            events = mapper.map(payload)
            self.assertEqual(events[0]['type'], 'usage')
            self.assertEqual(events[0]['usage']['input_tokens'], 1000)
            self.assertEqual(events[0]['usage']['total_tokens'], 1200)
            self.assertEqual(mapper.map(payload), [])

    def test_retry_usage_accumulates_attempts_without_adding_duplicate_snapshots(self):
        attempt = 0
        def start(emit):
            nonlocal attempt
            attempt += 1
            usage = {"type": "usage", "usage": {"input_tokens": 100, "output_tokens": 10}}
            emit(usage)
            emit(usage)
            return ("failed", "rate limit") if attempt == 1 else ("completed", "")
        with patch.object(opentag_agent, "emit_event") as output, \
             patch.object(opentag_agent, "retryable_backend_failure", return_value=True), \
             patch.object(opentag_agent.time, "sleep"), \
             patch.dict(opentag_agent.os.environ, {"OPENTAG_BACKEND_ATTEMPTS": "2"}):
            self.assertEqual(0, opentag_agent.run_rich_events(start, errors=(RuntimeError,), backend_name="test"))
        totals = [call.kwargs["usage"]["total_tokens"] for call in output.call_args_list if call.args[0] == "usage"]
        self.assertEqual(totals, [110, 110, 220, 220])

    def test_invalid_and_missing_usage_remains_unknown(self):
        for value in (None, {}, {'input_tokens': -1, 'output_tokens': 10},
                      {'input_tokens': True, 'output_tokens': 10},
                      {'input_tokens': 100, 'output_tokens': '10'}):
            self.assertIsNone(token_usage(value))
        self.assertEqual([], CodexEventMapper().map({'method': 'thread/tokenUsage/updated', 'params': {}}))
        self.assertFalse(any(e['type'] == 'usage' for e in ClaudeEventMapper().map(
            {'kind': 'ResultMessage', 'result': 'done'})))

    def test_storage_replaces_snapshots_and_computes_historical_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory))
            run = store.create(team='T', channel='C', thread_ts='1', request_ts='1', requester='U')
            self.assertNotIn('usage', recent_activity(store.root)[0])
            self.assertNotIn('duration_seconds', recent_activity(store.root)[0])
            for output in (10, 20, 20):
                store.observe(run, {'type': 'usage', 'usage': {'input_tokens': 100, 'output_tokens': output}})
            store.observe(run, {'type': 'usage', 'usage': {'input_tokens': 'bad'}})
            store.finish(run, 'completed')
            record = store.get(run)
            record.update(started_at='2026-10-05T00:00:00+00:00', finished_at='2026-10-05T00:01:12+00:00')
            store._write(record)
            row = recent_activity(store.root)[0]
            self.assertEqual(row['duration_seconds'], 72)
            self.assertEqual(row['usage']['total_tokens'], 120)
            # Summary generation must not overwrite the original task's usage.
            store.observe(run, {'type': 'usage', 'usage': {'input_tokens': 2, 'output_tokens': 1}})
            self.assertEqual(recent_activity(store.root)[0]['usage']['total_tokens'], 120)
            del record['usage']
            store._write(record)
            self.assertNotIn('usage', recent_activity(store.root)[0])
            self.assertEqual(recent_activity(store.root)[0]['duration_seconds'], 72)
