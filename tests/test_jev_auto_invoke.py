from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts import jev_auto_invoke
from scripts import tag_config


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self) -> bytes:
        return self.body


class JevAutoInvokeTests(unittest.TestCase):
    def test_enabled_configuration_requires_a_private_api_key(self) -> None:
        values = dict(tag_config.DEFAULTS, OPENTAG_JEV_AUTO_INVOKE="1")

        self.assertEqual(
            "Required when untagged Jev invocation is enabled",
            tag_config.config_errors(values)["OPENTAG_TYPESAFE_API_KEY"],
        )
        values["OPENTAG_TYPESAFE_API_KEY"] = "secret-key"
        self.assertNotIn("OPENTAG_TYPESAFE_API_KEY", tag_config.config_errors(values))

    def test_state_minimizes_identities_and_marks_tag_history(self) -> None:
        event = {
            "ts": "3",
            "user": "UOWNER",
            "text": "make that shorter for <@UOTHER>",
        }
        state = jev_auto_invoke.message_state(
            event,
            [
                {"ts": "1", "user": "UOWNER", "text": "ask <@UTAG> and <@UOWNER>"},
                {"ts": "2", "user": "UTAG", "bot_id": "BTAG", "text": "Long summary"},
                {"ts": "3", "user": "UOWNER", "text": "make that shorter for <@UOTHER>"},
            ],
            assistant_name="Tag",
            assistant_user_id="UTAG",
        )

        self.assertEqual("make that shorter for <participant>", state["current_message"])
        self.assertEqual(
            [
                {"author": "requester", "text": "ask <tag> and <requester>"},
                {"author": "tag", "text": "Long summary"},
            ],
            state["thread_history"],
        )
        self.assertNotIn("UOWNER", json.dumps(state))
        self.assertNotIn("UTAG", json.dumps(state))
        self.assertNotIn("UOTHER", json.dumps(state))

    def test_requires_addressed_probability_to_clear_threshold(self) -> None:
        response = {
            "model": "jev-1.13.0",
            "answers": {
                "tag_is_addressed": {"type": "noul", "noul": 0.82},
            },
        }
        with patch.object(
            jev_auto_invoke.urllib.request,
            "urlopen",
            return_value=FakeResponse(response),
        ) as urlopen:
            decision = jev_auto_invoke.evaluate(
                {"current_message": "Can we ship?"},
                api_key="secret-key",
                threshold=0.9,
            )

        self.assertFalse(decision.should_invoke)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual("jev-latest", payload["model"])
        self.assertEqual(
            {"tag_is_addressed"},
            set(payload["questions"]),
        )
        criteria = payload["questions"]["tag_is_addressed"]["criteria"]
        self.assertIn("you all", criteria["true"])
        self.assertIn("concrete task", criteria["true"])
        self.assertIn("chronological order", payload["questions"]["tag_is_addressed"]["instructions"])
        self.assertIn("several consecutive requester messages", criteria["true"])
        self.assertIn("immediately previous message", criteria["true"])
        self.assertIn("conversation has shifted", criteria["false"])
        self.assertEqual("Bearer secret-key", request.get_header("Authorization"))

    def test_rejects_malformed_provider_response(self) -> None:
        with patch.object(
            jev_auto_invoke.urllib.request,
            "urlopen",
            return_value=FakeResponse({"model": "jev-1.13.0", "answers": {}}),
        ):
            with self.assertRaises(jev_auto_invoke.JevEvaluationError):
                jev_auto_invoke.evaluate({}, api_key="secret-key")
