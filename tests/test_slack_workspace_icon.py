from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import slack_workspace_icon as icons
from scripts import tag_cli, tag_config, tag_instances


def team(**icon) -> dict:
    return {"ok": True, "team": {"id": "T1", "name": "Klovr", "icon": icon}}


CUSTOM = {"image_34": "https://avatars.slack-edge.com/k-34.png", "image_132": "https://avatars.slack-edge.com/k-132.png"}


class WorkspaceIconTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "home"
        (self.home / "state").mkdir(parents=True)

    def test_older_installation_saves_the_largest_useful_icon(self):
        api = Mock(return_value=team(**CUSTOM))
        fetch = Mock(return_value=(b"\x89PNG icon", ".png"))
        self.assertIsNone(icons.path(self.home))
        self.assertEqual("saved", icons.refresh(self.home, "xoxb-1", "T1", api=api, fetch=fetch))
        api.assert_called_once_with("xoxb-1", "team.info", {"team": "T1"})
        fetch.assert_called_once_with(CUSTOM["image_132"])
        self.assertEqual(self.home / "state/workspace-icon.png", icons.path(self.home))
        self.assertEqual(b"\x89PNG icon", icons.path(self.home).read_bytes())

    def test_repeated_runs_do_not_download_an_unchanged_icon(self):
        fetch = Mock(return_value=(b"icon", ".png"))
        api = Mock(return_value=team(**CUSTOM))
        icons.refresh(self.home, "xoxb-1", "T1", api=api, fetch=fetch)
        self.assertEqual("current", icons.refresh(self.home, "xoxb-1", "T1", api=api, fetch=fetch))
        self.assertEqual(1, fetch.call_count)

    def test_a_changed_icon_replaces_the_old_file_even_in_another_format(self):
        icons.refresh(self.home, "x", "T1", api=Mock(return_value=team(**CUSTOM)), fetch=Mock(return_value=(b"png", ".png")))
        changed = team(image_132="https://avatars.slack-edge.com/new-132.jpg")
        self.assertEqual("saved", icons.refresh(self.home, "x", "T1", api=Mock(return_value=changed),
                                                fetch=Mock(return_value=(b"jpg", ".jpg"))))
        self.assertEqual(self.home / "state/workspace-icon.jpg", icons.path(self.home))
        self.assertFalse((self.home / "state/workspace-icon.png").exists())

    def test_slacks_default_icon_removes_the_saved_copy(self):
        icons.refresh(self.home, "x", "T1", api=Mock(return_value=team(**CUSTOM)), fetch=Mock(return_value=(b"png", ".png")))
        default = team(image_default=True, image_132="https://a.slack-edge.com/default.png")
        fetch = Mock()
        self.assertEqual("default", icons.refresh(self.home, "x", "T1", api=Mock(return_value=default), fetch=fetch))
        fetch.assert_not_called()
        self.assertIsNone(icons.path(self.home))
        self.assertEqual([], list((self.home / "state").glob("workspace-icon*")))

    def test_failures_keep_the_last_good_icon_and_can_be_retried(self):
        icons.refresh(self.home, "x", "T1", api=Mock(return_value=team(**CUSTOM)), fetch=Mock(return_value=(b"png", ".png")))
        changed = team(image_132="https://avatars.slack-edge.com/new.png")
        for api, fetch in (
            (Mock(side_effect=icons.slack_channels.SlackChannelError("offline")), Mock()),
            (Mock(return_value={"ok": True, "team": {}}), Mock()),
            (Mock(return_value=changed), Mock(side_effect=RuntimeError("the workspace icon couldn't be downloaded"))),
        ):
            with self.subTest(api=api), self.assertRaises(RuntimeError):
                icons.refresh(self.home, "x", "T1", api=api, fetch=fetch)
            self.assertEqual(b"png", icons.path(self.home).read_bytes())
        self.assertEqual("saved", icons.refresh(self.home, "x", "T1", api=Mock(return_value=changed),
                                                fetch=Mock(return_value=(b"new", ".png"))))
        self.assertEqual(b"new", icons.path(self.home).read_bytes())

    def test_missing_team_read_keeps_the_letter_without_failing(self):
        icons.refresh(self.home, "x", "T1", api=Mock(return_value=team(**CUSTOM)), fetch=Mock(return_value=(b"png", ".png")))
        denied = Mock(side_effect=icons.slack_channels.MissingScope("team.info", "team:read"))
        self.assertEqual("needs_permission", icons.refresh(self.home, "x", "T1", api=denied, fetch=Mock()))
        self.assertEqual(b"png", icons.path(self.home).read_bytes())  # A previously saved icon stays.

    def test_only_the_file_this_module_wrote_is_reported(self):
        (self.home / "state/workspace-icon.json").write_text(json.dumps({"file": "../../secret.png"}), encoding="utf-8")
        self.assertIsNone(icons.path(self.home))
        (self.home / "state/workspace-icon.json").write_text(json.dumps({"file": "workspace-icon.png"}), encoding="utf-8")
        self.assertIsNone(icons.path(self.home))  # recorded but missing

    def test_download_accepts_only_https_images_of_reasonable_size(self):
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            icons.download("http://avatars.slack-edge.com/k.png")

        def response(kind: str, body: bytes):
            reply = Mock()
            reply.headers.get_content_type.return_value = kind
            reply.read.side_effect = lambda limit: body[:limit]
            reply.__enter__ = Mock(return_value=reply)
            reply.__exit__ = Mock(return_value=False)
            return reply

        with patch.object(icons.urllib.request, "urlopen", return_value=response("image/png", b"png")):
            self.assertEqual((b"png", ".png"), icons.download("https://avatars.slack-edge.com/k.png"))
        with patch.object(icons.urllib.request, "urlopen", return_value=response("text/html", b"<html>")), \
                self.assertRaisesRegex(RuntimeError, "image"):
            icons.download("https://avatars.slack-edge.com/k.png")
        with patch.object(icons.urllib.request, "urlopen", return_value=response("image/png", b"x" * (icons.MAX_BYTES + 1))), \
                self.assertRaisesRegex(RuntimeError, "large"):
            icons.download("https://avatars.slack-edge.com/k.png")


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        environment = patch.dict(os.environ, {"TAG_HOME": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_tag_list_reports_the_saved_icon_or_null(self):
        home = tag_instances.create(self.root, "t1-a1").home
        tag_config.save_config(home / "config/settings.json", {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"})

        def listed() -> dict:
            with patch.object(sys, "argv", ["tag", "list", "--json"]), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0, tag_cli.main())
            return json.loads(output.getvalue())["tags"][0]

        self.assertIsNone(listed()["workspace_icon"])
        icons.refresh(home, "x", "T1", api=Mock(return_value=team(**CUSTOM)), fetch=Mock(return_value=(b"png", ".png")))
        self.assertEqual(str(home / "state/workspace-icon.png"), listed()["workspace_icon"])

    def test_refresh_never_raises_for_a_start_or_setup(self):
        home = self.root / "h"
        (home / "state").mkdir(parents=True)
        self.assertEqual("skipped", tag_cli._refresh_workspace_icon(home, {}))
        with patch.object(icons, "refresh", side_effect=RuntimeError("Slack didn't share the workspace details")):
            result = tag_cli._refresh_workspace_icon(home, {"SLACK_BOT_TOKEN": "xoxb", "SLACK_TEAM_ID": "T1"})
        self.assertEqual("unavailable: Slack didn't share the workspace details", result)


if __name__ == "__main__":
    unittest.main()
