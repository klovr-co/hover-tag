from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import opentag_setup, setup_session, setup_ui, tag_config

BACK = {"back": True}


class Client:
    """A client that can answer or press Back, as Tag.app does."""

    def __init__(self, replies: list[dict]):
        self.stdin = StringIO("".join(json.dumps(reply) + "\n" for reply in replies))
        self.stdout = StringIO()

    def __enter__(self):
        setup_ui._history.clear()
        setup_ui._replay.clear()
        setup_ui._target = None
        self.patches = [
            patch.dict(os.environ, {setup_ui.PROTOCOL_ENV: "jsonl"}),
            patch.object(sys, "stdin", self.stdin),
            patch.object(sys, "__stdout__", self.stdout),
            patch.object(sys, "stdout", sys.stdout),
        ]
        for item in self.patches:
            item.start()
        setup_ui.enter_protocol()
        return self

    def __exit__(self, *_):
        for item in reversed(self.patches):
            item.stop()

    def questions(self) -> list[dict]:
        events = [json.loads(line) for line in self.stdout.getvalue().splitlines()]
        return [event for event in events if event["type"] == "question"]


def answer(value: object) -> dict:
    return {"answer": value}


def flow() -> tuple[int, int]:
    """Two questions in a row, like workspace then who can use Tag."""
    first = setup_ui.choose("Choose a workspace", ["Klovr", "Acme"], qid="workspace")
    second = setup_ui.choose("Who can use Tag?", ["Me", "Someone else"], qid="allowed_user")
    return first, second


class SetupBackTests(unittest.TestCase):
    def test_first_question_cannot_go_back_and_later_ones_can(self):
        with Client([answer(1), answer(0)]) as client:
            flow()
        first, second = client.questions()
        self.assertFalse(first["can_go_back"])
        self.assertTrue(second["can_go_back"])

    def test_back_returns_to_the_previous_question_with_its_answer_preselected(self):
        with Client([answer("Acme"), BACK, answer("Klovr"), answer(0)]) as client:
            with self.assertRaises(setup_ui.GoBack) as raised:
                flow()
            self.assertEqual(raised.exception.target, ("workspace", "Acme"))
            setup_ui.start_replay(raised.exception.replay, raised.exception.target)
            self.assertEqual(flow(), (0, 0))
        asked_again = client.questions()[2]
        self.assertEqual(asked_again["id"], "workspace")
        self.assertEqual(asked_again["default"], 1)
        self.assertFalse(asked_again["can_go_back"])

    def test_earlier_answers_are_replayed_silently(self):
        def three():
            return (*flow(), setup_ui.checklist(["#general", "#launch"], set()))

        with Client([answer(0), answer(1), BACK, answer(0), answer(["#general"])]) as client:
            with self.assertRaises(setup_ui.GoBack) as raised:
                three()
            self.assertEqual(raised.exception.target, ("allowed_user", 1))
            setup_ui.start_replay(raised.exception.replay, raised.exception.target)
            self.assertEqual(three(), (0, 0, {0}))
        questions = client.questions()
        # The workspace answer is replayed without being shown again.
        self.assertEqual([q["id"] for q in questions], ["workspace", "allowed_user", "channels", "allowed_user", "channels"])
        self.assertEqual(questions[3]["default"], 1)
        self.assertTrue(questions[3]["can_go_back"])

    def test_replay_stops_when_the_flow_changes(self):
        with Client([answer(0)]) as client:
            setup_ui.start_replay([("workspace", 0)], ("allowed_user", 1))
            self.assertEqual(setup_ui.choose("Something new", ["A", "B"], qid="backend"), 0)
        self.assertEqual(client.questions()[0]["id"], "backend")
        self.assertEqual(setup_ui._replay, [])

    def test_points_of_no_return_end_the_history(self):
        with Client([answer(0), answer(0)]) as client:
            setup_ui.choose("Choose a workspace", ["Klovr"], qid="workspace")
            setup_ui.commit()  # For example, the Slack app was just created.
            setup_ui.choose("Who can use Tag?", ["Me"], qid="allowed_user")
        self.assertFalse(client.questions()[1]["can_go_back"])

    def test_back_with_nothing_to_return_to_asks_again(self):
        with Client([BACK, answer(0)]) as client:
            self.assertEqual(setup_ui.choose("Choose a workspace", ["Klovr"], qid="workspace"), 0)
        self.assertEqual(len(client.questions()), 2)

    def test_secrets_are_never_offered_back_as_defaults(self):
        self.assertEqual(setup_ui._previous("secret", "xoxb-private", {}), {})

    def test_going_back_clears_only_what_that_question_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "settings.json"
            tag_config.save_config(config, {"SLACK_TEAM_ID": "T1", "SLACK_ENTERPRISE_ID": "E1",
                                            "SLACK_ALLOWED_USER_IDS": "U1", "OPENTAG_BOT_NAME": "Maya's Tag"})
            calls = []

            def guided(path, **_):
                calls.append(tag_config.load_config(path))
                if len(calls) == 1:
                    raise setup_ui.GoBack([], ("workspace", 0))
                return 0

            with patch.object(sys, "argv", ["opentag_setup.py", "--config", str(config), "--review"]), patch.dict(
                os.environ, {setup_ui.PROTOCOL_ENV: "jsonl", "TAG_HOME": directory}
            ), patch.object(opentag_setup, "guided_setup", side_effect=guided), patch.object(
                opentag_setup.tag_telemetry, "SetupSession"
            ), patch.object(sys, "stdout", StringIO()):
                self.assertEqual(opentag_setup.main(), 0)
        self.assertEqual(calls[1], {"OPENTAG_BOT_NAME": "Maya's Tag"})
        self.assertEqual(setup_ui._target, ("workspace", 0))


    def test_every_v2_question_that_saves_something_clears_it_on_back(self):
        clears = opentag_setup.BACK_CLEARS
        for qid in ("workspace", "org_workspace", "org_workspace_id"):
            self.assertEqual(set(clears[qid]), {"SLACK_TEAM_ID", "SLACK_ENTERPRISE_ID", "SLACK_ALLOWED_USER_IDS"})
        self.assertEqual(clears["existing_app"], ("SLACK_APP_ID",))
        self.assertEqual(clears["app_id"], ("SLACK_APP_ID",))
        self.assertEqual(clears["channels"], ("SLACK_CHANNEL_IDS",))
        # The people picker is gone, and so are its entries.
        self.assertFalse({"allowed_user", "person", "people_search", "member_id"} & set(clears))

    def test_going_back_to_channels_asks_them_again_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "settings.json"
            tag_config.save_config(config, {"SLACK_CHANNEL_POLICY": "invited", "SLACK_CHANNEL_IDS": "C1"})
            opentag_setup.save_progress(config, channels_done=True, app_path="new")
            calls = []

            def guided(path, **_):
                calls.append(opentag_setup.setup_progress(path))
                if len(calls) == 1:
                    raise setup_ui.GoBack([], ("channels", ["#general"]))
                return 0

            with patch.object(sys, "argv", ["opentag_setup.py", "--config", str(config), "--review"]), patch.dict(
                os.environ, {setup_ui.PROTOCOL_ENV: "jsonl", "TAG_HOME": directory}
            ), patch.object(opentag_setup, "guided_setup", side_effect=guided), patch.object(
                opentag_setup.tag_telemetry, "SetupSession"
            ), patch.object(sys, "stdout", StringIO()):
                self.assertEqual(opentag_setup.main(), 0)
            self.assertNotIn("channels_done", calls[1])
            self.assertEqual(calls[1]["app_path"], "new")  # Paused setups keep their path.
            self.assertNotIn("SLACK_CHANNEL_IDS", tag_config.load_config(config))


class StepSessionBackTests(unittest.TestCase):
    def test_back_is_refused_unless_the_question_allows_it(self):
        session = setup_session._Supervisor.__new__(setup_session._Supervisor)
        session.condition = setup_session.threading.Condition()
        session.question = {"id": "workspace", "can_go_back": False}
        session.events = []
        session.last_contact = 0
        with self.assertRaises(setup_session.SessionError):
            session.handle({"op": "back"})


if __name__ == "__main__":
    unittest.main()
