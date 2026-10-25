"""Tag personas: named faces over one Tag's agent loop (ADR 0012)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
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


class HelperAndPromptTests(unittest.TestCase):
    """The agent's handover helper and what a run is told about personas."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.file = Path(temporary.name) / "persona.json"
        self.personas = [persona("Finance", voice="brisk", skills=["month-end-close"]), persona("Ops")]

    def helper(self, *arguments: str) -> tuple[int, str, str]:
        from scripts import tag_persona
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"OPENTAG_PERSONA_FILE": str(self.file)}), redirect_stdout(out), redirect_stderr(err):
            code = tag_persona.main(list(arguments))
        return code, out.getvalue(), err.getvalue()

    def test_the_default_persona_hands_over_once_to_a_listed_persona(self) -> None:
        tag_personas.write_run_file(self.file, active=None, personas=self.personas)
        code, _, err = self.helper("handover", "--to", "Legal")
        self.assertEqual(1, code)
        self.assertIn("The personas are: Finance, Ops", err)
        code, out, _ = self.helper("handover", "--to", "finance")
        self.assertEqual(0, code)
        self.assertIn("Answer as Finance: Finance's work. Voice: brisk.", out)
        self.assertIn("month-end-close", out)
        self.assertEqual("Finance", tag_personas.read_handover(self.file))
        code, _, err = self.helper("handover", "--to", "Ops")
        self.assertEqual(1, code)
        self.assertIn("already hands over to Finance", err)

    def test_a_persona_already_answering_cannot_hand_over(self) -> None:
        tag_personas.write_run_file(self.file, active=self.personas[0], personas=self.personas)
        code, _, err = self.helper("handover", "--to", "Ops")
        self.assertEqual(1, code)
        self.assertIn("already answering as Finance", err)
        self.file.unlink()
        self.assertEqual(1, self.helper("handover", "--to", "Ops")[0])

    def test_the_request_names_the_persona_or_lists_who_to_hand_over_to(self) -> None:
        from scripts import opentag_agent

        def request() -> str:
            with patch.dict(os.environ, {"OPENTAG_PERSONA_FILE": str(self.file)}):
                return opentag_agent.build_request(channel_id="C123", question="Close the month",
                                                   thread_text="", attachments_dir=None, allowed_scopes="",
                                                   skill_dir=Path("/skill"))
        tag_personas.write_run_file(self.file, active=None, personas=self.personas)
        text = request()
        self.assertIn("you answer under your own name", text)
        self.assertIn("- Ops: Ops's work.", text)
        self.assertIn('tag_persona.py handover --to "NAME"', text)
        tag_personas.write_run_file(self.file, active=self.personas[0], personas=self.personas)
        text = request()
        self.assertIn("Persona: answer as Finance: Finance's work. Voice: brisk.", text)
        self.assertNotIn("handover --to", text)
        with patch.dict(os.environ, {"OPENTAG_PERSONA_FILE": ""}):
            self.assertNotIn("Persona", opentag_agent.build_request(
                channel_id="C123", question="Hi", thread_text="", attachments_dir=None, allowed_scopes="",
                skill_dir=Path("/skill")))


class BridgeCase(unittest.TestCase):
    """One Tag, Maya, with Finance and Ops personas, on a fake Slack."""

    def setUp(self) -> None:
        from scripts import slack_socket_agent
        from scripts.tag_handoff import HandoffStore
        from test_tag_handoff import MAYA, TAG_A, SlackBus
        from test_tag_lead import LeadApp
        self.sa, self.tag, self.maya = slack_socket_agent, TAG_A, MAYA
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.home = root / "maya"
        env = patch.dict(os.environ, {
            "TAG_HOME": str(root), "TAG_INSTANCE_HOME": str(self.home), "SLACK_BOT_TOKEN": "xoxb-test",
            "SLACK_CHANNEL_IDS": "C123", "OPENTAG_BOT_NAME": "Maya", "OPENTAG_SLACK_STREAMING": "0",
            "OPENTAG_CLAUDE_TRANSPORT": "print", "OPENTAG_MEMORY_ROOT": str(root / "memory"),
            "MFS_ALLOWED_SCOPES": "slack://T123/general__C123,file:///finance,file:///ops",
            "MFS_CHANNEL_SCOPES": '{"C123": ["file:///finance", "file:///ops"]}',
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        tag_personas.save(self.home, [
            tag_personas.validate({**FINANCE, "picture": "iris", "memory_scopes": ["file:///finance"]}),
            persona("Ops")])
        self.runs: list[dict[str, Any]] = []
        self.handover_to: str | None = None
        backend = patch.object(slack_socket_agent, "run_backend", side_effect=self.fake_backend)
        backend.start()
        self.addCleanup(backend.stop)
        busy = patch.object(slack_socket_agent, "reserve_thread", return_value=True)
        busy.start()
        self.addCleanup(busy.stop)
        self.customize = patch.object(slack_socket_agent, "slack_customize_granted", return_value=True)
        self.customize.start()
        self.addCleanup(self.customize.stop)
        self.workspace = root / "workspace"
        os.environ["OPENTAG_WORKDIR"] = str(self.workspace)
        owned = patch.object(slack_socket_agent, "owns_workspace", side_effect=lambda path: path == self.workspace)
        owned.start()
        self.addCleanup(owned.stop)
        self.proposal: dict[str, Any] | None = None
        self.bus = SlackBus()
        app = LeadApp()
        self.app = app
        self.bus.apps[TAG_A] = app
        self.client = self.bus.client(TAG_A)
        with patch.object(slack_socket_agent, "App", return_value=app):
            slack_socket_agent.create_app("claude", 30, frozenset({MAYA}), peers=[],
                                          handoff_store=HandoffStore(root / "handoffs"))

    def fake_backend(self, _backend: str, channel: str, caller: str, question: str, thread_text: str,
                     *_args: Any, **kwargs: Any) -> tuple[str, bool]:
        run_file = kwargs.get("persona_file")
        document = tag_personas.read_run_file(run_file) if run_file else {}
        self.runs.append({"question": question, "thread": thread_text, "active": (document.get("active") or {}).get("name"),
                          "listed": [item["name"] for item in document.get("personas") or []],
                          "scopes": kwargs.get("persona_scopes")})
        if self.handover_to and run_file:
            tag_personas.record_handover(run_file, self.handover_to)
        if self.proposal and run_file:
            tag_personas.record_proposal(run_file, self.proposal)
        return "Three items left.", True

    def ask(self, text: str, thread_ts: str | None = None) -> dict[str, Any]:
        with self.bus.lock:
            message = {"channel": "C123", "ts": self.bus.next_ts(), "user": self.maya, "text": text}
            if thread_ts:
                message["thread_ts"] = thread_ts
            self.bus.messages.append(message)
        self.bus.deliver(self.tag, {"type": "app_mention", **message})
        self.bus.settle()
        return message

    def replies(self, thread_ts: str) -> list[tuple[str, str | None]]:
        """(text, username) for each final reply the Tag posted, in order."""
        found = []
        for call in self.client.chat_postMessage.call_args_list:
            kwargs = call.kwargs
            if kwargs.get("thread_ts") == thread_ts and kwargs.get("text") not in (None, "") \
                    and not str(kwargs.get("text")).startswith("_"):
                found.append((kwargs["text"], kwargs.get("username")))
        return found

    def press(self, action_id: str, proposal_id: str, user: str | None = None) -> None:
        body = {"user": {"id": user or self.maya}, "channel": {"id": "C123"},
                "actions": [{"action_id": action_id, "value": json.dumps({"proposal": proposal_id})}]}
        self.app.actions[action_id](unittest.mock.MagicMock(), body, self.client, unittest.mock.MagicMock())



class PersonaBridgeTests(BridgeCase):
    def test_a_named_persona_answers_under_its_name_and_keeps_the_thread(self) -> None:
        origin = self.ask(f"<@{self.tag}> Finance: what's left for month-end?")
        self.assertEqual("what's left for month-end?", self.runs[-1]["question"])
        self.assertEqual("Finance", self.runs[-1]["active"])
        self.assertEqual("file:///finance", self.runs[-1]["scopes"])  # The persona's memory scope.
        self.assertIn(("Three items left.", "Finance"), self.replies(origin["ts"]))
        call = next(c for c in self.client.chat_postMessage.call_args_list if c.kwargs.get("username") == "Finance")
        self.assertTrue(call.kwargs["icon_url"].endswith("tag-iris-pixel.png"))
        self.assertEqual("Finance", tag_personas.thread_persona(self.home, "C123", origin["ts"]))

        self.ask(f"<@{self.tag}> and the invoices?", thread_ts=origin["ts"])
        self.assertEqual("Finance", self.runs[-1]["active"])
        self.assertIn("as Finance", self.runs[-1]["thread"])  # Earlier replies are labelled by persona.

        self.ask(f"<@{self.tag}> Maya: thanks, what's on today?", thread_ts=origin["ts"])
        self.assertIsNone(self.runs[-1]["active"])
        self.assertEqual(["Finance", "Ops"], self.runs[-1]["listed"])
        self.assertIsNone(tag_personas.thread_persona(self.home, "C123", origin["ts"]))

    def test_the_default_persona_hands_over_with_one_line_then_the_persona_answers(self) -> None:
        self.handover_to = "Finance"
        origin = self.ask(f"<@{self.tag}> what's left for month-end?")
        self.assertIsNone(self.runs[-1]["active"])
        thread = [m for m in self.bus.thread(origin["ts"]) if not m["text"].startswith("_")]
        texts = [m["text"] for m in thread]
        handover = tag_personas.HANDOVER_TEXT.format(name="Finance")
        self.assertIn(handover, texts)
        self.assertEqual("Three items left.", texts[-1])
        self.assertLess(texts.index(handover), len(texts) - 1)
        self.assertIn(("Three items left.", "Finance"), self.replies(origin["ts"]))
        self.assertNotIn((handover, "Finance"), self.replies(origin["ts"]))
        self.assertEqual("Finance", tag_personas.thread_persona(self.home, "C123", origin["ts"]))
        self.assertEqual("Finance", tag_personas.message_persona(self.home, "C123", thread[-1]["ts"]))

        # The thread stays with Finance, and Ops can take it by name.
        self.handover_to = None
        self.ask(f"<@{self.tag}> and the invoices?", thread_ts=origin["ts"])
        self.assertEqual("Finance", self.runs[-1]["active"])
        self.ask(f"<@{self.tag}> Ops: who's on shift?", thread_ts=origin["ts"])
        self.assertEqual("Ops", self.runs[-1]["active"])

    def test_without_the_customize_scope_the_app_posts_with_the_persona_name_first(self) -> None:
        self.customize.stop()
        with patch.object(self.sa, "slack_customize_granted", return_value=False):
            origin = self.ask(f"<@{self.tag}> Finance: what's left?")
        self.assertIn("*Finance:* Three items left.", [m["text"] for m in self.bus.thread(origin["ts"])])
        self.assertTrue(all(call.kwargs.get("username") is None for call in self.client.chat_postMessage.call_args_list))
        self.customize.start()

    def test_a_persona_scope_narrows_the_runs_memory_sources(self) -> None:
        from scripts.opentag_process_env import backend_environment
        source = dict(os.environ)
        whole = backend_environment(source, transport="slack", conversation_id="C123", caller_id=self.maya,
                                    authorized_scopes="slack://T123/general__C123,file:///finance,file:///ops")
        self.assertEqual("slack://T123/general__C123,file:///finance,file:///ops", whole["MFS_ALLOWED_SCOPES"])
        narrowed = backend_environment(source, transport="slack", conversation_id="C123", caller_id=self.maya,
                                       authorized_scopes="slack://T123/general__C123,file:///finance,file:///ops",
                                       persona_scopes="file:///finance,file:///elsewhere")
        self.assertEqual("file:///finance", narrowed["MFS_ALLOWED_SCOPES"])

    def test_without_personas_nothing_changes(self) -> None:
        tag_personas.save(self.home, [])
        origin = self.ask(f"<@{self.tag}> Finance: what's left?")
        self.assertEqual("Finance: what's left?", self.runs[-1]["question"])
        self.assertIsNone(self.runs[-1]["active"])
        self.assertTrue(all(username is None for _text, username in self.replies(origin["ts"])))


class ProposalTests(BridgeCase):
    """The Tag proposes a persona or a skill; only the person who asked approves it."""

    def test_the_default_persona_proposes_a_persona_and_the_requester_approves_it(self) -> None:
        legal = tag_personas.validate({"name": "Legal", "description": "Contracts and NDAs.", "voice": "careful"})
        self.proposal = {"kind": "persona", "persona": legal.to_json()}
        origin = self.ask(f"<@{self.tag}> we need someone for contracts")
        record = tag_persona_proposals_store(self.home).all()[0]
        self.assertEqual("pending", record["state"])
        card = next(m for m in self.bus.thread(origin["ts"]) if m["text"] == "A proposal needs your approval")
        self.assertEqual(card["ts"], record["card_ts"])
        self.assertIsNone(tag_personas.find(tag_personas.load(self.home), "Legal"))

        self.press(self.sa.PERSONA_PROPOSAL_APPROVE_ACTION_ID, record["id"], user="UOTHER")
        self.assertIsNone(tag_personas.find(tag_personas.load(self.home), "Legal"))
        self.press(self.sa.PERSONA_PROPOSAL_APPROVE_ACTION_ID, record["id"])
        self.assertEqual("careful", tag_personas.find(tag_personas.load(self.home), "Legal").voice)
        self.assertEqual("created", tag_persona_proposals_store(self.home).get(record["id"])["state"])
        self.press(self.sa.PERSONA_PROPOSAL_REJECT_ACTION_ID, record["id"])  # Decided once.
        self.assertEqual("created", tag_persona_proposals_store(self.home).get(record["id"])["state"])

    def test_a_proposed_skill_is_written_to_the_tags_own_folder_only_after_approval(self) -> None:
        self.proposal = {"kind": "skill", "skill": {"name": "month-end-close", "description": "Closing the month.",
                                                    "body": "1. Reconcile the bank.\n2. Post accruals."}}
        self.ask(f"<@{self.tag}> remember how we close the month")
        record = tag_persona_proposals_store(self.home).all()[0]
        target = self.workspace / ".claude/skills/month-end-close/SKILL.md"
        self.assertFalse(target.exists())
        self.press(self.sa.PERSONA_PROPOSAL_APPROVE_ACTION_ID, record["id"])
        self.assertIn("name: month-end-close", target.read_text(encoding="utf-8"))
        self.assertTrue((self.workspace / ".agents/skills/month-end-close/SKILL.md").is_file())

    def test_refused_rejected_and_expired_proposals_change_nothing(self) -> None:
        self.proposal = {"kind": "persona", "persona": {"name": "Finance", "description": "Again."}}
        origin = self.ask(f"<@{self.tag}> add finance")
        self.assertIn("I can't propose that: There is already a persona named Finance.",
                      [m["text"] for m in self.bus.thread(origin["ts"])])
        self.assertEqual([], tag_persona_proposals_store(self.home).all())

        self.proposal = {"kind": "persona", "persona": {"name": "Legal", "description": "Contracts."}}
        self.ask(f"<@{self.tag}> add legal")
        first = tag_persona_proposals_store(self.home).all()[0]
        self.press(self.sa.PERSONA_PROPOSAL_REJECT_ACTION_ID, first["id"])
        self.assertEqual("rejected", tag_persona_proposals_store(self.home).get(first["id"])["state"])

        self.ask(f"<@{self.tag}> add legal again")
        second = next(r for r in tag_persona_proposals_store(self.home).all() if r["id"] != first["id"])
        tag_persona_proposals_store(self.home).update(second["id"], lambda record: record.update(expires_at=0))
        self.press(self.sa.PERSONA_PROPOSAL_APPROVE_ACTION_ID, second["id"])
        self.assertEqual("expired", tag_persona_proposals_store(self.home).get(second["id"])["state"])
        self.assertIsNone(tag_personas.find(tag_personas.load(self.home), "Legal"))

    def test_a_skill_is_refused_when_the_tag_works_in_a_folder_of_yours(self) -> None:
        self.proposal = {"kind": "skill", "skill": {"name": "close", "description": "Close.", "body": "Steps."}}
        with patch.object(self.sa, "owns_workspace", return_value=False):
            origin = self.ask(f"<@{self.tag}> save this procedure")
        texts = [m["text"] for m in self.bus.thread(origin["ts"])]
        self.assertTrue(any("a folder of yours" in text for text in texts))

    def test_a_persona_answering_cannot_propose(self) -> None:
        with self.assertRaisesRegex(PersonaError, "Only the Tag under its own name proposes"):
            run_file = Path(tempfile.mkdtemp()) / "run.json"
            tag_personas.write_run_file(run_file, active=persona("Finance"), personas=[])
            tag_personas.record_proposal(run_file, {"kind": "skill"})


def tag_persona_proposals_store(home: Path):
    from scripts import tag_persona_proposals
    return tag_persona_proposals.store(home)
