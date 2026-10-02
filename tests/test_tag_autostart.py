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
        self.running = {self.first}
        now[0] += autostart.STABLE_SECONDS
        supervisor.check()
        self.assertEqual(supervisor.failures, {})

    def test_a_tag_that_crashes_right_after_starting_backs_off(self) -> None:
        # `tag start` succeeds, then the bridge dies (say, a revoked token).
        autostart.set_wanted(self.first, True)
        now = [0.0]
        start = Mock(return_value=0)
        supervisor = autostart.Supervisor(self.root, self.lifecycle, start, clock=lambda: now[0])
        attempts = 0
        for _ in range(30):  # 30 minutes of checks
            attempts += len(supervisor.check())
            now[0] += autostart.CHECK_SECONDS
        self.assertLessEqual(attempts, 5)

    def test_a_healthy_restart_is_immediate_the_first_time(self) -> None:
        autostart.set_wanted(self.first, True)
        supervisor = autostart.Supervisor(self.root, self.lifecycle, Mock(return_value=0), clock=lambda: 0)
        self.assertEqual(supervisor.check(), ["first"])

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
        # Stopping the agent must never stop the Tags it started, nor throttle them.
        self.assertTrue(plist["AbandonProcessGroup"])
        self.assertNotIn("ProcessType", plist)

    def test_systemd_unit_quotes_paths_and_restarts(self) -> None:
        unit = autostart.systemd_unit(["/bin/sh", "/a b/tag", "autostart", "run"], self.root)
        self.assertIn("ExecStart=/bin/sh '/a b/tag' autostart run", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("WantedBy=default.target", unit)
        # The default control-group kill would stop every Tag on `autostart off`.
        self.assertIn("KillMode=process", unit)

    def test_desktop_entry_quotes_paths_with_spaces(self) -> None:
        entry = autostart.xdg_entry(["/bin/sh", "/home/a b/50% $x/tag", "autostart", "run"], self.root)
        line = next(l for l in entry.splitlines() if l.startswith("Exec="))
        self.assertIn('"/home/a b/50%% \\$x/tag"', line)
        self.assertNotIn("'", line)

    def test_test_homes_never_share_the_real_service_name(self) -> None:
        self.assertNotEqual(autostart.label(self.root), autostart.LABEL)
        self.assertEqual(autostart.label(self.root), autostart.label(self.root))
        with patch("scripts.tag_paths.platform_tag_home", return_value=self.root):
            self.assertEqual(autostart.label(self.root), autostart.LABEL)

    def test_enable_writes_and_loads_the_launch_agent(self) -> None:
        home = Path(self.temporary.name) / "user"
        calls = []
        def run(command, **_):
            calls.append(command)
            return Mock(returncode=1 if command[1] == "print" else 0)  # job gone after bootout
        with patch.object(autostart, "mechanism", return_value="launchd"), \
                patch.object(autostart.Path, "home", return_value=home), \
                patch.object(autostart.time, "sleep"), \
                patch.object(autostart.subprocess, "run", side_effect=run):
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
                patch.object(autostart.time, "sleep"), \
                patch.object(autostart.subprocess, "run",
                             return_value=Mock(returncode=5, stderr="Bootstrap failed", stdout="")):
            with self.assertRaisesRegex(RuntimeError, "Bootstrap failed"):
                autostart.enable(self.root, Path("/src"))

    def test_bootstrap_waits_for_the_old_job_and_retries(self) -> None:
        home = Path(self.temporary.name) / "user"
        results = iter([Mock(returncode=0),  # bootout
                        Mock(returncode=0), Mock(returncode=1),  # print: still there, then gone
                        Mock(returncode=5, stderr="Bootstrap failed: 5", stdout=""), Mock(returncode=0)])
        with patch.object(autostart, "mechanism", return_value="launchd"), \
                patch.object(autostart.Path, "home", return_value=home), \
                patch.object(autostart.time, "sleep"), \
                patch.object(autostart.subprocess, "run", side_effect=lambda *a, **k: next(results)):
            self.assertTrue(autostart.enable(self.root, Path("/src"))["enabled"])


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

    def test_supervised_start_never_overrides_a_stop(self) -> None:
        # The login service decided to start this Tag, then the person stopped it.
        autostart.set_wanted(self.home, False)
        with patch.dict(os.environ, {autostart.SUPERVISED_ENV: "1"}), \
                patch.object(tag_cli, "missing_runtime_dependencies", return_value=[]), \
                patch.object(tag_cli, "legacy_slack_ready", return_value=False), \
                patch.object(tag_cli, "read_config", return_value={}), \
                patch.object(tag_cli, "start_process") as start:
            tag_config_path = self.home / "config/settings.json"
            tag_config_path.write_text("{}")
            with patch("scripts.tag_config.config_errors", return_value=[]), \
                    patch.object(tag_cli, "assert_unique_slack_app"), \
                    patch("scripts.tag_config.migrate_file_delivery"):
                code, _ = self.cli("t1-a1", "start")
        self.assertEqual(code, 0)
        start.assert_not_called()
        self.assertFalse(autostart.wanted(self.home))

    def test_keep_records_choices_without_starting(self) -> None:
        code, output = self.cli("autostart", "keep", "t1-a1", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(autostart.wanted(self.home))
        self.assertEqual(json.loads(output)["tags"], [{"tag": "t1-a1", "keep_running": True}])
        with self.assertRaises(SystemExit), redirect_stdout(StringIO()), patch("sys.stderr", StringIO()):
            self.cli("autostart", "keep")

    def test_autostart_is_installation_wide(self) -> None:
        with self.assertRaises(SystemExit):
            with redirect_stdout(StringIO()), patch("sys.stderr", StringIO()):
                self.cli("t1-a1", "autostart")


if __name__ == "__main__":
    unittest.main()
