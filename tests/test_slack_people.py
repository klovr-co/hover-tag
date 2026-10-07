from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from scripts import slack_people
from scripts.tag_activity import ActivityStore


def profile(user: str, name: str = "Maxine", avatar: str = "https://avatars.slack-edge.com/m_72.png") -> dict:
    return {"ok": True, "user": {"id": user, "name": "maxine.l", "real_name": "Maxine Lai",
                                 "profile": {"display_name": name, "image_72": avatar}}}


class SlackPeopleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def test_profile_prefers_display_name_and_keeps_only_https_pictures(self) -> None:
        self.assertEqual({"name": "Maxine", "avatar": "https://avatars.slack-edge.com/m_72.png"},
                         slack_people.from_profile(profile("U1"), "U1"))
        self.assertEqual({"name": "Maxine Lai"}, slack_people.from_profile(
            profile("U1", name="", avatar="javascript:alert(1)"), "U1"))
        self.assertIsNone(slack_people.from_profile(profile("U2"), "U1"))
        self.assertIsNone(slack_people.from_profile({"ok": False, "error": "user_not_found"}, "U1"))

    def test_lookup_caches_refreshes_when_stale_and_keeps_the_name_on_failure(self) -> None:
        calls = []

        def users_info(user):
            calls.append(user)
            return profile(user)
        self.assertTrue(slack_people.look_up(self.home, "T1", "U1", users_info))
        self.assertTrue(slack_people.look_up(self.home, "T1", "U1", users_info))
        self.assertEqual(["U1"], calls)
        self.assertEqual("Maxine", slack_people.read(self.home, "T1")["U1"]["name"])
        path = self.home / "state/slack-people/T1/U1.json"
        stale = time.time() - slack_people.FRESH_SECONDS - 60
        os.utime(path, (stale, stale))

        def offline(_user):
            raise OSError("offline")
        self.assertFalse(slack_people.look_up(self.home, "T1", "U1", offline))
        self.assertEqual("Maxine", slack_people.read(self.home, "T1")["U1"]["name"])
        # Invalid IDs never reach Slack or the file system.
        self.assertTrue(slack_people.look_up(self.home, "T1", "../U1", offline))
        self.assertEqual({}, slack_people.read(self.home, "../T1"))

    def test_migration_names_older_requesters_and_retries_failures(self) -> None:
        store = ActivityStore(self.home / "state/activity")
        for user in ("U1", "U2"):
            store.create(team="T1", channel="C1", thread_ts="1.0", request_ts="1.0", requester=user)
        values = {"SLACK_TEAM_ID": "T1", "SLACK_BOT_TOKEN": "xoxb-test"}
        marker = self.home / "state/migrations/slack-people-v1.json"

        def partly_down(_token, method, params):
            self.assertEqual("users.info", method)
            if params["user"] == "U2":
                raise RuntimeError("ratelimited")
            return profile(params["user"])
        self.assertFalse(slack_people.migrate(self.home, values, api=partly_down))
        self.assertEqual({"U1"}, set(slack_people.read(self.home, "T1")))
        self.assertFalse(marker.exists())
        asked = []

        def working(_token, _method, params):
            asked.append(params["user"])
            return profile(params["user"], name=params["user"].lower())
        self.assertTrue(slack_people.migrate(self.home, values, api=working))
        # Only the missing person is looked up on retry.
        self.assertEqual(["U2"], asked)
        self.assertEqual({"version": 1, "team": "T1"}, json.loads(marker.read_text(encoding="utf-8")))
        self.assertTrue(slack_people.migrate(self.home, values, api=working))
        self.assertEqual(["U2"], asked)


if __name__ == "__main__":
    unittest.main()
