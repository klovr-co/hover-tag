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

from scripts import tag_autostart, tag_cli, tag_config, tag_install, tag_instances

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
        documented = set(re.findall(r"^\| `([a-z-]+)` \|", DOC.read_text(encoding="utf-8"), re.M))
        self.assertEqual(documented, set(tag_cli.CAPABILITIES))

    def test_list_rows_provide_what_apps_read(self) -> None:
        home = tag_instances.create(self.root, "t1-a1").home
        tag_config.save_config(home / "config/settings.json", {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1"})
        result = self.cli("list", "--json")
        self.assertProvides(result, example("list.json"), "tag list --json")
        self.assertProvides(result["tags"][0], example("list.json")["tags"][0], "tag list --json rows")

    def test_autostart_and_logs_provide_what_apps_read(self) -> None:
        home = tag_instances.create(self.root, "t1-a1").home
        (home / "state/slack.log").write_text("Connected to Slack\n")
        with patch.object(tag_autostart, "mechanism", return_value="launchd"), \
                patch.object(tag_autostart.Path, "home", return_value=self.root / "user"):
            result = self.cli("autostart", "status", "--json")
        self.assertProvides(result, example("autostart.json"), "tag autostart --json")
        self.assertProvides(result["tags"][0], example("autostart.json")["tags"][0], "autostart rows")
        logs = self.cli("t1-a1", "logs", "--json")
        self.assertProvides(logs, example("logs.json"), "tag logs --json")
        self.assertEqual(logs["services"]["slack"], ["Connected to Slack"])

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
        kinds = {"choose", "multi", "text", "secret", "confirm", "people", "slack_login"}
        events = [json.loads(line) for line in (EXAMPLES / "setup.jsonl").read_text().splitlines()]
        for event in events:
            if event["type"] == "question":
                self.assertIn(event["kind"], kinds)
                known = f'qid="{event["id"]}"' in sources or f'qid: str = "{event["id"]}"' in sources
                self.assertTrue(known, f"unknown setup question {event['id']}")
                self.assertIn("can_go_back", event)
        self.assertEqual(events[-1]["type"], "result")


if __name__ == "__main__":
    unittest.main()
