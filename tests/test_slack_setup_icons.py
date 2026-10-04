from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import slack_setup_icons as icons
from scripts import opentag_setup as setup


PNG = b"\x89PNG\r\n\x1a\nfixture"
VALUES = {"SLACK_TEAM_ID": "T1", "SLACK_ALLOWED_USER_IDS": "U1"}
CONNECTION = {**VALUES, "SLACK_BOT_TOKEN": "xoxb-fixture", "SLACK_APP_ID": "A1"}


class SetupPicturesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "new-tag"
        self.api = Mock(side_effect=self.response)
        self.fetch = Mock(side_effect=lambda url: (PNG + url.encode(), ".png"))
        self.discover = patch.object(icons.tag_instances, "discover", return_value=[]).start()
        self.addCleanup(patch.stopall)

    def response(self, token, method, parameters):
        if method == "auth.test":
            return {"team_id": "T1", "app_id": "A1"}
        if method == "team.info":
            return {"team": {"id": "T1", "icon": {"image_132": "https://example.com/team.png"}}}
        return {"user": {"id": "U1", "profile": {"image_192": "https://example.com/user.png"}}}

    def pictures(self, values=None):
        return icons.pictures(self.root, self.home, values or CONNECTION, api=self.api, fetch=self.fetch)

    def test_older_setup_backfills_and_repeated_recaps_reuse_verified_images(self):
        result = self.pictures()
        for path in result.values():
            self.assertTrue(Path(path).read_bytes().startswith(PNG))
        self.api.reset_mock()
        self.assertEqual(result, self.pictures())
        self.api.assert_not_called()
        self.assertEqual(2, self.fetch.call_count)

    def test_new_setup_uses_only_a_connection_from_the_selected_workspace(self):
        self.discover.return_value = [{"id": "other", "valid": True}, {"id": "same", "valid": True}]
        with patch.object(icons.tag_instances, "resolve", side_effect=lambda root, name: SimpleNamespace(home=root / name)), \
                patch.object(icons.tag_config, "read_config", side_effect=[
                    {**CONNECTION, "SLACK_TEAM_ID": "T2", "SLACK_BOT_TOKEN": "other-token"}, CONNECTION]):
            self.assertTrue(all(self.pictures(VALUES).values()))
        self.assertTrue(all(call.args[0] == "xoxb-fixture" for call in self.api.call_args_list))

    def test_first_connection_keeps_placeholders_without_trying_cli_credentials(self):
        self.assertEqual({"workspace": None, "owner": None}, self.pictures(VALUES))
        self.api.assert_not_called()
        self.fetch.assert_not_called()

    def test_missing_scope_for_workspace_does_not_hide_owner_and_is_retryable(self):
        def missing(token, method, parameters):
            if method == "team.info":
                raise RuntimeError("missing_scope")
            return self.response(token, method, parameters)
        self.api.side_effect = missing
        first = self.pictures()
        self.assertIsNone(first["workspace"])
        self.assertTrue(first["owner"])
        self.api.side_effect = self.response
        self.assertTrue(all(self.pictures().values()))

    def test_default_workspace_uses_initial_without_downloading_slack_default(self):
        def default(token, method, parameters):
            payload = self.response(token, method, parameters)
            if method == "team.info":
                payload["team"]["icon"]["image_default"] = True
            return payload
        self.api.side_effect = default
        self.assertIsNone(self.pictures()["workspace"])
        self.fetch.assert_called_once_with("https://example.com/user.png")

    def test_failed_download_is_not_checkpointed_and_retry_repairs_it(self):
        self.fetch.side_effect = RuntimeError("offline")
        self.assertFalse(any(self.pictures().values()))
        self.assertFalse(list((self.home / "state").glob("setup-icons-v*.json")))
        self.fetch.side_effect = lambda url: (PNG + url.encode(), ".png")
        self.assertTrue(all(self.pictures().values()))

    def test_interrupted_record_write_and_corrupt_image_are_repaired(self):
        write = icons.slack_workspace_icon._write
        def interrupted(path, data):
            if path.suffix == ".json":
                raise OSError("disk full")
            write(path, data)
        with patch.object(icons.slack_workspace_icon, "_write", side_effect=interrupted):
            self.assertFalse(any(self.pictures().values()))
        result = self.pictures()
        Path(result["owner"]).write_bytes(b"invalid")
        self.assertEqual(result, self.pictures())
        self.assertTrue(Path(result["owner"]).read_bytes().startswith(PNG))

    def test_expired_cache_survives_network_failure_but_never_crosses_identities(self):
        result = self.pictures()
        record_path = next((self.home / "state").glob("setup-icons-v*.json"))
        record = json.loads(record_path.read_text())
        for kind in ("workspace", "owner"):
            record[kind]["checked"] = 0
        record_path.write_text(json.dumps(record))
        self.api.side_effect = RuntimeError("offline")
        self.assertEqual(result, self.pictures())
        self.assertFalse(any(self.pictures({**CONNECTION, "SLACK_ALLOWED_USER_IDS": "U2"}).values()))
        self.assertFalse(any(self.pictures({**CONNECTION, "SLACK_TEAM_ID": "T2"}).values()))

    def test_wrong_authorization_or_response_identity_cannot_supply_pictures(self):
        self.api.side_effect = None
        for payload in ({"team_id": "T2"}, {"team_id": "T1", "user": {"id": "U2"}, "team": {"id": "T2"}}):
            self.api.return_value = payload
            self.assertFalse(any(self.pictures().values()))
        self.fetch.assert_not_called()

    def test_recap_exposes_local_pictures_in_cli_json_for_desktop(self):
        paths = {"workspace": "/state/team.png", "owner": "/state/user.png"}
        with patch.object(setup.settings, "load_config", return_value=VALUES), \
                patch.object(setup, "setup_progress", return_value={}), \
                patch.object(setup, "profile_picture", return_value=None), \
                patch.object(setup, "instance_home", return_value=self.home), \
                patch.object(setup, "tag_home", return_value=self.root), \
                patch.object(setup.tag_ai, "default_choice", return_value=dict.fromkeys(("value", "backend", "backend_name", "label"), "")), \
                patch.object(setup.slack_setup_icons, "pictures", return_value=paths):
            recap = setup.setup_recap(self.home / "config/settings.json", self.home)
        self.assertEqual(paths["workspace"], recap["workspace"]["icon"])
        self.assertEqual(paths["owner"], recap["owner"]["icon"])
