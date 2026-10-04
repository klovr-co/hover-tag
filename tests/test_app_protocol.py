"""The CLI side of the app contract in docs/reference/app-protocol.md.

Desktop apps parse protocol/examples/. These tests fail when real CLI
output stops providing a field those examples promise, so the CLI and the
apps can't drift apart silently. The app's own tests parse the same files.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import agent_models, slack_channel_names, tag_activity, tag_autostart, tag_cli, tag_config, tag_install, tag_instances

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "protocol/examples"
DOC = ROOT / "docs/reference/app-protocol.md"


def example(name: str):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        environment = patch.dict(os.environ, {"TAG_HOME": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)

    def cli(self, *arguments: str) -> dict:
        with patch.object(sys, "argv", ["tag", *arguments]), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tag_cli.main(), 0)
        return json.loads(output.getvalue())

    def assertProvides(self, actual: dict, promised: dict, where: str) -> None:
        missing = sorted(set(promised) - set(actual))
        self.assertEqual(missing, [], f"{where} no longer provides {missing}")

    def test_version_reports_protocol_and_every_documented_capability(self) -> None:
        result = self.cli("version", "--json")
        self.assertProvides(result, example("version.json"), "tag version --json")
        self.assertEqual(result["app_protocol"], tag_cli.APP_PROTOCOL)
        documented = set(re.findall(r"^\| `([a-z0-9-]+)` \|", DOC.read_text(encoding="utf-8"), re.M))
        self.assertEqual(documented, set(tag_cli.CAPABILITIES))

    def test_list_rows_provide_what_apps_read(self) -> None:
        home = tag_instances.create(self.root, "t1-a1").home
        tag_config.save_config(home / "config/settings.json", {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"})
        result = self.cli("list", "--json")
        self.assertProvides(result, example("list.json"), "tag list --json")
        self.assertProvides(result["tags"][0], example("list.json")["tags"][0], "tag list --json rows")
        self.assertEqual((None, "Account default", None), (
            result["tags"][0]["description"], result["tags"][0]["default_model_name"], result["tags"][0]["default_effort"]))
        # With a description, a chosen model, and its saved catalog, the row names both.
        tag_config.save_config(home / "config/settings.json", {
            "SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1", "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5",
            "OPENTAG_BOT_DESCRIPTION": "I'm Maya's personal assistant. I help with launch work."})
        agent_models.remember_model_names([agent_models.ModelOption(
            "gpt-5.5", "GPT-5.5", ("low", "medium", "high"), default_reasoning_effort="medium")],
            agent_models.model_names_path(home))
        tag_config.save_config(home / "config/settings.json", {
            **tag_config.load_config(home / "config/settings.json"),
            "SLACK_CHANNEL_IDS": "C0LAUNCH1,C0GENERAL",
            "MFS_ALLOWED_SCOPES": "slack://tag-t1-a1/channels/launch__C0LAUNCH1,slack://tag-t1-a1/channels/general__C0GENERAL"})
        row = self.cli("list", "--json")["tags"][0]
        promised = example("list.json")["tags"][0]
        self.assertEqual(promised["channels"], row["channels"])
        # A channel Tag hasn't named yet keeps its ID, after the named ones.
        tag_config.save_config(home / "config/settings.json", {
            **tag_config.load_config(home / "config/settings.json"), "SLACK_CHANNEL_IDS": "C0PRIVATE,C0LAUNCH1,C0LAUNCH1"})
        self.assertEqual([{"id": "C0LAUNCH1", "name": "launch"}, {"id": "C0PRIVATE", "name": None}],
                         self.cli("list", "--json")["tags"][0]["channels"])
        self.assertEqual({key: promised[key] for key in ("description", "default_model", "default_model_label",
                                                         "default_model_name", "default_effort")},
                         {key: row[key] for key in ("description", "default_model", "default_model_label",
                                                    "default_model_name", "default_effort")})

    def test_autostart_and_logs_provide_what_apps_read(self) -> None:
        home = tag_instances.create(self.root, "t1-a1").home
        (home / "state/slack.log").write_text("Connected to Slack\n", encoding="utf-8")
        with patch.object(tag_autostart, "mechanism", return_value="launchd"), \
                patch.object(tag_autostart.Path, "home", return_value=self.root / "user"):
            result = self.cli("autostart", "status", "--json")
        self.assertProvides(result, example("autostart.json"), "tag autostart --json")
        self.assertProvides(result["tags"][0], example("autostart.json")["tags"][0], "autostart rows")
        logs = self.cli("t1-a1", "logs", "--json")
        self.assertProvides(logs, example("logs.json"), "tag logs --json")
        self.assertEqual(logs["services"]["slack"], ["Connected to Slack"])
        self.assertEqual([], logs["activity"])
        tag_config.save_config(home / "config/settings.json", {
            "MFS_ALLOWED_SCOPES": "slack://tag-t1-a1/channels/launch__C0LAUNCH1"})
        store = tag_activity.ActivityStore(home / "state/activity")
        store.finish(store.create(team="T1", channel="C0LAUNCH1", thread_ts="1.0", request_ts="1.0",
                                  requester="U1"), "completed")
        activity = self.cli("t1-a1", "logs", "--json")["activity"]
        self.assertProvides(activity[0], example("logs.json")["activity"][0], "tag logs --json activity")
        self.assertEqual(("replied", "launch", False), (activity[0]["kind"], activity[0]["channel_name"], activity[0]["dm"]))

    def test_activity_filters_apply_before_the_recent_limit_for_both_backends(self) -> None:
        home = tag_instances.create(self.root, "t1-a1").home
        store = tag_activity.ActivityStore(home / "state/activity")
        for backend in ("codex", "claude"):
            run = store.create(team="T1", channel="C1", thread_ts="1", request_ts="1", requester="U1")
            store.save_model(run, backend, "model")
            store.finish(run, "completed")
        stopped = store.create(team="T1", channel="C1", thread_ts="1", request_ts="1", requester="U1")
        store.finish(stopped, "interrupted")
        for index in range(tag_activity.MAX_RECENT + 1):
            run = store.create(team="T1", channel="C1" if index % 2 else "C2", thread_ts="1", request_ts="1", requester="U1")
            store.finish(run, "failed")
        before = {path.name: path.read_bytes() for path in store.root.glob("*.json")}
        items = self.cli("t1-a1", "logs", "--json", "--activity-channel", "C1", "--hide-errors")["activity"]
        self.assertEqual(len(items), 3)
        self.assertEqual({item.get("backend") for item in items}, {"codex", "claude", None})
        self.assertEqual({item["channel"] for item in items}, {"C1"})
        self.assertIn("stopped", {item["kind"] for item in items})
        self.assertEqual([item["at"] for item in items], sorted((item["at"] for item in items), reverse=True))
        self.assertEqual(before, {path.name: path.read_bytes() for path in store.root.glob("*.json")})
        self.assertEqual([], self.cli("t1-a1", "logs", "--json", "--activity-channel", "C3")["activity"])

    def test_activity_window_can_expand_to_retained_history(self) -> None:
        home = tag_instances.create(self.root, "t1-a1").home
        store = tag_activity.ActivityStore(home / "state/activity")
        for index in range(55):
            run = store.create(team="T1", channel="C1", thread_ts="1", request_ts="1", requester="U1")
            store.save_model(run, "codex" if index % 2 else "claude", "model")
            store.finish(run, "completed")
        page = self.cli("t1-a1", "logs", "--json", "--activity-limit", "50", "--hide-errors")
        self.assertEqual(len(page["activity"]), 50)
        self.assertTrue(page["activity_has_more"])
        expanded = self.cli("t1-a1", "logs", "--json", "--activity-limit", "100", "--hide-errors")
        self.assertEqual(len(expanded["activity"]), 55)
        self.assertFalse(expanded["activity_has_more"])
        self.assertTrue({item["run_id"] for item in page["activity"]} <= {item["run_id"] for item in expanded["activity"]})

    def test_ai_settings_provide_what_apps_read(self) -> None:
        tag_instances.create(self.root, "t1-a1")
        empty = self.root / "no-agents"
        empty.mkdir()
        with patch.dict(os.environ, {"PATH": str(empty)}):
            result = self.cli("t1-a1", "settings", "ai", "--json")
        promised = example("ai-status.json")
        self.assertProvides(result, promised, "tag settings ai --json")
        self.assertProvides(result["connections"][0], promised["connections"][0], "AI connection rows")
        self.assertProvides(result["default_model"], promised["default_model"], "AI default model")
        self.assertEqual((None, [], False), (result["default_effort"], result["effort_levels"], result["effort_chosen"]))
        self.assertEqual(["not_installed", "not_installed"], [row["state"] for row in result["connections"]])
        self.assertEqual(["install"], result["connections"][0]["actions"])

    def test_cached_channel_names_and_details_work_without_memory_or_slack(self) -> None:
        home = tag_instances.create(self.root, "t1-a1").home
        tag_config.save_config(home / "config/settings.json", {"SLACK_TEAM_ID": "T1", "SLACK_CHANNEL_IDS": "C1"})
        slack_channel_names.remember(home, "T1", {"C1": "launch"})
        store = tag_activity.ActivityStore(home / "state/activity")
        run = store.create(team="T1", channel="C1", thread_ts="1.0", request_ts="1.0", requester="U1")
        store.finish(run, "completed")
        with patch("scripts.slack_channels.slack_api", side_effect=AssertionError("must stay offline")):
            self.assertEqual([{"id": "C1", "name": "launch"}], self.cli("list", "--json")["tags"][0]["channels"])
            item = self.cli("t1-a1", "logs", "--json")["activity"][0]
            self.assertEqual("launch", item["channel_name"])
            details = self.cli("t1-a1", "logs", "--activity", item["run_id"], "--json")["activity"]
            self.assertEqual("completed", details["outcome"])
            self.assertEqual([], details["events"])

        with patch.object(sys, "argv", ["tag", "t1-a1", "logs", "--activity", run]), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tag_cli.main(), 0)
        self.assertIn(f"Request {run} · completed", output.getvalue())
        self.assertIn("No tool activity was recorded", output.getvalue())

    def test_activity_details_missing_and_invalid_ids_fail_with_actionable_errors(self) -> None:
        tag_instances.create(self.root, "t1-a1")
        for run in ("f" * 32, "../escape"):
            with self.subTest(run=run), patch.object(sys, "argv", ["tag", "t1-a1", "logs", "--activity", run, "--json"]), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(tag_cli.main(), 1)
            result = json.loads(output.getvalue())
            self.assertFalse(result["ok"])
            self.assertIn("logs --json", result["error"])

    def test_install_progress_lines_match_the_example_format(self) -> None:
        lines = (EXAMPLES / "install-progress.txt").read_text(encoding="utf-8").splitlines()
        steps = [json.loads(line.removeprefix("@tag-progress "))["step"] for line in lines]
        self.assertEqual(steps, ["tools", "python", "download", "release", "components",
                                 "memory", "command", "done"])
        stderr = io.StringIO()
        with patch.dict(os.environ, {"TAG_INSTALL_PROGRESS": "jsonl"}), redirect_stderr(stderr):
            tag_install.progress("release", version="0.2.0")
        line = stderr.getvalue().strip()
        self.assertTrue(line.startswith("@tag-progress "))
        self.assertEqual(json.loads(line.removeprefix("@tag-progress ")),
                         json.loads(lines[3].removeprefix("@tag-progress ")))
        quiet = io.StringIO()
        with patch.dict(os.environ, {"TAG_INSTALL_PROGRESS": ""}), redirect_stderr(quiet):
            tag_install.progress("release", version="0.2.0")
        self.assertEqual(quiet.getvalue(), "")

    def test_shell_installer_emits_the_same_steps(self) -> None:
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("tag_progress tools", script)
        self.assertIn("tag_progress python", script)
        self.assertIn('"step":"%s"', script)

    def test_setup_example_uses_real_question_ids_and_kinds(self) -> None:
        sources = "".join(path.read_text(encoding="utf-8") for path in (ROOT / "scripts").glob("*.py"))
        kinds = {"choose", "multi", "text", "secret", "confirm", "profile_picture", "slack_login"}
        events = [json.loads(line) for line in (EXAMPLES / "setup.jsonl").read_text(encoding="utf-8").splitlines()]
        for event in events:
            if event["type"] == "question":
                self.assertIn(event["kind"], kinds)
                known = f'qid="{event["id"]}"' in sources or f'qid: str = "{event["id"]}"' in sources
                self.assertTrue(known, f"unknown setup question {event['id']}")
                self.assertIn("can_go_back", event)
        self.assertEqual(events[-1]["type"], "result")
        # The v2 order: Your Tag, AI, Workspace, Create, Channels.
        asked = [event["id"] for event in events if event["type"] == "question"]
        self.assertEqual(list(dict.fromkeys(asked)), ["profile", "default_model", "workspace", "approve_setup", "channels"])
        steps = [event["step"] for event in events if event["type"] == "progress" and "backend" not in event]
        self.assertEqual(steps, ["create", "picture", "install", "connect"])

    def test_setup_result_reports_what_the_ready_screen_links_to(self) -> None:
        home = tag_instances.create(self.root, "t1-a1").home
        tag_config.save_config(home / "config/settings.json", {
            "OPENTAG_BACKEND": "codex", "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5", "MFS_URL": "http://127.0.0.1:13619",
            "MFS_ALLOWED_SCOPES": "slack://tag-t1-a1/channels/general__C0GENERAL", "SLACK_APP_TOKEN": "xapp-1",
            "SLACK_BOT_TOKEN": "xoxb-1", "SLACK_ALLOWED_USER_IDS": "U1", "SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1",
            "SLACK_CHANNEL_IDS": "C0GENERAL"})
        raw = io.StringIO()
        with patch.object(sys, "__stdout__", raw), patch.object(tag_cli, "_refresh_workspace_icon"):
            self.assertEqual(tag_cli._setup_result(0, "t1-a1", True), 0)
        result = json.loads(raw.getvalue())
        promised = [json.loads(line) for line in (EXAMPLES / "setup.jsonl").read_text(encoding="utf-8").splitlines()][-1]
        self.assertProvides(result, promised, "setup result")
        self.assertProvides(result["ready"], promised["ready"], "setup result ready")
        self.assertEqual(result["ready"], {"team": "T1", "app_id": "A1",
                                           "channels": [{"id": "C0GENERAL", "name": "general"}],
                                           "ai": {"backend": "codex", "backend_name": "Codex", "label": "gpt-5.5"}})


if __name__ == "__main__":
    unittest.main()
