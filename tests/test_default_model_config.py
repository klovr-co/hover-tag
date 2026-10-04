from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import agent_models, tag_config


class DefaultModelConfigTests(unittest.TestCase):
    def test_default_model_selects_its_backend(self):
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config/settings.json"
            tag_config.save_config(config, {"OPENTAG_BACKEND": "codex", "OPENTAG_BOT_NAME": "Custom"})
            values = tag_config.update_config(config, {"OPENTAG_DEFAULT_MODEL": "claude:opus"})
            self.assertEqual(("claude", "claude:opus", "Custom"),
                             (values["OPENTAG_BACKEND"], values["OPENTAG_DEFAULT_MODEL"], values["OPENTAG_BOT_NAME"]))
            self.assertEqual(values, tag_config.read_config(config))

    def test_switching_backend_clears_a_default_model_for_the_other_backend(self):
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config/settings.json"
            tag_config.save_config(config, {"OPENTAG_BACKEND": "claude", "OPENTAG_DEFAULT_MODEL": "claude:opus"})
            values = tag_config.update_config(config, {"OPENTAG_BACKEND": "codex"})
            self.assertEqual(("codex", ""), (values["OPENTAG_BACKEND"], values["OPENTAG_DEFAULT_MODEL"]))
            values = tag_config.update_config(config, {"OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5"})
            values = tag_config.update_config(config, {"OPENTAG_BACKEND": "codex"})
            self.assertEqual("codex:gpt-5.5", values["OPENTAG_DEFAULT_MODEL"])

    def test_model_and_backend_values_are_validated(self):
        for value in ("codex", "claude", "claude:opus", "codex:gpt-5.5", "claude:claude-opus-5-5[1m]", ""):
            self.assertIsNone(tag_config.validation_error("OPENTAG_DEFAULT_MODEL", value), value)
        for value in ("gemini:pro", "opus", "claude:", "claude:op us"):
            self.assertIsNotNone(tag_config.validation_error("OPENTAG_DEFAULT_MODEL", value), value)
        self.assertIsNone(tag_config.validation_error("OPENTAG_BACKENDS", "codex,claude"))
        self.assertIsNotNone(tag_config.validation_error("OPENTAG_BACKENDS", "codex,gemini"))
        self.assertEqual("Choose codex or claude", tag_config.validation_error("OPENTAG_BACKEND", "gemini"))

    def test_thinking_level_and_description_are_validated(self):
        for value in ("", *agent_models.SUPPORTED_REASONING_EFFORTS):
            self.assertIsNone(tag_config.validation_error("OPENTAG_DEFAULT_EFFORT", value), value)
        for value in ("huge", "High", " high", "default"):
            self.assertIsNotNone(tag_config.validation_error("OPENTAG_DEFAULT_EFFORT", value), value)
        for value in ("", "I help with launch work.", "x" * 140):
            self.assertIsNone(tag_config.validation_error("OPENTAG_BOT_DESCRIPTION", value), value)
        for value in ("x" * 141, "two\nlines", "tab\there", "line\u2028break"):
            self.assertIsNotNone(tag_config.validation_error("OPENTAG_BOT_DESCRIPTION", value), value)
        self.assertEqual("Default thinking level", tag_config.LABELS["OPENTAG_DEFAULT_EFFORT"])
        self.assertEqual("Description", tag_config.LABELS["OPENTAG_BOT_DESCRIPTION"])
        shown = tag_config.public_config({"OPENTAG_DEFAULT_EFFORT": "high", "OPENTAG_BOT_DESCRIPTION": "Hi"})
        self.assertEqual({"OPENTAG_DEFAULT_EFFORT": "high", "OPENTAG_BOT_DESCRIPTION": "Hi"}, shown)
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config/settings.json"
            values = tag_config.update_config(config, {"OPENTAG_DEFAULT_EFFORT": "high", "OPENTAG_BOT_DESCRIPTION": "Hi"})
            self.assertEqual(("high", "Hi"), (values["OPENTAG_DEFAULT_EFFORT"], values["OPENTAG_BOT_DESCRIPTION"]))
            with self.assertRaisesRegex(ValueError, "OPENTAG_DEFAULT_EFFORT"):
                tag_config.update_config(config, {"OPENTAG_DEFAULT_EFFORT": "huge"})


class ModelChoiceDisplayTests(unittest.TestCase):
    def test_saved_choices_render_without_starting_a_backend(self):
        self.assertEqual("Claude · opus", agent_models.describe_model_choice("claude:opus", "codex"))
        self.assertEqual("Codex · account default", agent_models.describe_model_choice("", "codex"))
        self.assertEqual("Claude · account default", agent_models.describe_model_choice("claude", "codex"))
        names = {"claude:claude-opus-5-5": "Opus 5.5"}
        self.assertEqual("Opus 5.5", agent_models.model_choice_name("claude:claude-opus-5-5", "codex", names=names))
        self.assertEqual("gpt-5.5", agent_models.model_choice_name("codex:gpt-5.5", "codex", names=names))
        self.assertEqual("Account default", agent_models.model_choice_name("", "claude"))


if __name__ == "__main__":
    unittest.main()
