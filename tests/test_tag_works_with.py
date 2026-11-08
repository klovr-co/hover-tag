"""Works with: the Tags and apps a Tag accepts tasks from, set from the CLI and Tag.app."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import slack_channels, tag_cli, tag_config, tag_control, tag_handoff, tag_instances, tag_works_with

TEAM = "T0KLOVR1"


def slack(bots: list[dict], *, own: str = "U0MAYA0TG", pages: int = 1):
    """A fake Slack API that lists ``bots`` (split over ``pages``) next to a person."""
    calls = []

    def api(token: str, method: str, parameters: dict[str, str]) -> dict:
        calls.append((method, dict(parameters)))
        if method == "auth.test":
            return {"ok": True, "user_id": own}
        assert method == "users.list"
        page = int(parameters.get("cursor", "0"))
        members = [{"id": "U0PERSON1", "name": "maya"}, {"id": "USLACKBOT", "is_bot": True, "name": "slackbot"},
                   {"id": own, "is_bot": True, "name": "maya-tag"}] if page == 0 else []
        members += bots[page::pages]
        return {"ok": True, "members": members,
                "response_metadata": {"next_cursor": str(page + 1) if page + 1 < pages else ""}}
    api.calls = calls
    return api


class WorksWithTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root = base / "Tag"
        empty_path = base / "bin"
        empty_path.mkdir()
        # Never reach real agent CLIs or the developer's Claude/Codex config.
        environment = patch.dict(os.environ, {
            "TAG_HOME": str(self.root), "PATH": str(empty_path), "HOME": str(base / "home"),
            "CLAUDE_CONFIG_DIR": str(base / "claude"), "CODEX_HOME": str(base / "codex")})
        environment.start()
        self.addCleanup(environment.stop)
        self.home = self.tag("t1-maya", "Maya's Tag", "U0MAYA0TG")
        self.research = self.tag("t1-research", "Research Tag", "U0RESRCH1")

    def tag(self, tag_id: str, name: str, user_id: str | None, team: str = TEAM, **extra: str) -> Path:
        home = tag_instances.create(self.root, tag_id).home
        tag_config.save_config(home / "config/settings.json", {
            "SLACK_TEAM_ID": team, "SLACK_APP_ID": "A" + user_id[1:] if user_id else "A1", "OPENTAG_BOT_NAME": name,
            "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_ALLOWED_USER_IDS": "U0PERSON1", **extra})
        if user_id:
            (home / "state").mkdir(parents=True, exist_ok=True)
            (home / "state/slack-avatar.json").write_text(json.dumps({"user_id": user_id}), encoding="utf-8")
        return home

    def values(self, home: Path | None = None) -> dict[str, str]:
        return tag_config.load_config((home or self.home) / "config/settings.json")

    def cli(self, *arguments: str) -> dict:
        with patch.object(sys, "argv", ["tag", *arguments, "--json"]), redirect_stdout(io.StringIO()) as output:
            code = tag_cli.main()
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0, result)
        return result

    def test_empty_by_default(self) -> None:
        self.assertEqual(tag_works_with.current(self.home, self.values()), {"works_with": [], "error": None})

    def test_candidates_are_my_tags_in_this_workspace_and_slack_bots(self) -> None:
        self.tag("t2-other", "Other Workspace Tag", "U0OTHERWS", team="T0ELSE001")
        self.tag("t1-new", "Never Started Tag", None)
        api = slack([{"id": "U0HOVER01", "is_bot": True, "profile": {"real_name": "Hover, by Klovr"}},
                     {"id": "U0RESRCH1", "is_bot": True, "name": "research-tag"},
                     {"id": "U0GONE001", "is_bot": True, "deleted": True, "name": "gone"}])
        found = tag_works_with.candidates(self.root, self.home, self.values(), api=api)
        self.assertIsNone(found["slack_error"])
        self.assertEqual(found["candidates"], [
            {"id": "U0RESRCH1", "name": "Research Tag", "kind": "tag", "tag": "t1-research"},
            {"id": "U0HOVER01", "name": "Hover by Klovr", "kind": "app"},
        ])
        self.assertIn(("users.list", {"limit": "200", "team_id": TEAM}), api.calls)

    def test_bots_are_read_across_pages(self) -> None:
        bots = [{"id": f"U0BOT000{n}", "is_bot": True, "name": f"bot {n}"} for n in range(4)]
        found = tag_works_with.workspace_bots(self.values(), api=slack(bots, pages=2))
        self.assertEqual([bot["id"] for bot in found], [f"U0BOT000{n}" for n in range(4)])

    def test_slack_failure_still_offers_my_tags(self) -> None:
        def down(*_):
            raise slack_channels.SlackChannelError("Slack is down")
        found = tag_works_with.candidates(self.root, self.home, self.values(), api=down)
        self.assertEqual([item["id"] for item in found["candidates"]], ["U0RESRCH1"])
        self.assertIn("Paste a member ID", found["slack_error"])

    def test_set_saves_my_tags_as_peers_and_everything_else_as_apps(self) -> None:
        tag_works_with.candidates(self.root, self.home, self.values(),
                                  api=slack([{"id": "U0HOVER01", "is_bot": True, "name": "Hover"}]))
        result = tag_works_with.edit(self.root, self.home, "set", ["U0RESRCH1", "U0HOVER01", "B0LEGACY1"])
        values = self.values()
        self.assertEqual(values["OPENTAG_PEER_TAGS"], "Research Tag=U0RESRCH1")
        self.assertEqual(values["SLACK_ALLOWED_BOT_IDS"], "U0HOVER01,B0LEGACY1")
        self.assertEqual(result["works_with"], [
            {"id": "U0RESRCH1", "name": "Research Tag", "kind": "tag"},
            {"id": "U0HOVER01", "name": "Hover", "kind": "app"},
            {"id": "B0LEGACY1", "name": None, "kind": "app"},
        ])
        # The bridge reads the same settings it always has.
        self.assertEqual(tag_handoff.parse_peers(values["OPENTAG_PEER_TAGS"]), [tag_handoff.Peer("Research Tag", "U0RESRCH1")])
        tag_works_with.edit(self.root, self.home, "set", [])
        self.assertEqual((self.values()["OPENTAG_PEER_TAGS"], self.values()["SLACK_ALLOWED_BOT_IDS"]), ("", ""))

    def test_someone_elses_tag_can_be_added_as_a_tag_with_a_name(self) -> None:
        tag_works_with.edit(self.root, self.home, "add", ["U0SAMSTAG"], kind="tag", name="Sam's Tag, v2")
        self.assertEqual(self.values()["OPENTAG_PEER_TAGS"], "Sam's Tag v2=U0SAMSTAG")
        with self.assertRaisesRegex(ValueError, "bot member ID"):
            tag_works_with.edit(self.root, self.home, "add", ["B0HOVER01"], kind="tag")

    def test_existing_values_are_kept_and_names_made_unique(self) -> None:
        tag_config.update_config(self.home / "config/settings.json", {
            "OPENTAG_PEER_TAGS": "Research=U0OLDPEER", "SLACK_ALLOWED_BOT_IDS": "B0HOVER01"})
        tag_works_with.edit(self.root, self.home, "add", ["U0NEWPEER"], kind="tag", name="research")
        self.assertEqual(self.values()["OPENTAG_PEER_TAGS"], "Research=U0OLDPEER,research 2=U0NEWPEER")
        self.assertEqual(self.values()["SLACK_ALLOWED_BOT_IDS"], "B0HOVER01")
        tag_works_with.edit(self.root, self.home, "remove", ["B0HOVER01"])
        self.assertEqual(self.values()["SLACK_ALLOWED_BOT_IDS"], "")

    def test_a_malformed_setting_is_reported_and_not_overwritten(self) -> None:
        settings_file = self.home / "config/settings.json"
        values = {**self.values(), "OPENTAG_PEER_TAGS": "Research Tag"}
        settings_file.write_text(json.dumps(values), encoding="utf-8")
        self.assertIn("Name=MEMBERID", tag_works_with.current(self.home, values)["error"])
        with self.assertRaisesRegex(ValueError, "Fix it"):
            tag_works_with.edit(self.root, self.home, "set", [])
        self.assertEqual(self.values()["OPENTAG_PEER_TAGS"], "Research Tag")

    def test_bad_ids_and_misused_options_are_refused(self) -> None:
        for action, ids, options in (("add", ["C0CHANNEL"], {}), ("add", [], {}), ("remove", ["U0NOTLIST"], {}),
                                     ("remove", ["U0RESRCH1"], {"kind": "tag"}), ("set", ["U0X"], {"name": "x"})):
            with self.subTest(action=action, ids=ids), self.assertRaises(ValueError):
                tag_works_with.edit(self.root, self.home, action, ids, **options)

    def test_cli_shows_lists_and_changes_the_list(self) -> None:
        with patch.object(tag_works_with.slack_channels, "slack_api",
                          slack([{"id": "U0HOVER01", "is_bot": True, "name": "Hover"}])):
            found = self.cli("t1-maya", "settings", "works-with", "candidates")
        self.assertEqual([item["id"] for item in found["candidates"]], ["U0RESRCH1", "U0HOVER01"])
        added = self.cli("t1-maya", "settings", "works-with", "add", "U0HOVER01")
        self.assertEqual(added["works_with"], [{"id": "U0HOVER01", "name": "Hover", "kind": "app"}])
        self.assertEqual((added["restart_required"], added["restarted"]), (False, False))
        changed = self.cli("t1-maya", "settings", "works-with", "set", "U0RESRCH1")
        self.assertEqual(changed["works_with"], [{"id": "U0RESRCH1", "name": "Research Tag", "kind": "tag"}])
        shown = self.cli("t1-maya", "settings", "works-with")
        self.assertEqual(shown["works_with"], changed["works_with"])
        self.assertIsNone(shown["error"])

    def test_cli_refuses_a_bad_id_without_saving(self) -> None:
        # tag_cli's entry point turns this error into {"ok": false, "error": …} for --json.
        with patch.object(sys, "argv", ["tag", "t1-maya", "settings", "works-with", "add", "nope", "--json"]), \
                redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "member IDs"):
            tag_cli.main()
        self.assertEqual(self.values().get("SLACK_ALLOWED_BOT_IDS", ""), "")

    def test_settings_menu_ticks_saved_entries_and_saves_the_choice(self) -> None:
        tag_config.update_config(self.home / "config/settings.json", {"SLACK_ALLOWED_BOT_IDS": "U0HOVER01"})
        asked = {}

        def checklist(labels, selected, **options):
            asked.update(labels=labels, selected=selected, **options)
            return {1}  # untick Hover, tick Research Tag
        with patch.object(tag_works_with.slack_channels, "slack_api", slack([{"id": "U0HOVER01", "is_bot": True, "name": "Hover"}])), \
                patch.object(tag_control.ui, "checklist", checklist), patch.object(tag_control.ui, "message"):
            tag_control._choose_works_with(self.home)
        self.assertEqual(asked["labels"], ["Hover (app)", "Research Tag (Tag)"])
        self.assertEqual((asked["selected"], asked["allow_empty"]), ({0}, True))
        self.assertEqual((self.values()["OPENTAG_PEER_TAGS"], self.values()["SLACK_ALLOWED_BOT_IDS"]),
                         ("Research Tag=U0RESRCH1", ""))

    def test_works_with_options_only_apply_to_works_with(self) -> None:
        for arguments in (["t1-maya", "settings", "works-with", "remove", "U0X", "--as", "tag"],
                          ["t1-maya", "status", "--as", "app"], ["t1-maya", "logs", "--restart"]):
            with self.subTest(arguments=arguments), patch.object(sys, "argv", ["tag", *arguments]), \
                    redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                tag_cli.main()


if __name__ == "__main__":
    unittest.main()
