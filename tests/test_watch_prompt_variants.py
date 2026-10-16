from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import watch_classifier as wc, watch_eval, watch_prompt_variants as variants


class Response:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self) -> bytes:
        return self.body


STATE = {"assistant": {"name": "Tag"}, "current_message": "which one is oldest?", "thread_history": []}


def call(variant: str, provider: str, response: dict):
    payload, parse = variants.get(variant, provider)
    with patch.object(wc.urllib.request, "urlopen", return_value=Response(response)) as urlopen:
        score = wc.score(STATE, provider=provider, api_key="k", payload=payload, parse=parse)
    return score, json.loads(urlopen.call_args.args[0].data)


class VariantTests(unittest.TestCase):
    def test_every_variant_puts_fixed_text_before_the_message_state(self) -> None:
        for name, providers in variants.VARIANTS.items():
            for provider in providers:
                if name == "baseline" and provider == "typesafe-jev":
                    continue  # Production payload, unchanged.
                body = list(variants.get(name, provider)[0](STATE, "m"))
                last = "input" if provider == "openai-decisions" else "state"
                with self.subTest(variant=name, provider=provider):
                    self.assertEqual(last, body[-1])
                    self.assertLess(body.index("questions"), body.index(last))

    def test_decisions_variants_record_cached_tokens_without_a_cache_key(self) -> None:
        # The Decisions API rejects prompt_cache_key (HTTP 400 unknown_parameter); it caches automatically.
        answer = {"answers": [{"type": "predicate", "name": "tag_is_addressed", "probability": 0.8}],
                  "usage": {"input_tokens": 900, "input_tokens_details": {"cached_tokens": 640}}}
        score, body = call("short", "openai-decisions", answer)
        self.assertNotIn("prompt_cache_key", body)
        self.assertEqual((0.8, 900, 640), (score.probability, score.input_tokens, score.cached_tokens))
        self.assertIn("True when:\n- ", body["questions"][0]["instructions"])

    def test_two_questions_score_is_the_minimum_and_refusal_fails_closed(self) -> None:
        answers = [{"type": "predicate", "name": "addressed", "probability": 0.95},
                   {"type": "predicate", "name": "welcome", "probability": 0.4}]
        score, body = call("two-questions", "openai-decisions", {"answers": answers})
        self.assertEqual(0.4, score.probability)
        self.assertEqual(["addressed", "welcome"], [q["name"] for q in body["questions"]])
        jev = {"model": "jev-1.13.0", "answers": {"addressed": {"noul": 0.3}, "welcome": {"noul": 0.9}}}
        self.assertEqual(0.3, call("two-questions", "typesafe-jev", jev)[0].probability)
        with self.assertRaises(wc.ClassifierRefusal):
            call("two-questions", "openai-decisions",
                 {"answers": [answers[0], {"type": "refusal", "name": "welcome"}]})
        with self.assertRaises(wc.ClassifierError):
            call("two-questions", "typesafe-jev", {"model": "jev-1.13.0", "answers": {"addressed": {"noul": 0.3}}})

    def test_jev_bullets_send_criteria_as_lists(self) -> None:
        _, body = call("jev-bullets", "typesafe-jev", {"model": "jev-1.13.0", "answers": {"tag_is_addressed": {"noul": 0.5}}})
        criteria = body["questions"]["tag_is_addressed"]["criteria"]
        self.assertTrue(isinstance(criteria["true"], list) and len(criteria["true"]) >= 6)
        self.assertTrue(all(item.endswith(".") and item[0].isupper() for item in criteria["true"] + criteria["false"]))
        with self.assertRaises(ValueError):
            variants.get("jev-bullets", "openai-decisions")

    def test_few_shot_examples_are_synthetic_and_not_copied_from_tuning_cases(self) -> None:
        _, cases = watch_eval.load_cases(Path("evals/channel_watch/synthetic-hard.json"))
        _, fixtures = watch_eval.load_cases()
        for case in cases + fixtures:
            self.assertNotIn(f"\"{case.event['text'].lower()}\"", variants.FEW_SHOT.lower())
        self.assertEqual(6, variants.FEW_SHOT.count("Answer:"))

    def test_production_scoring_is_unchanged_without_hooks(self) -> None:
        response = {"answers": [{"type": "predicate", "name": "tag_is_addressed", "probability": 0.7}]}
        with patch.object(wc.urllib.request, "urlopen", return_value=Response(response)) as urlopen:
            wc.score(STATE, provider="openai-decisions", api_key="k")
        self.assertNotIn("prompt_cache_key", json.loads(urlopen.call_args.args[0].data))


if __name__ == "__main__":
    unittest.main()
