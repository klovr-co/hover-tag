from __future__ import annotations

import json
import os
import plistlib
import sys
from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import tag_autostart as autostart
from scripts import tag_cli, tag_instances


class IntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_older_tags_have_no_recorded_choice(self) -> None:
        self.assertIsNone(autostart.wanted(self.home))

    def test_choice_round_trips_and_overwrites(self) -> None:
        autostart.set_wanted(self.home, True)
        self.assertTrue(autostart.wanted(self.home))
        autostart.set_wanted(self.home, False)
        self.assertFalse(autostart.wanted(self.home))

    def test_unreadable_choice_counts_as_unrecorded(self) -> None:
        (self.home / "state").mkdir()
        (self.home / autostart.INTENT).write_text("{not json")
        self.assertIsNone(autostart.wanted(self.home))
        (self.home / autostart.INTENT).write_text('{"running": "yes"}')
        self.assertIsNone(autostart.wanted(self.home))


class InstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "tag-home"
        self.first = tag_instances.create(self.root, "first").home
        self.second = tag_instances.create(self.root, "second").home
        self.running: set[Path] = set()
        self.lifecycle = Mock()
        self.lifecycle.process_for.side_effect = (
            lambda path: object() if path.parent.parent in self.running else None)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_seeding_keeps_the_tags_running_now(self) -> None:
        self.running = {self.first}
        self.assertEqual(autostart.seed_from_running(self.root, self.lifecycle), ["first"])
        self.assertTrue(autostart.wanted(self.first))
        self.assertIsNone(autostart.wanted(self.second))

    def test_seeding_never_overrides_recorded_choices(self) -> None:
        autostart.set_wanted(self.second, False)
        self.running = {self.first}
        self.assertEqual(autostart.seed_from_running(self.root, self.lifecycle), [])
        self.assertIsNone(autostart.wanted(self.first))

    def test_supervisor_starts_only_wanted_tags_that_are_down(self) -> None:
        autostart.set_wanted(self.first, True)
        autostart.set_wanted(self.second, False)
        start = Mock(return_value=0)
        supervisor = autostart.Supervisor(self.root, self.lifecycle, start, clock=lambda: 0)
        self.assertEqual(supervisor.check(), ["first"])
        self.running = {self.first}
        self.assertEqual(supervisor.check(), [])
        start.assert_called_once_with("first")

    def test_supervisor_backs_off_after_failures_then_recovers(self) -> None:
        autostart.set_wanted(self.first, True)
        now = [0.0]
        start = Mock(return_value=1)
        supervisor = autostart.Supervisor(self.root, self.lifecycle, start, clock=lambda: now[0])
        self.assertEqual(supervisor.check(), ["first"])
        self.assertEqual(supervisor.check(), [])  # waiting
        now[0] = autostart.CHECK_SECONDS * 2
        self.assertEqual(supervisor.check(), ["first"])
        now[0] += autostart.CHECK_SECONDS * 2  # second failure doubles the wait
        self.assertEqual(supervisor.check(), [])
        now[0] += autostart.MAX_BACKOFF_SECONDS
        start.return_value = 0
        self.assertEqual(supervisor.check(), ["first"])
        self.assertEqual(supervisor.failures, {})

    def test_stopping_on_purpose_clears_backoff(self) -> None:
        autostart.set_wanted(self.first, True)
        supervisor = autostart.Supervisor(self.root, self.lifecycle, Mock(return_value=1), clock=lambda: 0)
        supervisor.check()
        autostart.set_wanted(self.first, False)
        supervisor.check()
        self.assertEqual(supervisor.failures, {})


class ServiceDefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "tag-home"
        (self.root / "bin").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_managed_install_uses_the_upgrade_stable_command(self) -> None:
        bin_dir = Path(self.temporary.name) / "bin"
        bin_dir.mkdir()
        (bin_dir / "tag").write_text("#!/bin/sh\n")
        (self.root / "bin/tag-launch.py").write_text("")
        (self.root / "current.json").write_text(json.dumps({"bin_dir": str(bin_dir), "release": "r1"}))
        with patch.object(autostart.os, "name", "posix"):
            command = autostart.service_command(self.root, Path("/src"))
        self.assertEqual(command, ["/bin/sh", str(bin_dir / "tag"), "autostart", "run"])

    def test_source_checkout_runs_its_own_cli(self) -> None:
        command = autostart.service_command(self.root, Path("/src"))
        self.assertEqual(command[1:], ["/src/scripts/tag_cli.py", "autostart", "run"])

    def test_launchd_agent_restarts_and_runs_at_login(self) -> None:
        text = autostart.launchd_plist(["/bin/sh", "/a b/tag", "autostart", "run"], self.root, self.root / "log")
        plist = plistlib.loads(text.encode())
        self.assertEqual(plist["ProgramArguments"], ["/bin/sh", "/a b/tag", "autostart", "run"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["EnvironmentVariables"], {"TAG_HOME": str(self.root)})
        self.assertEqual(plist["Label"], autostart.label(self.root))

    def test_systemd_unit_quotes_paths_and_restarts(self) -> None:
        unit = autostart.systemd_unit(["/bin/sh", "/a b/tag", "autostart", "run"], self.root)
        self.assertIn("ExecStart=/bin/sh '/a b/tag' autostart run", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_test_homes_never_share_the_real_service_name(self) -> None:
        self.assertNotEqual(autostart.label(self.root), autostart.LABEL)
        self.assertEqual(autostart.label(self.root), autostart.label(self.root))
        with patch("scripts.tag_paths.platform_tag_home", return_value=self.root):
            self.assertEqual(autostart.label(self.root), autostart.LABEL)

    def test_enable_writes_and_loads_the_launch_agent(self) -> None:
        home = Path(self.temporary.name) / "user"
        calls = []
        with patch.object(autostart, "mechanism", return_value="launchd"), \
                patch.object(autostart.Path, "home", return_value=home), \
                patch.object(autostart.subprocess, "run",
                             side_effect=lambda command, **_: calls.append(command) or Mock(returncode=0)):
            result = autostart.enable(self.root, Path("/src"))
            self.assertTrue(result["enabled"])
            self.assertTrue(any(command[:2] == ["launchctl", "bootstrap"] for command in calls))
            autostart.enable(self.root, Path("/src"))  # repeatable
            result = autostart.disable(self.root)
        self.assertFalse(result["enabled"])

    def test_failed_registration_is_reported(self) -> None:
        home = Path(self.temporary.name) / "user"
        with patch.object(autostart, "mechanism", return_value="launchd"), \
                patch.object(autostart.Path, "home", return_value=home), \
                patch.object(autostart.subprocess, "run",
                             return_value=Mock(returncode=5, stderr="Bootstrap failed", stdout="")):
            with self.assertRaisesRegex(RuntimeError, "Bootstrap failed"):
                autostart.enable(self.root, Path("/src"))


class CommandTests(unittest.TestCase):
    """`tag start` and `tag stop` record the choice the login service follows."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Tag"
        environment = patch.dict(os.environ, {"TAG_HOME": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.home = tag_instances.create(self.root, "t1-a1").home

    def cli(self, *arguments: str) -> tuple[int, str]:
        with patch.object(sys, "argv", ["tag", *arguments]), redirect_stdout(StringIO()) as output:
            code = tag_cli.main()
        return code, output.getvalue()

    def test_stop_leaves_the_tag_off(self) -> None:
        autostart.set_wanted(self.home, True)
        with patch.object(tag_cli, "stop_process"):
            self.assertEqual(self.cli("t1-a1", "stop")[0], 0)
        self.assertFalse(autostart.wanted(self.home))

    def test_restart_keeps_the_tag_wanted(self) -> None:
        autostart.set_wanted(self.home, True)
        with patch.object(tag_cli, "stop_process"), patch.dict(os.environ, {"TAG_RESTART_FLOW": "1"}):
            self.cli("t1-a1", "stop")
        self.assertTrue(autostart.wanted(self.home))

    def test_list_and_status_report_the_choice(self) -> None:
        autostart.set_wanted(self.home, True)
        code, output = self.cli("list", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["tags"][0]["keep_running"])
        with patch.object(autostart, "mechanism", return_value="launchd"), \
                patch.object(autostart.Path, "home", return_value=self.root / "user"):
            code, output = self.cli("autostart", "--json")
        result = json.loads(output)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["tags"], [{"tag": "t1-a1", "keep_running": True}])

    def test_autostart_is_installation_wide(self) -> None:
        with self.assertRaises(SystemExit):
            with redirect_stdout(StringIO()), patch("sys.stderr", StringIO()):
                self.cli("t1-a1", "autostart")


if __name__ == "__main__":
    unittest.main()
