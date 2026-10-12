from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import patch

from scripts import watch_classifier
from scripts import tag_config


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self) -> bytes:
        return self.body


def respond(payload: object):
    return patch.object(watch_classifier.urllib.request, "urlopen", return_value=FakeResponse(payload))


def predicate(probability: object, *, name: str = "tag_is_addressed") -> dict:
    return {"answers": [{"type": "predicate", "name": name, "probability": probability}]}


class ConfigurationTests(unittest.TestCase):
    def test_disabled_by_default_with_typesafe_as_the_default_provider(self) -> None:
        self.assertEqual("", tag_config.DEFAULTS["OPENTAG_WATCH_CHANNEL_IDS"])
        self.assertEqual("typesafe-jev", tag_config.DEFAULTS["OPENTAG_WATCH_CLASSIFIER"])
        self.assertEqual({}, {
            key: error for key, error in tag_config.config_errors(dict(tag_config.DEFAULTS)).items()
            if "WATCH" in key or "TYPESAFE" in key or "DECISIONS" in key or "JEV" in key
        })

    def test_enabled_configuration_requires_the_selected_providers_private_key(self) -> None:
        values = dict(tag_config.DEFAULTS, OPENTAG_WATCH_CHANNEL_IDS="C123")
        self.assertIn("TypeSafe API key is required", tag_config.config_errors(values)["OPENTAG_TYPESAFE_API_KEY"])
        values["OPENTAG_TYPESAFE_API_KEY"] = "secret-key"
        self.assertNotIn("OPENTAG_TYPESAFE_API_KEY", tag_config.config_errors(values))

        values["OPENTAG_WATCH_CLASSIFIER"] = "openai-decisions"
        errors = tag_config.config_errors(values)
        self.assertIn("OpenAI Decisions API key is required", errors["OPENTAG_OPENAI_DECISIONS_API_KEY"])
        values["OPENTAG_OPENAI_DECISIONS_API_KEY"] = "sk-decisions"
        self.assertNotIn("OPENTAG_OPENAI_DECISIONS_API_KEY", tag_config.config_errors(values))

    def test_keys_are_editable_but_never_shown(self) -> None:
        for key in ("OPENTAG_TYPESAFE_API_KEY", "OPENTAG_OPENAI_DECISIONS_API_KEY"):
            self.assertIn(key, tag_config.EDITABLE)
            self.assertNotIn(key, tag_config.PUBLIC)
            self.assertEqual({key: "[set]"}, tag_config.public_config({key: "secret"}))
            self.assertEqual("API keys must not contain whitespace", tag_config.validation_error(key, "a b"))

    def test_watching_settings_are_validated(self) -> None:
        error = tag_config.validation_error
        for key, good, bad in (
            ("OPENTAG_WATCH_CHANNEL_IDS", ("", "C123,G456"), ("D123", "C1, nope")),
            ("OPENTAG_WATCH_NAMES", ("", "Iris's Tag, Tagbot"), ("Tag,,Bot", "x" * 36)),
            ("OPENTAG_WATCH_QUIET_HOURS", ("", "19:00-08:00", "09:30-17:45"), ("7pm-8am", "24:00-08:00")),
            ("OPENTAG_WATCH_DAILY_LIMIT", ("1", "50"), ("0", "51", "five")),
            ("OPENTAG_WATCH_COOLDOWN_HOURS", ("1", "168"), ("0", "169", "1.5")),
        ):
            for value in good:
                self.assertIsNone(error(key, value), (key, value))
            for value in bad:
                self.assertIsNotNone(error(key, value), (key, value))

    def test_each_provider_validates_its_own_model_and_threshold(self) -> None:
        error = tag_config.validation_error
        self.assertIsNone(error("OPENTAG_WATCH_CLASSIFIER", "openai-decisions"))
        self.assertIsNotNone(error("OPENTAG_WATCH_CLASSIFIER", "openai"))
        self.assertIsNone(error("OPENTAG_JEV_MODEL", "jev-1.13.0"))
        self.assertIsNotNone(error("OPENTAG_JEV_MODEL", "gpt-6-luna"))
        self.assertIsNone(error("OPENTAG_DECISIONS_MODEL", "gpt-6-luna"))
        self.assertIsNotNone(error("OPENTAG_DECISIONS_MODEL", "jev-latest"))
        for key in ("OPENTAG_JEV_THRESHOLD", "OPENTAG_DECISIONS_THRESHOLD"):
            self.assertIsNone(error(key, "0.75"))
            for bad in ("0.4", "1.1", "nan", "high"):
                self.assertIsNotNone(error(key, bad), (key, bad))
        self.assertIsNone(error("OPENTAG_WATCH_CLASSIFIER_TIMEOUT_SECONDS", "2.5"))
        for bad in ("0", "31", "inf", "soon"):
            self.assertIsNotNone(error("OPENTAG_WATCH_CLASSIFIER_TIMEOUT_SECONDS", bad))

    def test_settings_follow_the_selected_provider(self) -> None:
        environment = {
            "OPENTAG_WATCH_CLASSIFIER": "openai-decisions",
            "OPENTAG_TYPESAFE_API_KEY": "typesafe-secret",
            "OPENTAG_JEV_THRESHOLD": "0.95",
            "OPENTAG_OPENAI_DECISIONS_API_KEY": "openai-secret",
            "OPENTAG_DECISIONS_THRESHOLD": "0.7",
            "OPENTAG_WATCH_CLASSIFIER_TIMEOUT_SECONDS": "3",
        }
        settings = watch_classifier.settings_from(environment)
        self.assertEqual(
            watch_classifier.Settings("openai-decisions", "openai-secret", "gpt-6-luna", 0.7, 3.0), settings
        )
        settings = watch_classifier.settings_from({**environment, "OPENTAG_WATCH_CLASSIFIER": ""})
        self.assertEqual(
            watch_classifier.Settings("typesafe-jev", "typesafe-secret", "jev-latest", 0.95, 3.0), settings
        )

    def test_settings_fail_closed_without_a_key_or_with_bad_values(self) -> None:
        for environment in (
            {},
            {"OPENTAG_WATCH_CLASSIFIER": "openai-decisions", "OPENTAG_TYPESAFE_API_KEY": "wrong-provider"},
            {"OPENTAG_WATCH_CLASSIFIER": "elsewhere", "OPENTAG_TYPESAFE_API_KEY": "key"},
            {"OPENTAG_TYPESAFE_API_KEY": "key", "OPENTAG_JEV_THRESHOLD": "0.2"},
            {"OPENTAG_TYPESAFE_API_KEY": "key", "OPENTAG_WATCH_CLASSIFIER_TIMEOUT_SECONDS": "never"},
        ):
            with self.subTest(environment=environment), self.assertRaises(watch_classifier.ClassifierError):
                watch_classifier.settings_from(environment)


class MessageStateTests(unittest.TestCase):
    def test_state_minimizes_identities_and_marks_tag_history(self) -> None:
        event = {
            "ts": "3",
            "user": "UOWNER",
            "text": "make that shorter for <@UOTHER>",
        }
        state = watch_classifier.message_state(
            event,
            [
                {"ts": "1", "user": "UOWNER", "text": "ask <@UTAG> and <@UOWNER>"},
                {"ts": "2", "user": "UTAG", "bot_id": "BTAG", "text": "Long summary"},
                {"ts": "2.5", "user": "UBOT", "bot_id": "BOTHER", "text": "Deploy finished"},
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
                {"author": "another_bot", "text": "Deploy finished"},
            ],
            state["thread_history"],
        )
        for member_id in ("UOWNER", "UTAG", "UOTHER", "UBOT"):
            self.assertNotIn(member_id, json.dumps(state))

    def test_history_and_messages_are_bounded(self) -> None:
        history = [{"ts": str(index), "user": "UOWNER", "text": "x" * 5000} for index in range(30)]
        state = watch_classifier.message_state(
            {"ts": "99", "user": "UOWNER", "text": "y" * 5000}, history, assistant_name="Tag"
        )
        self.assertEqual(watch_classifier.MAX_CONTEXT_MESSAGES, len(state["thread_history"]))
        self.assertEqual(watch_classifier.MAX_MESSAGE_CHARS, len(state["current_message"]))
        self.assertTrue(all(len(item["text"]) == watch_classifier.MAX_MESSAGE_CHARS for item in state["thread_history"]))


class TypeSafeJevTests(unittest.TestCase):
    def test_requires_addressed_probability_to_clear_threshold(self) -> None:
        response = {
            "model": "jev-1.13.0",
            "answers": {"tag_is_addressed": {"type": "noul", "noul": 0.82}},
            "usage": {"input_tokens": 812, "output_tokens": 20},
        }
        with respond(response) as urlopen:
            decision = watch_classifier.evaluate(
                {"current_message": "Can we ship?"},
                provider="typesafe-jev",
                api_key="secret-key",
                threshold=0.9,
            )

        self.assertEqual(
            watch_classifier.ClassifierDecision(False, 0.82, "jev-1.13.0", "typesafe-jev"), decision
        )
        request = urlopen.call_args.args[0]
        self.assertEqual("https://api.typesafe.ai/v1/systemone", request.full_url)
        payload = json.loads(request.data)
        self.assertEqual("jev-latest", payload["model"])
        self.assertEqual({"current_message": "Can we ship?"}, payload["state"])
        self.assertEqual({"tag_is_addressed"}, set(payload["questions"]))
        question = payload["questions"]["tag_is_addressed"]
        self.assertEqual("noul", question["type"])
        criteria = question["criteria"]
        self.assertIn("you all", criteria["true"])
        self.assertIn("concrete task", criteria["true"])
        self.assertIn("chronological order", question["instructions"])
        self.assertIn("several consecutive requester messages", criteria["true"])
        self.assertIn("immediately previous message", criteria["true"])
        self.assertIn("conversation has shifted", criteria["false"])
        self.assertEqual("Bearer secret-key", request.get_header("Authorization"))

        with respond(response):
            self.assertTrue(watch_classifier.evaluate({}, provider="typesafe-jev", api_key="k", threshold=0.8).should_invoke)
        with respond(response):
            self.assertEqual(812, watch_classifier.score({}, provider="typesafe-jev", api_key="k").input_tokens)

    def test_rejects_malformed_provider_responses(self) -> None:
        for response in (
            {"model": "jev-1.13.0", "answers": {}},
            {"answers": {"tag_is_addressed": {"noul": 0.95}}},
            {"model": "", "answers": {"tag_is_addressed": {"noul": 0.95}}},
            {"model": "jev-1.13.0", "answers": {"tag_is_addressed": {"noul": True}}},
            {"model": "jev-1.13.0", "answers": {"tag_is_addressed": {"noul": 1.4}}},
            {"model": "jev-1.13.0", "answers": {"tag_is_addressed": {"noul": "0.95"}}},
            [],
        ):
            with self.subTest(response=response), respond(response):
                with self.assertRaises(watch_classifier.ClassifierError):
                    watch_classifier.evaluate({}, provider="typesafe-jev", api_key="secret-key", threshold=0.5)


class OpenAIDecisionsTests(unittest.TestCase):
    def test_asks_one_predicate_with_the_same_criteria_and_serialized_state(self) -> None:
        state = watch_classifier.message_state(
            {"ts": "2", "user": "UOWNER", "text": "and more casual"},
            [{"ts": "1", "user": "UTAG", "text": "Here's a draft"}],
            assistant_name="Iris's Tag",
            assistant_user_id="UTAG",
        )
        with respond(predicate(0.93)) as urlopen:
            decision = watch_classifier.evaluate(
                state, provider="openai-decisions", api_key="sk-decisions", threshold=0.9, timeout=2
            )

        self.assertEqual(
            watch_classifier.ClassifierDecision(True, 0.93, "gpt-6-luna", "openai-decisions"), decision
        )
        request = urlopen.call_args.args[0]
        self.assertEqual(2, urlopen.call_args.kwargs["timeout"])
        self.assertEqual("https://api.openai.com/v1/decisions", request.full_url)
        self.assertEqual("Bearer sk-decisions", request.get_header("Authorization"))
        payload = json.loads(request.data)
        self.assertEqual({"model", "input", "questions"}, set(payload))
        self.assertEqual("gpt-6-luna", payload["model"])
        [question] = payload["questions"]
        self.assertEqual({"type", "name", "instructions"}, set(question))
        self.assertEqual(("predicate", "tag_is_addressed"), (question["type"], question["name"]))
        for text in (watch_classifier.INSTRUCTIONS, watch_classifier.TRUE_CRITERIA, watch_classifier.FALSE_CRITERIA):
            self.assertIn(text, question["instructions"])
        self.assertIn("Answer true when:", question["instructions"])
        self.assertIn("Answer false when:", question["instructions"])
        self.assertTrue(payload["input"].startswith(watch_classifier.DECISIONS_INPUT_PREFIX))
        self.assertEqual(state, json.loads(payload["input"][len(watch_classifier.DECISIONS_INPUT_PREFIX):]))
        self.assertNotIn("UOWNER", payload["input"])

    def test_threshold_is_the_providers_own(self) -> None:
        with respond(predicate(0.71)):
            self.assertTrue(watch_classifier.evaluate({}, provider="openai-decisions", api_key="k", threshold=0.7).should_invoke)
        with respond(predicate(0.69)):
            self.assertFalse(watch_classifier.evaluate({}, provider="openai-decisions", api_key="k", threshold=0.7).should_invoke)

    def test_reports_response_model_and_usage_when_present(self) -> None:
        response = predicate(0.5) | {"model": "gpt-6-luna-2026-09-30", "usage": {"input_tokens": 640}}
        with respond(response):
            answer = watch_classifier.score({}, provider="openai-decisions", api_key="k")
        self.assertEqual(watch_classifier.Score(0.5, "gpt-6-luna-2026-09-30", 640), answer)

    def test_refusal_fails_closed(self) -> None:
        with respond({"answers": [{"type": "refusal", "name": "tag_is_addressed"}]}):
            with self.assertRaises(watch_classifier.ClassifierRefusal):
                watch_classifier.evaluate({}, provider="openai-decisions", api_key="k", threshold=0.5)

    def test_rejects_malformed_provider_responses(self) -> None:
        for response in (
            {},
            {"answers": {}},
            {"answers": []},
            predicate(0.95, name="something_else"),
            {"answers": [predicate(0.95)["answers"][0]] * 2},
            {"answers": [{"type": "choice", "name": "tag_is_addressed", "choice": "yes"}]},
            predicate(None),
            predicate(True),
            predicate(-0.1),
            predicate("0.9"),
        ):
            with self.subTest(response=response), respond(response):
                with self.assertRaises(watch_classifier.ClassifierError):
                    watch_classifier.evaluate({}, provider="openai-decisions", api_key="k", threshold=0.5)


class FailureTests(unittest.TestCase):
    def test_transport_and_http_errors_fail_closed_for_both_providers(self) -> None:
        failures = (
            TimeoutError(),
            urllib.error.URLError("offline"),
            urllib.error.HTTPError("https://example.test", 429, "Too Many Requests", None, None),
            ValueError("not json"),
        )
        for provider in watch_classifier.PROVIDERS:
            for failure in failures:
                with self.subTest(provider=provider, failure=failure), patch.object(
                    watch_classifier.urllib.request, "urlopen", side_effect=failure
                ):
                    with self.assertRaises(watch_classifier.ClassifierError) as raised:
                        watch_classifier.evaluate({}, provider=provider, api_key="k", threshold=0.5)
                    self.assertNotIn("k", str(raised.exception).split(":")[-1].strip())

    def test_missing_key_or_unknown_provider_never_calls_the_network(self) -> None:
        with patch.object(watch_classifier.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(watch_classifier.ClassifierError):
                watch_classifier.evaluate({}, provider="openai-decisions", api_key="", threshold=0.5)
            with self.assertRaises(watch_classifier.ClassifierError):
                watch_classifier.evaluate({}, provider="elsewhere", api_key="k", threshold=0.5)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
