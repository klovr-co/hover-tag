"""Lead: one designated Tag routes to one owner and alone creates Tags (#232)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from scripts import slack_cli_config, slack_socket_agent, tag_lead, tag_cli, tag_creator, tag_handoff, tag_instances
from scripts.tag_lead import LeadBridge, LeadRefused
from scripts.tag_handoff import HandoffRefused, HandoffStore
from test_tag_handoff import MAYA, TAG_A, TAG_B, TAG_C, SlackBus

OTHER_PERSON = "UOTHER"
APPROVE = slack_socket_agent.TAG_CREATE_APPROVE_ACTION_ID
REJECT = slack_socket_agent.TAG_CREATE_REJECT_ACTION_ID
CHECK = slack_socket_agent.TAG_CREATE_CHECK_ACTION_ID
PROPOSAL = {"name": "Finance Tag", "description": "Answers finance questions and runs the month-end close.",
            "boundary": "credentials", "why_not_skill": "It needs the finance system's own credentials.",
            "channels": ["C123"], "model": "", "routines": [], "reason": ""}

# Creating Tags is on hold (ADR 0012). The dormant creation code keeps its tests, so they run with the hold
# lifted; HoldTests and the tests named "on hold" check the hold itself.
_lift_hold = patch.object(tag_lead, "CREATION_ON_HOLD", False)


def setUpModule() -> None:
    _lift_hold.start()


def tearDownModule() -> None:
    _lift_hold.stop()


def on_hold():
    return patch.object(tag_lead, "CREATION_ON_HOLD", True)


def make_tags(root: Path, *ids: str, app: set[str] = frozenset()) -> None:
    for tag_id in ids:
        home = tag_instances.create(root, tag_id).home
        (home / "config").mkdir(parents=True, exist_ok=True)
        (home / "config/settings.json").write_text(
            json.dumps({"SLACK_APP_ID": "A123"} if tag_id in app else {}), encoding="utf-8")


class DesignationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        self.root.mkdir()

    def test_only_one_tag_holds_the_role_and_moving_it_replaces_the_holder(self) -> None:
        tag_lead.set_lead(self.root, "research")
        tag_lead.set_lead(self.root, "writer")
        self.assertEqual("writer", tag_lead.lead_id(self.root))
        record = json.loads(tag_lead.record_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual("writer", record["id"])

    def test_migration_designates_the_main_tag_once_and_is_idempotent(self) -> None:
        make_tags(self.root, "research", "writer", app={"research", "writer"})
        tag_instances.set_main_tag(self.root, "writer")
        tags = tag_instances.discover(self.root)
        self.assertEqual("writer", tag_lead.migrate(self.root, tags, "writer"))
        marker = tag_lead._migration_marker(self.root)
        first = marker.read_text(encoding="utf-8")
        before = tag_lead.record_path(self.root).read_text(encoding="utf-8")
        for _ in range(3):  # Restarts and upgrades run it again.
            self.assertEqual("writer", tag_lead.migrate(self.root, tag_instances.discover(self.root), "research"))
        self.assertEqual(before, tag_lead.record_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(first, marker.read_text(encoding="utf-8"))

    def test_migration_prefers_a_tag_with_an_app_when_there_is_no_main_tag(self) -> None:
        make_tags(self.root, "alpha", "beta", app={"beta"})
        self.assertEqual("beta", tag_lead.migrate(self.root, tag_instances.discover(self.root), None))

    def test_migration_repairs_a_role_left_on_a_removed_tag(self) -> None:
        make_tags(self.root, "alpha", "beta")
        tag_lead.set_lead(self.root, "gone")
        self.assertEqual("alpha", tag_lead.migrate(self.root, tag_instances.discover(self.root), "alpha"))

    def test_migration_without_tags_records_nothing(self) -> None:
        self.assertIsNone(tag_lead.migrate(self.root, [], None))
        self.assertFalse(tag_lead._migration_marker(self.root).exists())
        self.assertFalse(tag_lead.record_path(self.root).exists())

    def test_role_and_created_list_follow_a_renamed_tag(self) -> None:
        tag_lead.set_lead(self.root, "new-tag")
        tag_lead.record_created(self.root, "new-tag-2")
        tag_lead.save_card(self.root, "new-tag", user_id="ULEAD", team="T1", name="Lead")
        tag_lead.renamed(self.root, "new-tag", "t1-a1")
        tag_lead.renamed(self.root, "new-tag-2", "t1-a2")
        self.assertEqual("t1-a1", tag_lead.lead_id(self.root))
        self.assertEqual(["t1-a2"], tag_lead.created_by_lead(self.root))
        self.assertEqual(["t1-a1"], [card.id for card in tag_lead.cards(self.root)])

    def test_cli_moves_the_role_and_reports_it_in_list_and_status(self) -> None:
        make_tags(self.root, "research", "writer", app={"research", "writer"})
        tag_instances.set_main_tag(self.root, "research")
        out = io.StringIO()
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}), redirect_stdout(out), \
                patch.object(sys, "argv", ["tag", "writer", "lead", "set", "--json"]):
            self.assertEqual(0, tag_cli.main())
        report = json.loads(out.getvalue())
        self.assertEqual("writer", report["lead"])
        self.assertEqual({"research": False, "writer": True},
                         {tag["id"]: tag["lead"] for tag in report["tags"]})
        self.assertFalse(report["service_account"]["ready"])
        self.assertEqual("on", report["creating_tags"])
        out = io.StringIO()
        with on_hold(), patch.dict(os.environ, {"TAG_HOME": str(self.root)}), redirect_stdout(out), \
                patch.object(sys, "argv", ["tag", "lead", "--json"]):
            self.assertEqual(0, tag_cli.main())
        self.assertEqual("on_hold", json.loads(out.getvalue())["creating_tags"])
        out = io.StringIO()
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}), redirect_stdout(out), \
                patch.object(sys, "argv", ["tag", "list", "--json"]):
            self.assertEqual(0, tag_cli.main())
        rows = {row["id"]: row for row in json.loads(out.getvalue())["tags"]}
        self.assertTrue(rows["writer"]["lead"])
        self.assertFalse(rows["research"]["lead"])
        self.assertFalse(rows["research"]["created_by_lead"])

    def test_cli_refuses_tag_management_inside_a_tags_request(self) -> None:
        for command in (["add", "--json"], ["writer", "lead", "set"], ["writer", "setup", "--json"]):
            with self.subTest(command=command), patch.dict(os.environ, {
                "TAG_HOME": str(self.root), "OPENTAG_CALLER_ID": MAYA, "OPENTAG_HANDOFF_DEPTH": "0",
            }), patch.object(sys, "argv", ["tag", *command]), redirect_stderr(io.StringIO()) as err, \
                    patch.object(tag_cli.subprocess, "call") as call:
                self.assertEqual(2, tag_cli.main())
                call.assert_not_called()
                self.assertIn("Ask Lead", err.getvalue())
        self.assertEqual([], [item for item in tag_instances.discover(self.root)
                              if Path(str(item["home"])).exists()])

    def test_a_tag_named_lead_before_the_command_existed_keeps_working(self) -> None:
        with patch.object(tag_instances, "RESERVED_NAMES", tag_instances.RESERVED_NAMES - {"lead"}):
            home = tag_instances.create(self.root, "lead").home
        self.assertEqual(home, tag_instances.resolve(self.root, "lead").home)
        with self.assertRaises(ValueError):
            tag_instances.create(self.root, "lead")


class PresetTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        self.root.mkdir()
        self.preset = Path(temporary.name) / "preset.json"

    def run_add(self) -> tuple[int, list[dict[str, Any]], MagicMock]:
        raw = io.StringIO()
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}), \
                patch.object(sys, "argv", ["tag", "add", "--json", "--preset", str(self.preset)]), \
                patch.object(sys.stdin, "isatty", return_value=False), patch.object(sys, "__stdout__", raw), \
                patch.object(sys, "stdout", sys.stdout), patch.object(tag_cli.subprocess, "call", return_value=0) as call:
            code = tag_cli.main()
        return code, [json.loads(line) for line in raw.getvalue().splitlines() if line.startswith("{")], call

    def test_preset_seeds_the_new_tags_settings_before_setup_asks(self) -> None:
        folder = str(self.root / "shared/slack/service-account")
        self.preset.write_text(json.dumps({"OPENTAG_BOT_NAME": "Finance Tag", "SLACK_ALLOWED_USER_IDS": MAYA,
                                           "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5",
                                           slack_cli_config.TAG_SETTING: folder}), encoding="utf-8")
        code, events, call = self.run_add()
        self.assertEqual(0, code)
        self.assertEqual({"type": "tag", "tag": "new-tag"}, events[0])
        settings = json.loads((tag_instances.resolve(self.root, "new-tag").home / "config/settings.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual("Finance Tag", settings["OPENTAG_BOT_NAME"])
        self.assertEqual(MAYA, settings["SLACK_ALLOWED_USER_IDS"])
        self.assertEqual(folder, settings["OPENTAG_SLACK_CLI_CONFIG"])
        call.assert_called_once()

    def test_preset_refuses_tokens_and_unknown_settings(self) -> None:
        self.preset.write_text(json.dumps({"SLACK_BOT_TOKEN": "xoxb-1"}), encoding="utf-8")
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}), \
                patch.object(sys, "argv", ["tag", "add", "--json", "--preset", str(self.preset)]), \
                patch.object(sys, "__stdout__", io.StringIO()), patch.object(sys, "stdout", sys.stdout), \
                patch.object(tag_cli.subprocess, "call") as call, self.assertRaises(ValueError):
            tag_cli._run_cli()
        call.assert_not_called()


class SlackCliConfigTests(unittest.TestCase):
    def test_flags_use_an_isolated_folder_only_when_one_is_chosen(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual([], slack_cli_config.flags())
        with patch.dict(os.environ, {slack_cli_config.CONFIG_DIR_ENV: "/srv/tag/sa"}):
            self.assertEqual(["--config-dir", "/srv/tag/sa"], slack_cli_config.flags())
        with patch.dict(os.environ, {slack_cli_config.CONFIG_DIR_ENV: "relative"}):
            self.assertEqual([], slack_cli_config.flags())

    def test_setup_lists_sign_ins_from_the_service_accounts_folder(self) -> None:
        from scripts import opentag_setup
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch.dict(os.environ, {slack_cli_config.CONFIG_DIR_ENV: "/srv/tag/sa"}), \
                patch.object(opentag_setup.subprocess, "run", return_value=completed) as run:
            opentag_setup.list_slack_sign_ins()
        self.assertEqual(["--config-dir", "/srv/tag/sa"], run.call_args.args[0][-2:])

    def test_a_created_tag_keeps_using_the_service_account(self) -> None:
        values = {slack_cli_config.TAG_SETTING: "/srv/tag/sa"}
        with patch.dict(os.environ, {}, clear=True):
            slack_cli_config.apply_tag_setting(values)
            self.assertEqual("/srv/tag/sa", os.environ[slack_cli_config.CONFIG_DIR_ENV])

    def test_service_account_folder_defaults_under_the_installation(self) -> None:
        root = Path("/srv/Tag")
        self.assertEqual(root / "shared/slack/service-account", slack_cli_config.service_account_dir(root))
        self.assertEqual(Path("/sa"), slack_cli_config.service_account_dir(root, {"OPENTAG_LEAD_SLACK_CONFIG": "/sa"}))


class AgentHelperTests(unittest.TestCase):
    """The agent's helpers refuse early; the bridge enforces the same rules again."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.requests = self.root / "requests.jsonl"
        tag_lead.set_lead(self.root, "tag-a")
        for tag_id, user, name in (("tag-a", TAG_A, "Lead"), ("tag-b", TAG_B, "Tag B"), ("tag-c", TAG_C, "Tag C")):
            tag_lead.save_card(self.root, tag_id, user_id=user, team="T123", name=name,
                                channels=[{"id": "C123", "name": "launch"}])

    def env(self, tag_id: str, depth: str = "0"):
        return patch.dict(os.environ, {"TAG_HOME": str(self.root), "TAG_ID": tag_id, "OPENTAG_HANDOFF_DEPTH": depth,
                                       "OPENTAG_HANDOFF_REQUESTS": str(self.requests), "OPENTAG_PEER_TAGS": ""})

    def test_a_tag_that_is_not_lead_cannot_propose_a_tag(self) -> None:
        with self.env("tag-b"), self.assertRaisesRegex(LeadRefused, "Only Lead can create Tags"):
            tag_lead.record_proposal(PROPOSAL)
        self.assertFalse(self.requests.exists())

    def test_lead_proposes_only_for_a_real_boundary(self) -> None:
        with self.env("tag-a"):
            with self.assertRaisesRegex(LeadRefused, "real boundary"):
                tag_lead.record_proposal({**PROPOSAL, "boundary": "role"})
            with self.assertRaisesRegex(LeadRefused, "skill or an in-process subagent"):
                tag_lead.record_proposal({**PROPOSAL, "why_not_skill": ""})
            tag_lead.record_proposal({**PROPOSAL, "routines": ["weekdays 09:00::Post the open invoices"]})
        proposal = tag_handoff.read_proposal(self.requests)
        self.assertEqual("Finance Tag", proposal["name"])
        self.assertEqual([{"schedule": "weekdays 09:00", "prompt": "Post the open invoices"}], proposal["routines"])

    def test_lead_routes_to_one_owner_and_never_collects_answers(self) -> None:
        with self.env("tag-a"):
            with self.assertRaisesRegex(HandoffRefused, "doesn't collect answers"):
                tag_handoff.record_request(["Tag B"], "Help.", 30)
            message = tag_handoff.record_route("Tag B", "Tag B owns revenue questions.")
            with self.assertRaisesRegex(HandoffRefused, "one Tag action"):
                tag_handoff.record_route("Tag C", "Second owner.")
        self.assertIn("Tag B, which will answer here itself", message)
        self.assertEqual({"target": {"name": "Tag B", "user_id": TAG_B}, "reason": "Tag B owns revenue questions."},
                         tag_handoff.read_route(self.requests))

    def test_a_specialist_may_only_escalate_to_lead(self) -> None:
        with self.env("tag-b"):
            with self.assertRaisesRegex(HandoffRefused, "Only Lead routes"):
                tag_handoff.record_route("Tag C", "It owns this.")
            with self.assertRaisesRegex(HandoffRefused, "not a Tag this Tag may ask"):
                tag_handoff.record_request(["Tag C"], "Write the headline.", 30)
            tag_handoff.record_request(["Lead"], "Should finance or sales own discount approvals?", 30)
        request = tag_handoff.read_request(self.requests)
        self.assertEqual([{"name": "Lead", "user_id": TAG_A}], request["targets"])

    def test_a_tag_answering_another_tag_can_neither_route_nor_ask(self) -> None:
        with self.env("tag-a", depth="1"), self.assertRaises(HandoffRefused):
            tag_handoff.record_route("Tag B", "It owns this.")
        with self.env("tag-b", depth="1"), self.assertRaises(HandoffRefused):
            tag_handoff.record_request(["Lead"], "Judgment call.", 30)
        self.assertEqual(2, tag_handoff.MAX_TAG_HOPS)

    def test_lead_instructions_list_its_lanes_and_forbid_splitting(self) -> None:
        from scripts import opentag_agent
        with self.env("tag-a"):
            text = opentag_agent.build_instructions(skill_dir=Path("/skill"), workdir=self.root, channel_id="C123")
        self.assertIn("- Tag B (in this channel)", text)
        self.assertIn("Never split a request across several Tags", text)
        self.assertIn("route --to", text)
        self.assertIn("propose --name", text)
        self.assertNotIn("Asking other Tags:", text)
        with self.env("tag-b"):
            text = opentag_agent.build_instructions(skill_dir=Path("/skill"), workdir=self.root, channel_id="C123")
        self.assertIn("Lead is this installation's Lead", text)
        self.assertIn("You cannot create Tags", text)
        self.assertNotIn("propose --name", text)

    def test_on_hold_the_helper_refuses_and_lead_is_told_not_to_propose(self) -> None:
        from scripts import opentag_agent
        with on_hold(), self.env("tag-a"):
            with self.assertRaisesRegex(LeadRefused, "Creating new Tags is on hold"):
                tag_lead.record_proposal(PROPOSAL)
            text = opentag_agent.build_instructions(skill_dir=Path("/skill"), workdir=self.root, channel_id="C123")
        self.assertFalse(self.requests.exists())
        self.assertIn("Creating new Tags is on hold", text)
        self.assertIn("route --to", text)
        self.assertNotIn("propose --name", text)


class LeadApp:
    """A FakeApp that also keeps action handlers, so tests can press buttons."""

    def __init__(self) -> None:
        self.events: dict[str, Any] = {}
        self.actions: dict[str, Any] = {}

    def event(self, name: str):
        def register(handler: Any) -> Any:
            self.events[name] = handler
            return handler
        return register

    def action(self, name: Any):
        def register(handler: Any) -> Any:
            if isinstance(name, str):
                self.actions[name] = handler
            return handler
        return register

    def view(self, _name: str):
        return lambda handler: handler


class LeadBridgeTests(unittest.TestCase):
    """Three Tags on one fake Slack: Tag A is Lead, Tag B and Tag C are lanes."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {
            "TAG_HOME": str(self.root), "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_IDS": "C123",
            "OPENTAG_SLACK_STREAMING": "0", "OPENTAG_CLAUDE_TRANSPORT": "print",
            "OPENTAG_MEMORY_ROOT": str(self.root / "memory"),
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        tag_lead.set_lead(self.root, "tag-a")
        self.ids = {TAG_A: "tag-a", TAG_B: "tag-b", TAG_C: "tag-c"}
        names = {TAG_A: "Tag A", TAG_B: "Tag B", TAG_C: "Tag C"}
        for user, tag_id in self.ids.items():
            tag_lead.save_card(self.root, tag_id, user_id=user, team="T123", name=names[user],
                                description=f"{names[user]}'s lane.", channels=[{"id": "C123", "name": "launch"}])
        self.bus = SlackBus()
        self.runs: list[tuple[str, str, dict[str, Any]]] = []
        self.created: list[dict[str, Any]] = []
        self.outcome = tag_creator.Outcome("created", "t123-a9", "Created *Finance Tag*.", notes=["It is running."])
        self.script: dict[str, Any] = {}
        backend = patch.object(slack_socket_agent, "run_backend", side_effect=self.fake_backend)
        backend.start()
        self.addCleanup(backend.stop)
        busy = patch.object(slack_socket_agent, "reserve_thread", return_value=True)
        busy.start()
        self.addCleanup(busy.stop)
        self.bridges: dict[str, LeadBridge] = {}
        for user, tag_id in self.ids.items():
            app = LeadApp()
            self.bus.apps[user] = app
            self.bus.client(user)
            self.bridges[user] = LeadBridge(self.root, tag_id, self.root / tag_id, create=self.fake_create)
            with patch.object(slack_socket_agent, "App", return_value=app):
                slack_socket_agent.create_app(
                    "claude", 30, frozenset({MAYA}), peers=[],
                    handoff_store=HandoffStore(self.root / tag_id / "handoffs"), lead=self.bridges[user],
                )

    def fake_create(self, record: dict[str, Any], *, on_tag: Any) -> tag_creator.Outcome:
        self.created.append(record)
        on_tag(self.outcome.tag)
        return self.outcome

    def fake_backend(self, _backend: str, channel: str, caller: str, question: str, thread_text: str,
                     *_args: Any, **kwargs: Any) -> tuple[str, bool]:
        tag = getattr(self.bus.current, "tag", TAG_A)
        self.runs.append((tag, question, kwargs))
        action = self.script.get(tag)
        if action and kwargs.get("handoff_requests") is not None and kwargs["handoff_depth"] == 0:
            with patch.dict(os.environ, {"TAG_ID": self.ids[tag], "OPENTAG_HANDOFF_DEPTH": "0",
                                         "OPENTAG_HANDOFF_REQUESTS": str(kwargs["handoff_requests"])}):
                return action(kwargs)
        return f"{self.ids[tag]} answer", True

    def settle(self) -> None:
        self.bus.settle()
        for thread in threading.enumerate():
            if thread.name.startswith("tag-create-"):
                thread.join(timeout=10)
        self.bus.settle()

    def press(self, action_id: str, proposal_id: str, user: str = MAYA) -> None:
        body = {"user": {"id": user}, "channel": {"id": "C123"},
                "actions": [{"action_id": action_id, "value": json.dumps({"proposal": proposal_id})}]}
        self.bus.apps[TAG_A].actions[action_id](MagicMock(), body, self.bus.clients[TAG_A], MagicMock())
        self.settle()

    def test_lead_routes_one_request_to_one_owner_who_answers_in_the_thread(self) -> None:
        self.script[TAG_A] = lambda _kwargs: (
            tag_handoff.record_route("Tag B", "Tag B owns revenue questions."),
            ("Tag B will take this: it owns revenue questions.", True))[1]
        origin = self.bus.human_mention(TAG_A, f"<@{TAG_A}> what was Acme's Q3 revenue?")
        self.settle()

        thread = self.bus.thread(origin["ts"])
        route = next(m for m in thread if tag_handoff.ROUTE_RE.search(m["text"]))
        self.assertEqual(TAG_A, route["user"])
        self.assertTrue(route["text"].startswith(f"<@{TAG_B}> Tag B owns revenue questions."))
        self.assertIn(f"for <@{MAYA}>", route["text"])
        # The routing decision is reported in the thread, then the owner answers there itself.
        decision = next(m for m in thread if "Tag B will take this" in m["text"])
        answer = next(m for m in thread if m["text"] == "tag-b answer")
        self.assertEqual(TAG_B, answer["user"])
        self.assertLess(thread.index(decision), thread.index(answer))
        self.assertEqual([TAG_A, TAG_B], sorted({run[0] for run in self.runs}))
        owner_run = next(run for run in self.runs if run[0] == TAG_B)
        self.assertIn("routed the request in this thread to you because: Tag B owns revenue questions.", owner_run[1])
        self.assertEqual(0, owner_run[2]["handoff_depth"], "the owner may still escalate once")
        # Lead never writes after the owner, never combines, and Tag C never hears of it.
        self.assertEqual(1, sum(1 for run in self.runs if run[0] == TAG_A))
        self.assertFalse([m for m in self.bus.messages if tag_handoff.REQUEST_RE.search(m["text"])])
        self.assertEqual(thread[-1], answer)
        self.assertEqual("tag-b", self.bridges[TAG_A].route_for_thread("C123", origin["ts"])["owner"])

    def test_lead_mentioned_again_in_a_routed_thread_knows_who_owns_it(self) -> None:
        self.script[TAG_A] = lambda _kwargs: (
            tag_handoff.record_route("Tag B", "Tag B owns revenue questions."), ("Routing to Tag B.", True))[1]
        origin = self.bus.human_mention(TAG_A, f"<@{TAG_A}> what was Acme's Q3 revenue?")
        self.settle()
        self.script.clear()
        with self.bus.lock:
            follow_up = {"channel": "C123", "ts": self.bus.next_ts(), "thread_ts": origin["ts"], "user": MAYA,
                         "text": f"<@{TAG_A}> and Q4?"}
            self.bus.messages.append(follow_up)
        self.bus.deliver(TAG_A, {"type": "app_mention", **follow_up})
        self.settle()
        last = [run for run in self.runs if run[0] == TAG_A][-1]
        self.assertIn("you routed the request to Tag B, which owns it", last[1])

    def test_only_lead_routes_are_accepted_by_an_owner(self) -> None:
        text = tag_handoff.route_text("r-0123456789", {"user_id": TAG_B}, "Take it.", MAYA)
        self.bus.clients[TAG_C].chat_postMessage(channel="C123", thread_ts="1700000000.000001", text=text)
        self.settle()
        self.assertEqual([], self.runs, "a route from a Tag that isn't Lead must be ignored")

    def test_a_route_to_a_tag_outside_this_channel_asks_for_an_invitation(self) -> None:
        tag_lead.save_card(self.root, "tag-b", user_id=TAG_B, team="T123", name="Tag B",
                            channels=[{"id": "C999", "name": "finance"}])
        self.script[TAG_A] = lambda _kwargs: (
            tag_handoff.record_route("Tag B", "It owns revenue."), ("Routing.", True))[1]
        origin = self.bus.human_mention(TAG_A, f"<@{TAG_A}> revenue?")
        self.settle()
        texts = [m["text"] for m in self.bus.thread(origin["ts"])]
        self.assertTrue(any("isn't in this channel. Invite it with /invite" in text for text in texts))
        self.assertFalse(any(tag_handoff.ROUTE_RE.search(text) for text in texts))

    def propose(self) -> dict[str, Any]:
        self.script[TAG_A] = lambda _kwargs: (tag_lead.record_proposal(PROPOSAL), ("I'd like to propose a Tag.", True))[1]
        origin = self.bus.human_mention(TAG_A, f"<@{TAG_A}> we need someone for finance")
        self.settle()
        records = [item for item in self.bridges[TAG_A].proposals.all() if item["state"] == "pending"]
        self.assertEqual(1, len(records))
        record = records[0]
        card = next(m for m in self.bus.thread(origin["ts"]) if m["text"].startswith("Proposed new Tag"))
        self.assertEqual(card["ts"], record["card_ts"])
        return record

    def test_a_tag_that_is_not_lead_cannot_create_tags(self) -> None:
        # Tag B's agent writes a proposal straight into its request file, bypassing the helper's check.
        def bypass(kwargs: dict[str, Any]) -> tuple[str, bool]:
            Path(kwargs["handoff_requests"]).write_text(json.dumps({"kind": "propose", **PROPOSAL}) + "\n", encoding="utf-8")
            return "Proposing a finance Tag.", True
        self.script[TAG_B] = bypass
        origin = self.bus.human_mention(TAG_B, f"<@{TAG_B}> make a finance Tag")
        self.settle()
        self.assertEqual([], self.bridges[TAG_A].proposals.all())
        self.assertEqual([], self.created)
        texts = [m["text"] for m in self.bus.thread(origin["ts"])]
        self.assertIn("Only Lead can create Tags. Nothing was created; ask Lead.", texts)
        # The creator refuses too, even when handed a record directly.
        record = {"lead": "tag-b", "proposal": PROPOSAL, "tag": None}
        with self.assertRaisesRegex(LeadRefused, "Only Lead can create Tags"):
            tag_creator.create(self.root, record, lead_values={}, on_tag=lambda _tag: None,
                               run=lambda *args, **kwargs: self.fail("setup must not run"))

    def test_on_hold_a_proposal_written_past_the_helper_is_refused_and_nothing_is_created(self) -> None:
        def bypass(kwargs: dict[str, Any]) -> tuple[str, bool]:
            Path(kwargs["handoff_requests"]).write_text(json.dumps({"kind": "propose", **PROPOSAL}) + "\n", encoding="utf-8")
            return "Proposing a finance Tag.", True
        self.script[TAG_A] = bypass
        with on_hold():
            origin = self.bus.human_mention(TAG_A, f"<@{TAG_A}> make a finance Tag")
            self.settle()
            self.assertEqual([], self.bridges[TAG_A].proposals.all())
            # An approved proposal saved before the hold creates nothing either.
            with self.assertRaisesRegex(LeadRefused, "on hold"):
                self.bridges[TAG_A].create({"lead": "tag-a", "proposal": PROPOSAL, "tag": None}, on_tag=lambda _tag: None)
        self.assertEqual([], self.created)
        texts = [m["text"] for m in self.bus.thread(origin["ts"])]
        self.assertIn(f"I can't propose that Tag: {tag_lead.ON_HOLD_TEXT}", texts)

    def test_a_proposal_from_a_former_lead_creates_nothing(self) -> None:
        record = self.propose()
        tag_lead.set_lead(self.root, "tag-c")
        self.press(APPROVE, record["id"])
        self.assertEqual([], self.created)
        self.assertEqual("refused", self.bridges[TAG_A].proposals.get(record["id"])["state"])

    def test_approval_by_the_requester_creates_the_tag_and_reports_in_the_thread(self) -> None:
        record = self.propose()
        self.press(APPROVE, record["id"], user=OTHER_PERSON)
        self.assertEqual([], self.created, "only the person who asked may approve")
        self.press(APPROVE, record["id"])
        self.press(APPROVE, record["id"])  # A second press doesn't create a second Tag.
        self.assertEqual(1, len(self.created))
        saved = self.bridges[TAG_A].proposals.get(record["id"])
        self.assertEqual(("created", "t123-a9"), (saved["state"], saved["tag"]))
        texts = [m["text"] for m in self.bus.thread(record["thread_ts"])]
        self.assertIn("Created *Finance Tag*. It is running.", texts)

    def test_a_rejected_or_expired_proposal_creates_nothing_in_slack(self) -> None:
        record = self.propose()
        self.press(REJECT, record["id"])
        self.press(APPROVE, record["id"])
        self.assertEqual([], self.created)
        self.assertEqual("rejected", self.bridges[TAG_A].proposals.get(record["id"])["state"])

        second = self.propose()
        expired = self.bridges[TAG_A].proposals.claim_expired(now=time.time() + 61 * 60)
        self.assertEqual([second["id"]], [item["id"] for item in expired])
        self.press(APPROVE, second["id"])
        self.assertEqual([], self.created)
        self.assertEqual("expired", self.bridges[TAG_A].proposals.get(second["id"])["state"])

    def test_a_step_only_a_person_can_take_is_posted_and_resumes_with_check_again(self) -> None:
        self.outcome = tag_creator.Outcome("waiting_on_slack", "new-tag", "Slack needs a workspace admin.",
                                           "app_install")
        record = self.propose()
        self.press(APPROVE, record["id"])
        self.assertEqual("waiting_on_slack", self.bridges[TAG_A].proposals.get(record["id"])["state"])
        step = next(m for m in self.bus.thread(record["thread_ts"]) if m["text"] == "Slack needs a workspace admin.")
        self.assertEqual(TAG_A, step["user"])
        self.outcome = tag_creator.Outcome("created", "t123-a9", "Created *Finance Tag*.")
        self.press(CHECK, record["id"])
        self.assertEqual(2, len(self.created))
        self.assertEqual("new-tag", self.created[1]["tag"], "Check again continues the same Tag")
        self.assertEqual("created", self.bridges[TAG_A].proposals.get(record["id"])["state"])

    def test_after_a_restart_an_unfinished_creation_waits_for_check_again_once(self) -> None:
        record = self.propose()
        self.bridges[TAG_A].proposals.transition(record["id"], {"pending"}, "creating")
        # The bridge's 30-second deadline scan, as its main loop runs it.
        handler = self.bus.apps[TAG_A].tag_expire_handoffs
        handler(self.bus.clients[TAG_A], MagicMock())
        self.assertEqual("waiting_on_slack", self.bridges[TAG_A].proposals.get(record["id"])["state"])
        self.bridges[TAG_A].proposals.transition(record["id"], {"waiting_on_slack"}, "creating")
        handler(self.bus.clients[TAG_A], MagicMock())
        self.assertEqual("creating", self.bridges[TAG_A].proposals.get(record["id"])["state"],
                         "a creation running after startup is never marked interrupted")
        texts = [m["text"] for m in self.bus.thread(record["thread_ts"])]
        self.assertEqual(1, sum("Tag restarted while creating Finance Tag" in text for text in texts))

    def test_limits_refuse_a_proposal_before_anything_is_posted_for_approval(self) -> None:
        self.propose()
        self.script[TAG_A] = lambda _kwargs: (tag_lead.record_proposal({**PROPOSAL, "name": "Sales Tag"}),
                                              ("Another one.", True))[1]
        origin = self.bus.human_mention(TAG_A, f"<@{TAG_A}> and sales")
        self.settle()
        self.assertEqual(1, len(self.bridges[TAG_A].proposals.all()))
        texts = [m["text"] for m in self.bus.thread(origin["ts"])]
        self.assertTrue(any("still waiting for approval" in text for text in texts))


class LimitTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tag_lead.set_lead(self.root, "tag-a")

    def check(self, **changes: Any) -> None:
        arguments = {"lead": "tag-a", "channel": "C123", "allowed_channels": {"C123", "C456"},
                     "existing_names": ["Tag B"], "tag_count": 3, "max_tags": 10, **changes}
        proposal = arguments.pop("proposal", tag_lead.validate_proposal(PROPOSAL))
        tag_lead.check_limits(self.root, proposal, **arguments)

    def test_limits(self) -> None:
        self.check()
        with self.assertRaisesRegex(LeadRefused, "most Lead may create"):
            self.check(tag_count=10)
        with self.assertRaisesRegex(LeadRefused, "already exists"):
            self.check(existing_names=["finance tag"])
        with self.assertRaisesRegex(LeadRefused, "only join channels Lead is in"):
            self.check(proposal={**tag_lead.validate_proposal(PROPOSAL), "channels": ["C999"]})
        with self.assertRaisesRegex(LeadRefused, "Only Lead"):
            self.check(lead="tag-b")


FAKE_SETUP = textwrap.dedent("""
    import json, sys
    def emit(event):
        print(json.dumps(event), flush=True)
    def ask(event):
        emit({"type": "question", **event})
        line = sys.stdin.readline()
        reply = json.loads(line) if line else {"pause": True}
        with open(sys.argv[1], "a") as log:
            log.write(json.dumps({"id": event["id"], "reply": reply}) + "\\n")
        if reply.get("pause"):
            emit({"type": "result", "status": "paused", "tag": "new-tag", "exit_code": 0})
            sys.exit(0)
        return reply["answer"]
    resume = sys.argv[2] == "resume"
    if not resume:
        emit({"type": "tag", "tag": "new-tag"})
        print("not json")
        ask({"id": "profile", "kind": "profile_picture", "prompt": "Your Tag"})
        ask({"id": "default_model", "kind": "choose", "prompt": "Model", "option_ids": ["codex:gpt-5.5"]})
        ask({"id": "workspace", "kind": "choose", "prompt": "Which workspace?", "option_ids": ["T123", "sign_in", "exit"]})
        ask({"id": "approve_setup", "kind": "choose", "prompt": "Create?", "option_ids": ["create", "edit"]})
        emit({"type": "message", "text": "token xoxb-123-secret was used"})
    ask({"id": "app_install", "kind": "choose", "prompt": "Finish app installation",
         "options": ["Check approval again", "Continue installation", "Save and exit"]})
    ask({"id": "channels", "kind": "multi", "prompt": "Where?", "channels": [{"id": "C999"}, {"id": "C123"}]})
    emit({"type": "result", "status": "complete", "tag": "t123-a9", "exit_code": 0})
""")


class SetupDriverTests(unittest.TestCase):
    """The driver against a real child process that speaks setup's JSON lines."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.script = self.folder / "fake_setup.py"
        self.script.write_text(FAKE_SETUP, encoding="utf-8")
        self.log = self.folder / "answers.jsonl"

    def run_setup(self, mode: str, answers: tag_creator.Answers) -> tuple[dict[str, Any], list[str]]:
        seen: list[str] = []
        result = tag_creator.drive([sys.executable, str(self.script), str(self.log), mode], dict(os.environ),
                                   answers, on_tag=seen.append, timeout=60)
        return result, seen

    def replies(self) -> dict[str, Any]:
        return {item["id"]: item["reply"] for item in map(json.loads, self.log.read_text(encoding="utf-8").splitlines())}

    def test_driver_answers_from_the_proposal_and_pauses_for_admin_approval(self) -> None:
        answers = tag_creator.Answers(PROPOSAL, sign_in="T123", model="codex:gpt-5.5", resume=False)
        result, seen = self.run_setup("first", answers)
        self.assertEqual("paused", result["status"])
        self.assertEqual(["new-tag"], seen)
        self.assertEqual("app_install", answers.step)
        replies = self.replies()
        self.assertEqual({"answer": {"name": "Finance Tag", "description": PROPOSAL["description"]}}, replies["profile"])
        self.assertEqual({"answer": {"value": "codex:gpt-5.5", "effort": "default"}}, replies["default_model"])
        self.assertEqual({"answer": "T123"}, replies["workspace"])
        self.assertEqual({"answer": "create"}, replies["approve_setup"])
        self.assertEqual({"answer": None, "pause": True}, replies["app_install"])
        self.assertNotIn("xoxb-123-secret", json.dumps(result))
        message = tag_creator.step_message(answers.step, PROPOSAL, "new-tag")
        self.assertIn("Slack needs a workspace admin to approve installing *Finance Tag*", message)

    def test_check_again_resumes_and_finishes_the_same_setup(self) -> None:
        answers = tag_creator.Answers(PROPOSAL, sign_in="T123", model="codex:gpt-5.5", resume=True)
        result, _seen = self.run_setup("resume", answers)
        self.assertEqual(("complete", "t123-a9"), (result["status"], result["tag"]))
        replies = self.replies()
        self.assertEqual({"answer": 0}, replies["app_install"])
        self.assertEqual({"answer": [1]}, replies["channels"], "only the approved channels are chosen")

    def test_a_workspace_the_service_account_isnt_signed_in_to_needs_a_person(self) -> None:
        answers = tag_creator.Answers(PROPOSAL, sign_in="T999", model="codex:gpt-5.5", resume=False)
        result, _seen = self.run_setup("first", answers)
        self.assertEqual(("paused", "workspace"), (result["status"], answers.step))
        self.assertNotIn("approve_setup", self.replies(), "nothing is created in Slack")


class CreateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tag_lead.set_lead(self.root, "tag-a")
        self.record = {"lead": "tag-a", "proposal": PROPOSAL, "tag": None}
        self.values = {"OPENTAG_LEAD_SLACK_SIGN_IN": "T123", "SLACK_ALLOWED_USER_IDS": MAYA,
                       "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5"}

    def test_create_uses_the_service_accounts_folder_and_presets_owners(self) -> None:
        seen: dict[str, Any] = {}

        def run(command: list[str], environment: dict[str, str], _answers: Any, *, on_tag: Any) -> dict[str, Any]:
            seen["command"], seen["environment"] = command, environment
            seen["preset"] = json.loads(Path(command[command.index("--preset") + 1]).read_text(encoding="utf-8"))
            on_tag("new-tag")
            return {"status": "complete", "tag": "t123-a9"}
        finished: list[str] = []
        outcome = tag_creator.create(self.root, self.record, lead_values=self.values, on_tag=lambda _t: None,
                                     run=run, finish=lambda _root, tag, _p: finished.append(tag) or ["Started."])
        self.assertEqual(("created", "t123-a9"), (outcome.state, outcome.tag))
        self.assertEqual(["t123-a9"], finished)
        self.assertEqual(["add", "--json", "--preset"], seen["command"][2:5])
        folder = str(self.root / "shared/slack/service-account")
        self.assertEqual(folder, seen["environment"][slack_cli_config.CONFIG_DIR_ENV])
        self.assertFalse([key for key in seen["environment"] if key.startswith(("SLACK_", "OPENTAG_"))])
        self.assertEqual(MAYA, seen["preset"]["SLACK_ALLOWED_USER_IDS"])
        self.assertEqual(folder, seen["preset"]["OPENTAG_SLACK_CLI_CONFIG"])

    def test_without_a_service_account_nothing_runs_and_the_step_is_posted(self) -> None:
        outcome = tag_creator.create(self.root, self.record, lead_values={}, on_tag=lambda _t: None,
                                     run=lambda *a, **k: self.fail("setup must not run"),
                                     fallback=tag_creator.ConfigTokenFallback(self.root / "none"))
        self.assertEqual(("waiting_on_slack", "service_account"), (outcome.state, outcome.step))
        self.assertIn("tag lead sign-in", outcome.message)

    def test_finish_records_starts_and_hands_over_routines(self) -> None:
        tag_instances.create(self.root, "t123-a9")
        calls: list[list[str]] = []
        notes = tag_creator.finish_tag(self.root, "t123-a9", {**PROPOSAL, "requester": MAYA, "routines": [
            {"schedule": "weekdays 09:00", "prompt": "Post the open invoices"}]},
            call=lambda command: calls.append(command) or MagicMock(returncode=0))
        self.assertEqual(["t123-a9"], tag_lead.created_by_lead(self.root))
        self.assertEqual([["autostart", "keep", "t123-a9", "--json"], ["t123-a9", "start", "--json"]],
                         [command[2:] for command in calls])
        saved = json.loads((tag_instances.resolve(self.root, "t123-a9").home / tag_creator.ROUTINES_FILE).read_text(encoding="utf-8"))
        # Each entry is exactly what `tag NAME routine add --from-json` takes.
        self.assertEqual([{"name": "Finance Tag routine 1", "schedule": "weekdays 09:00",
                           "prompt": "Post the open invoices", "channel": "C123", "owner": MAYA}], saved["routines"])
        self.assertEqual(MAYA, saved["approved_by"])
        self.assertIn("1 starting routine(s) are saved", notes[0])


class FallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.now = 1_000_000.0

    def api(self, method: str, token: str, parameters: dict[str, str]) -> dict[str, Any]:
        self.calls.append((method, token, parameters))
        if method == "tooling.tokens.rotate":
            return {"ok": True, "token": "xoxe.xoxp-new", "refresh_token": "xoxe-1-new", "exp": self.now + 43200}
        return {"ok": True, "app_id": "A0FIN", "oauth_authorize_url": "https://slack.com/oauth/v2/authorize?client_id=1"}

    def test_token_rotates_before_use_and_saves_the_new_pair_first(self) -> None:
        fallback = tag_creator.ConfigTokenFallback(self.root, api=self.api, clock=lambda: self.now)
        fallback.save("xoxe.xoxp-old", "xoxe-1-old", self.now + 60)
        self.assertEqual("xoxe.xoxp-new", fallback.token())
        self.assertEqual([("tooling.tokens.rotate", "", {"refresh_token": "xoxe-1-old"})], self.calls)
        self.assertEqual("xoxe-1-new", json.loads(fallback.path.read_text(encoding="utf-8"))["refresh_token"])
        self.assertEqual("xoxe.xoxp-new", fallback.token())
        self.assertEqual(1, len(self.calls), "a fresh token is not rotated again")

    def test_fallback_creates_the_app_and_asks_a_person_to_install_it_outside_slack(self) -> None:
        fallback = tag_creator.ConfigTokenFallback(self.root / "sa", api=self.api, clock=lambda: self.now)
        fallback.save("xoxe.xoxp-old", "xoxe-1-old", self.now + 3600)
        tag_lead.set_lead(self.root, "tag-a")
        seen: list[str] = []
        outcome = tag_creator.create(self.root, {"lead": "tag-a", "proposal": PROPOSAL, "tag": None},
                                     lead_values={"SLACK_TEAM_ID": "T123", "SLACK_ALLOWED_USER_IDS": MAYA},
                                     on_tag=seen.append, fallback=fallback)
        self.assertEqual(("waiting_on_slack", "manual_install"), (outcome.state, outcome.step))
        manifest = json.loads(self.calls[-1][2]["manifest"])
        self.assertEqual("Finance Tag", manifest["display_information"]["name"])
        self.assertIn("<https://slack.com/oauth/v2/authorize?client_id=1|install it>", outcome.message)
        self.assertIn("Never paste tokens in Slack", outcome.message)
        settings = json.loads((tag_instances.resolve(self.root, seen[0]).home / "config/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(("A0FIN", MAYA), (settings["SLACK_APP_ID"], settings["SLACK_ALLOWED_USER_IDS"]))


if __name__ == "__main__":
    unittest.main()
