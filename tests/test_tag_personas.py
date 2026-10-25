"""Tag personas: named faces over one Tag's agent loop (ADR 0012)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import tag_cli, tag_instances, tag_personas
from scripts.tag_personas import Persona, PersonaError

FINANCE = {"name": "Finance", "description": "Month-end close and invoices.", "skills": ["month-end-close"]}


def persona(name: str, **fields: Any) -> Persona:
    return tag_personas.validate({"name": name, "description": f"{name}'s work.", **fields})


class PersonaRecordTests(unittest.TestCase):
    def test_a_persona_keeps_only_tidy_fields(self) -> None:
        found = tag_personas.validate({**FINANCE, "description": "  Month-end   close\nand invoices. ",
                                       "voice": "warm,  short replies", "channels": {"C123": "when-addressed"}})
        self.assertEqual("Month-end close and invoices.", found.description)
        self.assertEqual("warm, short replies", found.voice)
        self.assertEqual("only-when-asked", found.speaks_up)
        self.assertEqual("when-addressed", found.speaks_up_in("C123"))
        self.assertEqual("only-when-asked", found.speaks_up_in("C999"))

    def test_refuses_names_that_cannot_be_called_or_already_mean_something(self) -> None:
        for name in ("", "Fin: ance", "Finance, Ops", "9lives", "Lead", "tag", "x" * 36):
            with self.subTest(name=name), self.assertRaises(PersonaError):
                tag_personas.validate({**FINANCE, "name": name})
        self.assertEqual("Head of Ops", tag_personas.validate({**FINANCE, "name": "Head of Ops"}).name)

    def test_refuses_long_voice_unknown_levels_bad_skills_and_channels(self) -> None:
        with self.assertRaisesRegex(PersonaError, "Rules and procedures belong in skills"):
            tag_personas.validate({**FINANCE, "voice": "x" * 201})
        with self.assertRaisesRegex(PersonaError, "Speaks up must be one of"):
            tag_personas.validate({**FINANCE, "speaks_up": "always"})
        with self.assertRaisesRegex(PersonaError, "skills by their folder name"):
            tag_personas.validate({**FINANCE, "skills": ["../secrets"]})
        with self.assertRaisesRegex(PersonaError, "by their ID"):
            tag_personas.validate({**FINANCE, "channels": {"#finance": "chimes-in"}})

    def test_memory_scopes_must_be_among_the_tags_allowed_sources(self) -> None:
        allowed = ["slack://T1/C1", "file:///notes"]
        self.assertEqual(("slack://T1/C1",), tag_personas.validate(
            {**FINANCE, "memory_scopes": ["slack://T1/C1"]}, allowed_scopes=allowed).memory_scopes)
        with self.assertRaisesRegex(PersonaError, "not allowed: slack://T1/C2"):
            tag_personas.validate({**FINANCE, "memory_scopes": ["slack://T1/C2"]}, allowed_scopes=allowed)

    def test_pictures_are_characters_or_public_https_urls(self) -> None:
        self.assertIsNone(tag_personas.picture_error("Iris"))
        self.assertIsNone(tag_personas.picture_error("https://example.com/finance.png"))
        for bad in ("http://example.com/a.png", "https://localhost/a.png", "https://10.0.0.1/a.png",
                    "https://user:pw@example.com/a.png", "file:///a.png"):
            with self.subTest(bad=bad):
                self.assertIsNotNone(tag_personas.picture_error(bad))
        self.assertTrue(tag_personas.picture_url("iris").endswith("/assets/characters/tag-iris-pixel.png"))
        self.assertIn(tag_personas.ICON_COMMIT, tag_personas.picture_url(""))
        self.assertEqual({"username": "Finance", "icon_url": "https://example.com/f.png"},
                         persona("Finance", picture="https://example.com/f.png").message_fields())

    def test_only_one_persona_chimes_in_per_channel(self) -> None:
        tag_personas.check_all([persona("Finance", channels={"C111": "chimes-in"}),
                                persona("Ops", channels={"C222": "chimes-in"})], tag_channels=["C111", "C222"])
        with self.assertRaisesRegex(PersonaError, "Only one persona can chime in per channel; in C111"):
            tag_personas.check_all([persona("Finance", channels={"C111": "chimes-in"}),
                                    persona("Ops", speaks_up="chimes-in")], tag_channels=["C111"])
        # An override lets a second persona keep chiming in by default elsewhere.
        tag_personas.check_all([persona("Finance", channels={"C111": "chimes-in"}),
                                persona("Ops", speaks_up="chimes-in", channels={"C111": "when-addressed"})],
                               tag_channels=["C111", "C222"])
        with self.assertRaisesRegex(PersonaError, "chime in by default"):
            tag_personas.check_all([persona("Finance", speaks_up="chimes-in"), persona("Ops", speaks_up="chimes-in")])

    def test_names_are_unique_and_not_the_tags_own(self) -> None:
        with self.assertRaisesRegex(PersonaError, "already a persona named"):
            tag_personas.check_all([persona("Finance"), persona("finance")])
        with self.assertRaisesRegex(PersonaError, "this Tag's own name"):
            tag_personas.check_all([persona("Maya")], tag_name="maya")


class CallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.personas = [persona("Finance"), persona("Head of Ops"), persona("Ops")]

    def called(self, text: str) -> tuple[str, str] | None:
        call = tag_personas.parse_call(text, self.personas)
        return (call.persona.name, call.text) if call else None

    def test_a_name_first_followed_by_a_colon_comma_or_after_ask_is_a_call(self) -> None:
        self.assertEqual(("Finance", "close the month"), self.called("<@U1> Finance: close the month"))
        self.assertEqual(("Finance", "check the invoice"), self.called("<@U1> finance, check the invoice"))
        self.assertEqual(("Finance", "check the invoice"), self.called("<@U1> ask Finance to check the invoice"))
        self.assertEqual(("Head of Ops", "rota?"), self.called("<@U1> head of ops: rota?"))
        self.assertEqual(("Ops", "rota?"), self.called("<@U1> Ops: rota?"))

    def test_a_name_elsewhere_or_without_a_request_is_not_a_call(self) -> None:
        for text in ("<@U1> ask the finance team whether we paid", "<@U1> what does Finance: think",
                     "<@U1> Finance:", "<@U1> Financed: yes", "<@U1> close the month"):
            with self.subTest(text=text):
                self.assertIsNone(self.called(text))


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def test_save_and_load_round_trip_and_skip_a_broken_entry(self) -> None:
        tag_personas.save(self.home, [persona("Finance", voice="brisk")])
        document = json.loads(tag_personas.path(self.home).read_text(encoding="utf-8"))
        document["personas"].append({"name": "Lead", "description": "reserved"})
        tag_personas.path(self.home).write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(["Finance"], [item.name for item in tag_personas.load(self.home)])
        self.assertEqual([], tag_personas.load(self.home / "missing"))

    def test_threads_remember_their_persona_and_messages_who_posted_them(self) -> None:
        self.assertIsNone(tag_personas.thread_persona(self.home, "C1", "1.2"))
        tag_personas.set_thread_persona(self.home, "C1", "1.2", "Finance", by="handover")
        self.assertEqual("Finance", tag_personas.thread_persona(self.home, "C1", "1.2"))
        tag_personas.set_thread_persona(self.home, "C1", "1.2", None, by="named")
        self.assertIsNone(tag_personas.thread_persona(self.home, "C1", "1.2"))
        with self.assertRaises(ValueError):
            tag_personas.set_thread_persona(self.home, "../C1", "1.2", "Finance", by="named")
        tag_personas.record_message(self.home, "C1", "1.3", "Finance")
        self.assertEqual("Finance", tag_personas.message_persona(self.home, "C1", "1.3"))
        old = tag_personas._message_path(self.home, "C1", "1.3")
        os.utime(old, (time.time() - 40 * 86_400,) * 2)
        self.assertEqual(1, tag_personas.prune_messages(self.home))
        self.assertIsNone(tag_personas.message_persona(self.home, "C1", "1.3"))


class PersonaCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        self.root.mkdir()
        self.home = tag_instances.create(self.root, "maya").home
        (self.home / "config").mkdir(parents=True, exist_ok=True)
        (self.home / "config/settings.json").write_text(json.dumps({
            "OPENTAG_BOT_NAME": "Maya", "SLACK_CHANNEL_IDS": "C123,C456",
            "MFS_ALLOWED_SCOPES": "slack://T1/C123,file:///finance"}), encoding="utf-8")

    def cli(self, *arguments: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"TAG_HOME": str(self.root), **(env or {})}), \
                patch.object(sys, "argv", ["tag", *arguments]), redirect_stdout(out), redirect_stderr(err):
            code = tag_cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_add_set_show_list_and_remove(self) -> None:
        code, out, _ = self.cli("maya", "persona", "add", "Finance", "--description", "Month-end close.",
                                "--picture", "iris", "--skill", "month-end-close", "--memory-scope", "file:///finance",
                                "--voice", "brisk, exact", "--channel", "C456=when-addressed", "--json")
        self.assertEqual(0, code)
        report = json.loads(out)
        self.assertEqual("Maya", report["default_persona"])
        finance = report["personas"][0]
        self.assertEqual(["month-end-close"], finance["skills"])
        self.assertEqual({"C456": "when-addressed"}, finance["channels"])
        self.assertEqual("only-when-asked", finance["speaks_up_now"])
        self.assertIn("channel watching", report["speaks_up_note"])
        self.assertTrue(finance["picture_url"].endswith("tag-iris-pixel.png"))

        code, out, _ = self.cli("maya", "persona", "set", "finance", "--name", "Money", "--no-skills",
                                "--channel", "C456=default", "--json")
        self.assertEqual(0, code)
        money = json.loads(out)["personas"][0]
        self.assertEqual(("Money", [], {}), (money["name"], money["skills"], money["channels"]))
        self.assertIsNone(json.loads(out)["speaks_up_note"])

        self.assertEqual(0, self.cli("maya", "persona", "show", "Money")[0])
        code, out, _ = self.cli("persona", "list", "--json")  # No name: the only Tag.
        self.assertEqual(["Money"], [item["name"] for item in json.loads(out)["personas"]])
        self.assertEqual(0, self.cli("maya", "persona", "remove", "money")[0])
        self.assertEqual([], tag_personas.load(self.home))

    def test_refusals_explain_themselves_and_change_nothing(self) -> None:
        self.cli("maya", "persona", "add", "Finance", "--description", "Close.", "--speaks-up", "chimes-in")
        code, out, _ = self.cli("maya", "persona", "add", "Ops", "--description", "Rota.",
                                "--channel", "C123=chimes-in", "--json")
        self.assertEqual(1, code)
        self.assertIn("Only one persona can chime in per channel", json.loads(out)["error"])
        code, _, err = self.cli("maya", "persona", "add", "Ops", "--description", "Rota.",
                                "--memory-scope", "slack://T1/C999")
        self.assertEqual(1, code)
        self.assertIn("not allowed", err)
        self.assertEqual(1, self.cli("maya", "persona", "add", "Maya", "--description", "Me.")[0])
        self.assertEqual(1, self.cli("maya", "persona", "add", "finance", "--description", "Again.")[0])
        self.assertEqual(1, self.cli("maya", "persona", "show", "Nobody")[0])
        self.assertEqual(["Finance"], [item.name for item in tag_personas.load(self.home)])

    def test_a_tags_agent_cannot_change_personas_from_inside_a_request(self) -> None:
        marker = sorted(tag_cli.AGENT_RUN_MARKERS)[0]
        code, _, err = self.cli("maya", "persona", "add", "Finance", "--description", "Close.", env={marker: "1"})
        self.assertEqual(2, code)
        self.assertIn("Lead proposes a persona", err)
        self.assertEqual(0, self.cli("maya", "persona", "list", env={marker: "1"})[0])
        self.assertEqual([], tag_personas.load(self.home))

    def test_the_personas_capability_is_advertised(self) -> None:
        self.assertIn("personas", tag_cli.CAPABILITIES)


if __name__ == "__main__":
    unittest.main()
