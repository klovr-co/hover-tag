"""Tag profiles: Tags that answer through Chief-of-Tag's one Slack app (ADR 0012)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import tag_chief, tag_cli, tag_config, tag_control, tag_instances, tag_profiles
from test_tag_handoff import MAYA

CHIEF_VALUES = {
    "SLACK_APP_TOKEN": "xapp-chief", "SLACK_BOT_TOKEN": "xoxb-chief", "SLACK_TEAM_ID": "T123",
    "SLACK_APP_ID": "A123", "SLACK_ALLOWED_USER_IDS": MAYA, "SLACK_CHANNEL_IDS": "C123",
    "OPENTAG_BACKEND": "claude", "OPENTAG_DEFAULT_MODEL": "claude:sonnet", "MFS_URL": "http://127.0.0.1:13619",
    "MFS_ALLOWED_SCOPES": "slack://T123/C123", "MFS_SLACK_TOKEN": "xoxp-history", "OPENTAG_BOT_NAME": "Tag",
}


def write_settings(home: Path, values: dict[str, str]) -> None:
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config/settings.json").write_text(json.dumps(values), encoding="utf-8")


def make_profile(root: Path, tag_id: str, name: str, **values: str) -> Path:
    home = tag_instances.create(root, tag_id, profile=True).home
    write_settings(home, {"OPENTAG_BOT_NAME": name, **values})
    return home


class Installation(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        self.root.mkdir()
        self.chief_home = tag_instances.create(self.root, "chief").home
        write_settings(self.chief_home, CHIEF_VALUES)
        tag_chief.set_chief(self.root, "chief")
        self.preset = Path(temporary.name) / "preset.json"

    def cli(self, *arguments: str) -> tuple[int, str]:
        out = io.StringIO()
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}), patch.object(sys, "argv", ["tag", *arguments]), \
                redirect_stdout(out), patch.object(tag_cli.subprocess, "call") as call:
            code = tag_cli.main()
        call.assert_not_called()  # A profile never runs Slack setup.
        return code, out.getvalue()

    def add_profile(self, preset: dict[str, str]) -> tuple[int, list[dict[str, Any]]]:
        self.preset.write_text(json.dumps(preset), encoding="utf-8")
        raw = io.StringIO()
        with patch.dict(os.environ, {"TAG_HOME": str(self.root)}), \
                patch.object(sys, "argv", ["tag", "add", "--profile", "--json", "--preset", str(self.preset)]), \
                patch.object(sys, "__stdout__", raw), patch.object(sys, "stdout", sys.stdout), \
                patch.object(tag_cli.subprocess, "call") as call:
            code = tag_cli.main()
        call.assert_not_called()
        return code, [json.loads(line) for line in raw.getvalue().splitlines() if line.startswith("{")]


class ProfileInstanceTests(Installation):
    def test_add_profile_creates_a_tag_without_a_slack_app_or_tokens(self) -> None:
        code, events = self.add_profile({"OPENTAG_BOT_NAME": "Designer",
                                         "OPENTAG_BOT_DESCRIPTION": "Makes banners and slides.",
                                         "OPENTAG_PROFILE_ICON": "zara"})
        self.assertEqual(0, code)
        self.assertEqual({"type": "tag", "tag": "designer"}, events[0])
        self.assertEqual(("complete", "profile"), (events[-1]["status"], events[-1]["kind"]))
        home = tag_instances.resolve(self.root, "designer").home
        self.assertEqual("profile", tag_profiles.kind(home))
        settings = json.loads((home / "config/settings.json").read_text(encoding="utf-8"))
        self.assertEqual("Designer", settings["OPENTAG_BOT_NAME"])
        self.assertEqual("zara", settings["OPENTAG_PROFILE_ICON"])
        # Backend and memory choices start from Chief-of-Tag's; Slack credentials never do.
        self.assertEqual(("claude", "claude:sonnet", MAYA, "C123"), (
            settings["OPENTAG_BACKEND"], settings["OPENTAG_DEFAULT_MODEL"],
            settings["SLACK_ALLOWED_USER_IDS"], settings["SLACK_CHANNEL_IDS"]))
        for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_ID", "SLACK_TEAM_ID", "MFS_SLACK_TOKEN"):
            self.assertNotIn(key, settings)
        self.assertEqual({}, tag_config.config_errors(settings, profile=True))
        self.assertIn("SLACK_BOT_TOKEN", tag_config.config_errors(settings))

    def test_list_reports_the_kind_of_each_tag(self) -> None:
        make_profile(self.root, "designer", "Designer")
        code, out = self.cli("list", "--json")
        self.assertEqual(0, code)
        rows = {row["id"]: row for row in json.loads(out)["tags"]}
        self.assertEqual(("app", "profile"), (rows["chief"]["kind"], rows["designer"]["kind"]))
        self.assertTrue(rows["chief"]["chief_of_tag"])

    def test_add_profile_needs_a_connected_chief_and_a_unique_name(self) -> None:
        make_profile(self.root, "designer", "Designer")
        with self.assertRaisesRegex(ValueError, "already called 'designer'"):
            self.add_profile({"OPENTAG_BOT_NAME": "designer"})
        with self.assertRaisesRegex(ValueError, "needs --preset with OPENTAG_BOT_NAME"):
            self.add_profile({"OPENTAG_BOT_DESCRIPTION": "No name"})
        write_settings(self.chief_home, {**CHIEF_VALUES, "SLACK_BOT_TOKEN": ""})
        with self.assertRaisesRegex(ValueError, "isn't connected to Slack yet"):
            self.add_profile({"OPENTAG_BOT_NAME": "Ops"})
        self.assertEqual(["chief", "designer"], sorted(
            str(item["id"]) for item in tag_instances.discover(self.root) if Path(str(item["home"])).exists()))

    def test_add_profile_refuses_slack_tokens_in_the_preset(self) -> None:
        with self.assertRaisesRegex(ValueError, "--preset accepts only"):
            self.add_profile({"OPENTAG_BOT_NAME": "Ops", "SLACK_BOT_TOKEN": "xoxb-own"})

    def test_a_profile_cannot_save_slack_tokens_of_its_own(self) -> None:
        home = make_profile(self.root, "designer", "Designer")
        with self.assertRaisesRegex(ValueError, "a profile keeps no Slack tokens"):
            tag_control.config_command(home, ["set", "SLACK_BOT_TOKEN", "xoxb-own"], json_output=True,
                                       stdin=False, tag_id="designer")

    def test_a_profile_shares_only_the_bot_token_and_app_ids(self) -> None:
        values = tag_profiles.shared_slack_values(self.root)
        self.assertEqual({"SLACK_BOT_TOKEN": "xoxb-chief", "SLACK_TEAM_ID": "T123", "SLACK_APP_ID": "A123"}, values)
        tag_chief.set_chief(self.root, "designer")
        make_profile(self.root, "designer", "Designer")
        with self.assertRaisesRegex(ValueError, "must be a Tag with its own Slack app"):
            tag_profiles.shared_slack_values(self.root)

    def test_backend_credentials_stay_in_the_profiles_own_home(self) -> None:
        home = make_profile(self.root, "designer", "Designer")
        self.assertNotEqual(self.chief_home, home)
        self.assertFalse(home.is_relative_to(self.chief_home))
        self.assertEqual(tag_instances.resolve(self.root, "designer").workspace,
                         tag_instances.resolve(self.root, "designer").workspace)
        self.assertNotEqual(tag_instances.resolve(self.root, "chief").workspace,
                            tag_instances.resolve(self.root, "designer").workspace)


class PictureTests(unittest.TestCase):
    def test_built_in_characters_use_pinned_public_urls(self) -> None:
        url = tag_profiles.icon_url("Zara")
        self.assertTrue(url.startswith("https://raw.githubusercontent.com/klovr-co/hover-tag/"))
        self.assertIn(tag_profiles.ICON_COMMIT, url)
        self.assertTrue(url.endswith("/assets/characters/tag-zara-pixel.png"))
        self.assertEqual(tag_profiles.icon_url(""), tag_profiles.icon_url("maya"))

    def test_a_custom_picture_must_be_a_public_https_url(self) -> None:
        self.assertEqual("https://example.com/a.png", tag_profiles.icon_url("https://example.com/a.png"))
        for bad in ("http://example.com/a.png", "https://localhost/a.png", "https://10.0.0.2/a.png",
                    "https://user:pw@example.com/a.png", "file:///tmp/a.png", "https://box.local/a.png"):
            with self.subTest(bad=bad):
                self.assertIsNotNone(tag_profiles.icon_error(bad))
                self.assertIsNotNone(tag_config.validation_error("OPENTAG_PROFILE_ICON", bad))
                self.assertEqual(tag_profiles.icon_url(""), tag_profiles.icon_url(bad))


class CallTests(unittest.TestCase):
    def setUp(self) -> None:
        home = Path("/nonexistent")
        self.designer = tag_profiles.Profile("designer", home, "Designer", "", None, "")
        self.ops = tag_profiles.Profile("ops", home, "Ops", "", "deploys", "")
        self.ops_lead = tag_profiles.Profile("ops-lead", home, "Ops Lead", "", None, "")
        self.all = [self.designer, self.ops, self.ops_lead]

    def call(self, text: str) -> tuple[str, str] | None:
        found = tag_profiles.parse_call(text, self.all)
        return (found.profile.id, found.text) if found else None

    def test_a_name_first_followed_by_a_colon_or_comma_calls_that_profile(self) -> None:
        self.assertEqual(("designer", "make a banner"), self.call("<@UTAG> designer: make a banner"))
        self.assertEqual(("designer", "make a banner"), self.call("<@UTAG>  Designer, make a banner"))
        self.assertEqual(("designer", "make a banner"), self.call("<@UTAG> @designer: make a banner"))
        self.assertEqual(("ops", "check the deploy"), self.call("<@UTAG> deploys: check the deploy"))

    def test_ask_name_calls_that_profile(self) -> None:
        self.assertEqual(("ops", "check the deploy"), self.call("<@UTAG> ask Ops to check the deploy"))
        self.assertEqual(("ops", "whether it shipped"), self.call("<@UTAG> ask ops whether it shipped"))

    def test_the_longest_matching_name_wins(self) -> None:
        self.assertEqual(("ops-lead", "plan the rota"), self.call("<@UTAG> Ops Lead: plan the rota"))

    def test_a_name_anywhere_else_is_ordinary_text(self) -> None:
        for text in ("<@UTAG> ask the designer whether this works", "<@UTAG> can the designer: help",
                     "<@UTAG> opsy: hi", "<@UTAG> designer:", "<@UTAG> what does ops think?"):
            with self.subTest(text=text):
                self.assertIsNone(self.call(text))


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_threads_remember_their_owner(self) -> None:
        self.assertIsNone(tag_profiles.thread_owner(self.root, "T123", "C123", "1700000000.000100"))
        tag_profiles.set_thread_owner(self.root, "T123", "C123", "1700000000.000100", "designer", by="name")
        self.assertEqual("designer", tag_profiles.thread_owner(self.root, "T123", "C123", "1700000000.000100"))
        with self.assertRaises(ValueError):
            tag_profiles.thread_owner(self.root, "T123", "../C123", "1")

    def test_messages_remember_which_profile_posted_them(self) -> None:
        tag_profiles.record_message(self.root, "C123", "1700000000.000200", "designer")
        self.assertEqual("designer", tag_profiles.message_profile(self.root, "C123", "1700000000.000200"))
        self.assertIsNone(tag_profiles.message_profile(self.root, "C123", "1700000000.000300"))

    def test_the_inbox_hands_each_payload_over_once_and_drops_stale_ones(self) -> None:
        tag_profiles.deliver(self.root, {"kind": "slack", "body": {"n": 1}})
        tag_profiles.deliver(self.root, {"kind": "slack", "body": {"n": 2}})
        stale = tag_profiles.deliver(self.root, {"kind": "slack", "body": {"n": 3}})
        document = json.loads(stale.read_text(encoding="utf-8"))
        stale.write_text(json.dumps({**document, "created_at": time.time() - 3600}), encoding="utf-8")
        self.assertEqual([1, 2], [item["body"]["n"] for item in tag_profiles.claim(self.root)])
        self.assertEqual([], list(tag_profiles.claim(self.root)))
        self.assertEqual([], list(tag_profiles.inbox_path(self.root).iterdir()))


if __name__ == "__main__":
    unittest.main()
