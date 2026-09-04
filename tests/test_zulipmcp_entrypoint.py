from __future__ import annotations

import types
import unittest

from scripts import zulipmcp_entrypoint


class _Process:
    def __init__(self, return_code: int | None) -> None:
        self.return_code = return_code

    def poll(self) -> int | None:
        return self.return_code


class ZulipMcpEntrypointTests(unittest.TestCase):
    def test_session_limit_counts_only_live_processes(self) -> None:
        listener = types.SimpleNamespace(
            _sessions={
                ("engineering", "one"): _Process(None),
                ("engineering", "done"): _Process(0),
            }
        )

        self.assertEqual(zulipmcp_entrypoint.active_session_count(listener), 1)

    def test_limited_spawn_rejects_new_topic_at_capacity(self) -> None:
        spawned: list[dict[str, object]] = []
        rejected: list[dict[str, object]] = []
        listener = types.SimpleNamespace(
            _sessions={("engineering", "one"): _Process(None)},
            _spawn=lambda config, message: spawned.append(message),
        )
        original = zulipmcp_entrypoint.install_session_limit(
            listener,
            max_sessions=1,
            on_capacity=lambda config, message, limit: rejected.append(message),
        )

        listener._spawn(object(), {"display_recipient": "engineering", "subject": "two"})

        self.assertEqual(spawned, [])
        self.assertEqual(len(rejected), 1)
        self.assertIs(original, listener._opentag_original_spawn)

    def test_limited_spawn_allows_existing_topic_to_reenter(self) -> None:
        spawned: list[dict[str, object]] = []
        listener = types.SimpleNamespace(
            _sessions={("engineering", "one"): _Process(None)},
            _spawn=lambda config, message: spawned.append(message),
        )
        zulipmcp_entrypoint.install_session_limit(
            listener,
            max_sessions=1,
            on_capacity=lambda config, message, limit: self.fail("existing topic was rejected"),
        )

        message = {"display_recipient": "Engineering", "subject": "One"}
        listener._spawn(object(), message)

        self.assertEqual(spawned, [message])


if __name__ == "__main__":
    unittest.main()
