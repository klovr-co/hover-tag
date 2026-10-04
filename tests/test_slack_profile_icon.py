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

from scripts import slack_profile_icon as icons
from scripts import tag_cli, tag_config, tag_instances


PNG = b"\x89PNG\r\n\x1a\nfixture"
VALUES = {"SLACK_BOT_TOKEN": "xoxb-fixture", "SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"}


def api_for(url="https://avatars.slack-edge.com/bot.png"):
    def api(token, method, parameters):
        if method == "auth.test":
            return {"ok": True, "team_id": "T1", "user_id": "U1", "bot_id": "B1"}
        return {"ok": True, "user": {"id": "U1", "is_bot": True, "profile": {
            "api_app_id": "A1", "image_192": url, "image_48": "https://avatars.slack-edge.com/small.png",
        }}}
    return Mock(side_effect=api)


class AvatarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.api = api_for()
        self.fetch = Mock(return_value=(PNG, ".png"))

    def refresh(self, **kwargs):
        return icons.refresh(self.home, VALUES, api=kwargs.get("api", self.api), fetch=kwargs.get("fetch", self.fetch))

    def test_legacy_installation_migrates_and_repeated_runs_reuse_the_file(self):
        setup = self.home / "integrations/slack-cli/assets/tag-profile.png"
        setup.parent.mkdir(parents=True)
        setup.write_bytes(b"old setup picture")
        self.assertEqual(str(setup), tag_cli._avatar(self.home, VALUES))
        self.assertEqual("saved", self.refresh())
        self.assertEqual("current", self.refresh())
        self.fetch.assert_called_once_with("https://avatars.slack-edge.com/bot.png")
        self.api.assert_any_call("xoxb-fixture", "users.info", {"user": "U1", "team_id": "T1"})
        self.assertEqual(PNG, icons.path(self.home).read_bytes())
        self.assertEqual(str(icons.path(self.home)), tag_cli._avatar(self.home, VALUES))
        self.assertEqual(b"old setup picture", setup.read_bytes())
        self.assertEqual(1, json.loads((self.home / icons.RECORD).read_text(encoding="utf-8"))["version"])

    def test_picture_changes_get_a_new_path_even_when_format_is_the_same(self):
        self.refresh()
        old = icons.path(self.home)
        self.refresh(api=api_for("https://avatars.slack-edge.com/new.png"), fetch=Mock(return_value=(PNG + b"new", ".png")))
        self.assertNotEqual(old, icons.path(self.home))
        self.assertFalse(old.exists())

    def test_failures_keep_the_checkpoint_and_retry_successfully(self):
        self.refresh()
        before = (self.home / icons.RECORD).read_bytes()
        changed = api_for("https://avatars.slack-edge.com/new.png")
        for api, fetch in (
            (Mock(side_effect=RuntimeError("offline")), self.fetch),
            (changed, Mock(side_effect=RuntimeError("offline"))),
            (changed, Mock(return_value=(b"<html>not an image", ".png"))),
        ):
            with self.subTest(api=api), self.assertRaises(RuntimeError):
                self.refresh(api=api, fetch=fetch)
            self.assertEqual(before, (self.home / icons.RECORD).read_bytes())
            self.assertEqual(PNG, icons.path(self.home).read_bytes())
        self.assertEqual("saved", self.refresh(api=changed))

    def test_interrupted_first_migration_is_not_marked_complete(self):
        write = icons.slack_workspace_icon._write
        def interrupted(path, data):
            if path == self.home / icons.RECORD:
                raise OSError("disk full")
            write(path, data)
        with patch.object(icons.slack_workspace_icon, "_write", side_effect=interrupted), self.assertRaises(OSError):
            self.refresh()
        self.assertIsNone(icons.path(self.home))
        self.assertEqual("saved", self.refresh())
        self.assertEqual(PNG, icons.path(self.home).read_bytes())

    def test_missing_corrupt_or_old_version_cache_is_repaired(self):
        self.refresh()
        icons.path(self.home).unlink()
        self.assertEqual("saved", self.refresh())
        icons.path(self.home).write_bytes(b"broken")
        self.assertEqual("saved", self.refresh())
        record = json.loads((self.home / icons.RECORD).read_text(encoding="utf-8"))
        record["version"] = 0
        (self.home / icons.RECORD).write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual("saved", self.refresh())

    def test_wrong_identity_and_untrusted_paths_are_not_displayed(self):
        self.refresh()
        self.assertIsNone(icons.path(self.home, app_id="A2"))
        self.assertIsNone(icons.path(self.home, team_id="T2"))
        with self.assertRaisesRegex(RuntimeError, "different Slack workspace"):
            icons.refresh(self.home, {**VALUES, "SLACK_TEAM_ID": "T2"}, api=self.api, fetch=self.fetch)
        record = json.loads((self.home / icons.RECORD).read_text(encoding="utf-8"))
        record["file"] = "../../secret.png"
        (self.home / icons.RECORD).write_text(json.dumps(record), encoding="utf-8")
        self.assertIsNone(icons.path(self.home))

    def test_optional_failures_do_not_block_start(self):
        self.assertEqual("skipped", icons.refresh_safely(self.home, {}))
        with patch.object(icons, "refresh", side_effect=icons.slack_channels.MissingScope("users.info", "users:read")):
            self.assertEqual("needs_permission", tag_cli._refresh_avatar(self.home, VALUES))
        with patch.object(icons, "refresh", side_effect=OSError("disk full")):
            self.assertEqual("unavailable", tag_cli._refresh_avatar(self.home, VALUES))

    def test_background_refresh_reloads_credentials_and_retries_for_both_backends(self):
        for backend in ("codex", "claude"):
            with self.subTest(backend=backend):
                shutdown = Mock()
                shutdown.is_set.return_value = False
                shutdown.wait.side_effect = [False, True]
                old, new = {**VALUES, "OPENTAG_BACKEND": backend}, {**VALUES, "SLACK_BOT_TOKEN": "refreshed", "OPENTAG_BACKEND": backend}
                with patch.object(icons.tag_config, "read_config", side_effect=[old, new]), \
                        patch.object(icons, "refresh_safely", side_effect=["unavailable", "saved"]) as refresh:
                    icons.watch(self.home, shutdown)
                self.assertEqual([old, new], [call.args[1] for call in refresh.call_args_list])
                self.assertEqual([300, 3600], [call.args[0] for call in shutdown.wait.call_args_list])

    def test_cli_list_exposes_the_cached_slack_avatar_without_network(self):
        root = self.home / "installation"
        with patch.dict(os.environ, {"TAG_HOME": str(root)}):
            home = tag_instances.create(root, "t1-a1").home
            tag_config.save_config(home / "config/settings.json", VALUES)
            icons.refresh(home, VALUES, api=self.api, fetch=self.fetch)
            with patch.object(sys, "argv", ["tag", "list", "--json"]), \
                    patch.object(icons.slack_channels, "slack_api", side_effect=AssertionError("network on list")), \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0, tag_cli.main())
            row = json.loads(output.getvalue())["tags"][0]
            self.assertEqual(str(icons.path(home)), row["avatar"])


if __name__ == "__main__":
    unittest.main()
