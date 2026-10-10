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
        (self.home / autostart.INTENT).write_text("{not json", encoding="utf-8")
        self.assertIsNone(autostart.wanted(self.home))
        (self.home / autostart.INTENT).write_text('{"running": "yes"}', encoding="utf-8")
        self.assertIsNone(autostart.wanted(self.home))


class MemoryIntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.shared = Path(self.temporary.name) / "shared/mfs"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_no_choice_is_recorded_until_memory_is_started_or_stopped(self) -> None:
        self.assertIsNone(autostart.memory_wanted(self.shared))

    def test_choice_round_trips_and_overwrites(self) -> None:
        autostart.set_memory_wanted(self.shared, True)
        self.assertTrue(autostart.memory_wanted(self.shared))
        autostart.set_memory_wanted(self.shared, False)
        self.assertFalse(autostart.memory_wanted(self.shared))

    def test_unreadable_choice_counts_as_unrecorded(self) -> None:
        self.shared.mkdir(parents=True)
        (self.shared / autostart.MEMORY_INTENT).write_text("{not json", encoding="utf-8")
        self.assertIsNone(autostart.memory_wanted(self.shared))


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


class MemorySupervisionTests(unittest.TestCase):
    """The login service restarts shared MFS as well as the Tags."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "tag-home"
        self.first = tag_instances.create(self.root, "first").home
        self.shared = tag_instances.shared_mfs_home(self.root)
        self.memory_running = False
        self.tags_running: set[Path] = set()
        self.settings: dict[str, str] = {}
        self.lifecycle = Mock()
        self.lifecycle.process_for.side_effect = self.process_for
        self.lifecycle.read_config.side_effect = lambda _path: dict(self.settings)
        self.lifecycle.local_mfs_endpoint.side_effect = tag_cli.local_mfs_endpoint
        (self.first / "config").mkdir(exist_ok=True)
        (self.first / "config/settings.json").write_text("{}", encoding="utf-8")
        self.now = [0.0]
        self.start = Mock(return_value=0)
        self.start_memory = Mock(return_value=0)

    def process_for(self, path: Path):
        if path == self.shared / "mfs.json":
            return object() if self.memory_running else None
        return object() if path.parent.parent in self.tags_running else None

    def supervisor(self) -> autostart.Supervisor:
        return autostart.Supervisor(self.root, self.lifecycle, self.start,
                                    start_memory=self.start_memory, clock=lambda: self.now[0])

    def test_memory_is_restarted_when_a_kept_tag_needs_it(self) -> None:
        autostart.set_wanted(self.first, True)
        self.tags_running = {self.first}  # the bridge is still up; only MFS died
        supervisor = self.supervisor()
        self.assertTrue(supervisor.check_memory())
        self.start_memory.assert_called_once_with()
        self.memory_running = True
        self.assertFalse(supervisor.check_memory())
        self.start_memory.assert_called_once_with()

    def test_memory_started_by_hand_is_kept_even_with_no_tags(self) -> None:
        # `tag memory start` with no working Tag, then MFS dies.
        autostart.set_memory_wanted(self.shared, True)
        self.assertTrue(self.supervisor().check_memory())

    def test_memory_nobody_asked_for_is_left_alone(self) -> None:
        autostart.set_wanted(self.first, False)
        self.assertFalse(self.supervisor().check_memory())
        autostart.set_memory_wanted(self.shared, False)
        self.assertFalse(self.supervisor().check_memory())
        self.start_memory.assert_not_called()

    def test_stopping_memory_on_purpose_wins_over_a_stopped_tags_leftovers(self) -> None:
        autostart.set_memory_wanted(self.shared, False)
        self.assertFalse(self.supervisor().check_memory())

    def test_an_external_memory_server_is_never_started(self) -> None:
        autostart.set_wanted(self.first, True)
        self.settings = {"MFS_URL": "https://memory.example.com"}
        self.assertFalse(self.supervisor().check_memory())
        self.start_memory.assert_not_called()

    def test_failed_restarts_back_off_and_recover(self) -> None:
        autostart.set_memory_wanted(self.shared, True)
        self.start_memory.return_value = 1
        supervisor = self.supervisor()
        self.assertTrue(supervisor.check_memory())
        self.assertFalse(supervisor.check_memory())  # waiting
        self.now[0] = autostart.CHECK_SECONDS * 2
        self.assertTrue(supervisor.check_memory())
        self.now[0] += autostart.CHECK_SECONDS * 2  # a second failure doubles the wait
        self.assertFalse(supervisor.check_memory())
        self.now[0] += autostart.MAX_BACKOFF_SECONDS
        self.start_memory.return_value = 0
        self.assertTrue(supervisor.check_memory())
        self.memory_running = True
        self.now[0] += autostart.STABLE_SECONDS
        supervisor.check_memory()
        self.assertEqual(supervisor.failures, {})

    def test_memory_that_dies_right_after_starting_backs_off(self) -> None:
        # The store is unreachable: MFS starts, then exits at once.
        autostart.set_memory_wanted(self.shared, True)
        supervisor = self.supervisor()
        attempts = 0
        for _ in range(30):  # 30 minutes of checks
            attempts += supervisor.check_memory()
            self.now[0] += autostart.CHECK_SECONDS
        self.assertLessEqual(attempts, 5)

    def test_tags_and_memory_do_not_share_backoff(self) -> None:
        autostart.set_wanted(self.first, True)
        autostart.set_memory_wanted(self.shared, True)
        self.start_memory.return_value = 1
        supervisor = self.supervisor()
        supervisor.check_memory()
        self.assertEqual(supervisor.check(), ["first"])

    def test_the_login_service_checks_memory_before_the_tags(self) -> None:
        order: list[str] = []

        class Stop(Exception):
            pass

        class Recording:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def check_memory(self) -> None:
                order.append("memory")

            def check(self) -> None:
                order.append("tags")
                raise Stop

        with patch.object(autostart, "Supervisor", Recording), patch.object(autostart, "ensure_linger"), \
                redirect_stdout(StringIO()), self.assertRaises(Stop):
            autostart.run(self.root, Mock(), Path(self.temporary.name) / "source")
        self.assertEqual(order, ["memory", "tags"])

    def test_the_login_service_turns_linger_on_once_and_logs_what_is_left_to_do(self) -> None:
        class Stop(Exception):
            pass

        class Boom:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def check_memory(self) -> None:
                raise Stop

        output = StringIO()
        with patch.object(autostart, "Supervisor", Boom), patch.object(autostart, "mechanism", return_value="systemd"), \
                patch.object(autostart, "ensure_linger", return_value=False) as ensure, \
                patch.object(autostart, "linger_command", return_value="sudo loginctl enable-linger ops"), \
                redirect_stdout(output), self.assertRaises(Stop):
            autostart.run(self.root, Mock(), Path(self.temporary.name) / "source")
        ensure.assert_called_once_with("systemd")
        self.assertIn("sudo loginctl enable-linger ops", output.getvalue())

    def test_the_login_service_starts_memory_with_the_tool_path(self) -> None:
        captured = {}

        class Stop(Exception):
            pass

        class OneCheck:
            def __init__(self, _root, _lifecycle, _start, *, start_memory) -> None:
                captured["start_memory"] = start_memory

            def check_memory(self) -> None:
                captured["start_memory"]()
                raise Stop

        finished = Mock(returncode=0, stdout="", stderr="")
        with patch.object(autostart, "Supervisor", OneCheck), patch.object(autostart, "ensure_linger"), \
                patch.object(autostart.subprocess, "run", return_value=finished) as run, \
                redirect_stdout(StringIO()), self.assertRaises(Stop):
            autostart.run(self.root, Mock(), Path(self.temporary.name) / "source")
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["memory", "start"])
        self.assertEqual(run.call_args.kwargs["env"][autostart.SUPERVISED_ENV], "1")


class LingerTests(unittest.TestCase):
    """A systemd user service starts at boot only for an account with linger on."""

    def run_loginctl(self, stdout: str = "yes\n", returncode: int = 0):
        return Mock(returncode=returncode, stdout=stdout, stderr="")

    def test_reports_the_accounts_setting(self) -> None:
        with patch.object(autostart.shutil, "which", return_value="/usr/bin/loginctl"), \
                patch.object(autostart.subprocess, "run", return_value=self.run_loginctl("yes\n")):
            self.assertIs(autostart.linger_enabled(), True)
        with patch.object(autostart.shutil, "which", return_value="/usr/bin/loginctl"), \
                patch.object(autostart.subprocess, "run", return_value=self.run_loginctl("no\n")):
            self.assertIs(autostart.linger_enabled(), False)

    def test_unknown_when_it_cannot_be_read(self) -> None:
        with patch.object(autostart.shutil, "which", return_value=None):
            self.assertIsNone(autostart.linger_enabled())
        with patch.object(autostart.shutil, "which", return_value="/usr/bin/loginctl"), \
                patch.object(autostart.subprocess, "run", return_value=self.run_loginctl("", 1)):
            self.assertIsNone(autostart.linger_enabled())

    def test_only_systemd_has_a_linger_setting(self) -> None:
        for kind in ("launchd", "windows-run", "xdg-autostart"):
            with patch.object(autostart, "mechanism", return_value=kind), \
                    patch.object(autostart.subprocess, "run") as run:
                self.assertIsNone(autostart.linger_status(kind))
            run.assert_not_called()

    def test_enabling_linger_without_a_prompt_is_attempted_once(self) -> None:
        answers = [self.run_loginctl("no\n"), self.run_loginctl(""), self.run_loginctl("yes\n")]
        with patch.object(autostart.shutil, "which", return_value="/usr/bin/loginctl"), \
                patch.object(autostart.subprocess, "run", side_effect=answers) as run:
            self.assertIs(autostart.ensure_linger("systemd"), True)
        self.assertEqual(run.call_args_list[1].args[0], ["loginctl", "enable-linger"])

    def test_a_refusal_leaves_the_exact_command_to_run(self) -> None:
        answers = [self.run_loginctl("no\n"), self.run_loginctl("", 1), self.run_loginctl("no\n")]
        with patch.object(autostart.shutil, "which", return_value="/usr/bin/loginctl"), \
                patch.object(autostart.subprocess, "run", side_effect=answers):
            self.assertIs(autostart.ensure_linger("systemd"), False)
        with patch.dict(os.environ, {"USER": "ops"}):
            self.assertEqual(autostart.linger_command(), "sudo loginctl enable-linger ops")

    def test_linger_already_on_is_not_touched(self) -> None:
        with patch.object(autostart.shutil, "which", return_value="/usr/bin/loginctl"), \
                patch.object(autostart.subprocess, "run", return_value=self.run_loginctl("yes\n")) as run:
            self.assertIs(autostart.ensure_linger("systemd"), True)
        self.assertEqual(run.call_count, 1)


class ToolPathTests(unittest.TestCase):
    """Starts at login find `claude` and `codex` although login services get a minimal PATH."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "tag-home"

    @unittest.skipIf(os.name == "nt", "Windows starts login programs with the person's own PATH")
    def test_login_starts_search_the_usual_install_folders(self) -> None:
        with patch.dict(os.environ, {"HOME": "/Users/person"}):
            folders = autostart.tool_path(self.root, os.pathsep.join(["/usr/bin", "/bin"])).split(os.pathsep)
        self.assertEqual(folders[:3], ["/Users/person/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"])
        self.assertEqual(folders[3:], ["/usr/bin", "/bin"])

    def test_folders_from_the_persons_own_starts_come_first_and_accumulate(self) -> None:
        autostart.remember_path(self.root, os.pathsep.join(["/nvm/v22/bin", "relative/bin", "/usr/bin"]))
        autostart.remember_path(self.root, os.pathsep.join(["/opt/tools/bin", "/usr/bin"]))
        folders = autostart.tool_path(self.root, "/usr/bin").split(os.pathsep)
        self.assertEqual(folders[:3], ["/opt/tools/bin", "/usr/bin", "/nvm/v22/bin"])
        self.assertNotIn("relative/bin", folders)
        self.assertEqual(folders.count("/usr/bin"), 1)

    def test_an_unchanged_path_is_not_rewritten(self) -> None:
        autostart.remember_path(self.root, "/nvm/v22/bin")
        record = self.root / autostart.PATH_RECORD
        before = record.stat().st_mtime_ns
        with patch.object(autostart, "_write") as write:
            autostart.remember_path(self.root, "/nvm/v22/bin")
        write.assert_not_called()
        self.assertEqual(record.stat().st_mtime_ns, before)

    def test_a_damaged_record_is_ignored(self) -> None:
        (self.root / "state").mkdir(parents=True)
        (self.root / autostart.PATH_RECORD).write_text("not json", encoding="utf-8")
        self.assertIn("/usr/bin", autostart.tool_path(self.root, "/usr/bin").split(os.pathsep))
        autostart.remember_path(self.root, "/nvm/v22/bin")
        self.assertEqual(autostart.tool_path(self.root, "").split(os.pathsep)[0], "/nvm/v22/bin")

    def test_the_login_service_starts_tags_with_the_tool_path(self) -> None:
        autostart.remember_path(self.root, "/nvm/v22/bin")

        class Stop(Exception):
            pass

        class OneCheck:
            def __init__(self, _root, _lifecycle, start, **_options) -> None:
                self.start = start

            def check_memory(self) -> None:
                pass

            def check(self) -> None:
                self.start("first")
                raise Stop

        finished = Mock(returncode=0, stdout="", stderr="")
        with patch.object(autostart, "Supervisor", OneCheck), patch.object(autostart, "ensure_linger"), \
                patch.object(autostart.subprocess, "run", return_value=finished) as run, \
                patch.dict(os.environ, {"PATH": os.pathsep.join(["/usr/bin", "/bin"])}), \
                redirect_stdout(StringIO()), self.assertRaises(Stop):
            autostart.run(self.root, Mock(), Path(self.temporary_source()))
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment[autostart.SUPERVISED_ENV], "1")
        self.assertEqual(environment["PATH"].split(os.pathsep)[0], "/nvm/v22/bin")
        self.assertIn("/usr/bin", environment["PATH"].split(os.pathsep))

    def temporary_source(self) -> str:
        return str(self.root.parent / "source")


class ServiceDefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "tag-home"
        (self.root / "bin").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @unittest.skipIf(os.name == "nt", "launchd and the POSIX launcher exist only on macOS and Linux")
    def test_managed_install_uses_the_upgrade_stable_command(self) -> None:
        bin_dir = Path(self.temporary.name) / "bin"
        bin_dir.mkdir()
        (bin_dir / "tag").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.root / "bin/tag-launch.py").write_text("", encoding="utf-8")
        (self.root / "current.json").write_text(json.dumps({"bin_dir": str(bin_dir), "release": "r1"}), encoding="utf-8")
        with patch.object(autostart.os, "name", "posix"):
            command = autostart.service_command(self.root, Path("/src"))
        self.assertEqual(command, ["/bin/sh", str(bin_dir / "tag"), "autostart", "run"])

    @unittest.skipUnless(os.name == "nt", "the Windows Run-key command; CI runs it on Windows")
    def test_windows_runs_the_launcher_without_a_console(self) -> None:
        (self.root / "bin/tag-launch.py").write_text("", encoding="utf-8")
        (self.root / "current.json").write_text(json.dumps({"bin_dir": str(self.root / "bin"), "release": "r1"}), encoding="utf-8")
        python = Path(self.temporary.name) / "python" / "python.exe"
        python.parent.mkdir()
        python.write_text("", encoding="utf-8")
        python.with_name("pythonw.exe").write_text("", encoding="utf-8")
        with patch.object(autostart.sys, "_base_executable", str(python), create=True):
            command = autostart.service_command(self.root, Path("/src"))
        self.assertEqual(command, [str(python.with_name("pythonw.exe")), str(self.root / "bin/tag-launch.py"),
                                   "autostart", "run"])

    def test_source_checkout_runs_its_own_cli(self) -> None:
        command = autostart.service_command(self.root, Path("/src"))
        self.assertEqual(command[1:], [str(Path("/src/scripts/tag_cli.py")), "autostart", "run"])

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

    @unittest.skipIf(os.name == "nt", "launchd and the POSIX launcher exist only on macOS and Linux")
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

    @unittest.skipIf(os.name == "nt", "launchd and the POSIX launcher exist only on macOS and Linux")
    def test_failed_registration_is_reported(self) -> None:
        home = Path(self.temporary.name) / "user"
        with patch.object(autostart, "mechanism", return_value="launchd"), \
                patch.object(autostart.Path, "home", return_value=home), \
                patch.object(autostart.time, "sleep"), \
                patch.object(autostart.subprocess, "run",
                             return_value=Mock(returncode=5, stderr="Bootstrap failed", stdout="")):
            with self.assertRaisesRegex(RuntimeError, "Bootstrap failed"):
                autostart.enable(self.root, Path("/src"))

    @unittest.skipIf(os.name == "nt", "launchd and the POSIX launcher exist only on macOS and Linux")
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
            tag_config_path.write_text("{}", encoding="utf-8")
            with patch("scripts.tag_config.config_errors", return_value=[]), \
                    patch.object(tag_cli, "assert_unique_slack_app"), \
                    patch("scripts.tag_config.migrate_file_delivery"):
                code, _ = self.cli("t1-a1", "start")
        self.assertEqual(code, 0)
        start.assert_not_called()
        self.assertFalse(autostart.wanted(self.home))

    def start_until_readiness(self, environment: dict[str, str]) -> None:
        """Run `tag start` up to its first readiness step, then stop it there."""
        (self.home / "config/settings.json").write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, environment), \
                patch.object(tag_cli, "missing_runtime_dependencies", return_value=[]), \
                patch.object(tag_cli, "legacy_slack_ready", return_value=False), \
                patch.object(tag_cli, "read_config", return_value={}), \
                patch("scripts.tag_config.config_errors", return_value=[]), \
                patch.object(tag_cli, "assert_unique_slack_app"), \
                patch("scripts.tag_config.migrate_file_delivery"), \
                patch.object(tag_cli, "selected_target", side_effect=RuntimeError("stop here")), \
                self.assertRaisesRegex(RuntimeError, "stop here"):
            self.cli("t1-a1", "start")

    def test_a_persons_start_records_where_their_tools_live(self) -> None:
        self.start_until_readiness({"PATH": os.pathsep.join(["/nvm/v22/bin", "/usr/bin"])})
        folders = autostart.tool_path(self.root, "").split(os.pathsep)
        self.assertEqual(folders[0], "/nvm/v22/bin")
        # The Tag's own integrations folder stays out of the record the other Tags share.
        self.assertFalse([item for item in folders if item.endswith(os.path.join("integrations", "bin"))])

    def test_a_supervised_start_does_not_record_the_services_path(self) -> None:
        autostart.set_wanted(self.home, True)
        self.start_until_readiness({autostart.SUPERVISED_ENV: "1", "PATH": "/usr/bin"})
        self.assertFalse((self.root / autostart.PATH_RECORD).exists())

    def test_turning_autostart_on_records_where_tools_live(self) -> None:
        with patch.dict(os.environ, {"PATH": "/nvm/v22/bin"}), \
                patch.object(autostart, "enable", return_value={"enabled": True}):
            self.assertEqual(self.cli("autostart", "on", "--json")[0], 0)
        self.assertEqual(autostart.tool_path(self.root, "").split(os.pathsep)[0], "/nvm/v22/bin")

    def test_keep_records_choices_without_starting(self) -> None:
        code, output = self.cli("autostart", "keep", "t1-a1", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(autostart.wanted(self.home))
        self.assertEqual(json.loads(output)["tags"], [{"tag": "t1-a1", "keep_running": True}])
        with self.assertRaises(SystemExit), redirect_stdout(StringIO()), patch("sys.stderr", StringIO()):
            self.cli("autostart", "keep")

    def test_starting_memory_by_hand_keeps_it_running(self) -> None:
        shared = tag_instances.shared_mfs_home(self.root)
        with patch.object(tag_cli, "ensure_shared_memory"):
            self.assertEqual(self.cli("memory", "start")[0], 0)
        self.assertTrue(autostart.memory_wanted(shared))

    def test_stopping_memory_leaves_it_off(self) -> None:
        shared = tag_instances.shared_mfs_home(self.root)
        autostart.set_memory_wanted(shared, True)
        with patch.object(tag_cli, "ensure_shared_memory"):
            self.cli("memory", "start")
        with patch.object(tag_cli, "stop_process"), \
                patch.object(tag_cli, "process_for", return_value=object()), \
                patch.object(tag_cli, "bridge_processes", return_value=[]):
            self.assertEqual(self.cli("memory", "stop")[0], 0)
        self.assertFalse(autostart.memory_wanted(shared))

    def test_a_supervised_memory_start_never_changes_the_choice(self) -> None:
        shared = tag_instances.shared_mfs_home(self.root)
        autostart.set_memory_wanted(shared, False)
        with patch.dict(os.environ, {autostart.SUPERVISED_ENV: "1"}), \
                patch.object(tag_cli, "ensure_shared_memory"):
            self.assertEqual(self.cli("memory", "start")[0], 0)
        self.assertFalse(autostart.memory_wanted(shared))

    def test_status_reports_whether_memory_is_kept_running(self) -> None:
        autostart.set_wanted(self.home, True)
        with patch.object(autostart, "mechanism", return_value="launchd"), \
                patch.object(autostart.Path, "home", return_value=self.root / "user"):
            result = json.loads(self.cli("autostart", "--json")[1])
        self.assertEqual(result["memory"], {"keep_running": True})
        self.assertNotIn("linger", result)  # launchd has no such setting

    def test_memory_status_says_whether_it_is_restarted(self) -> None:
        shared = tag_instances.shared_mfs_home(self.root)
        with patch.object(tag_cli, "healthy", return_value=False):
            self.assertFalse(json.loads(self.cli("memory", "status", "--json")[1])["keep_running"])
            autostart.set_memory_wanted(shared, True)
            self.assertTrue(json.loads(self.cli("memory", "status", "--json")[1])["keep_running"])
            self.assertIn("Kept running", self.cli("memory", "status")[1])

    def test_a_missing_linger_setting_is_reported_with_the_command(self) -> None:
        with patch.object(autostart, "mechanism", return_value="systemd"), \
                patch.object(autostart, "linger_enabled", return_value=False), \
                patch.object(autostart, "linger_command", return_value="sudo loginctl enable-linger ops"), \
                patch.object(autostart.Path, "home", return_value=self.root / "user"):
            result = json.loads(self.cli("autostart", "--json")[1])
        self.assertEqual(result["linger"], {"enabled": False, "command": "sudo loginctl enable-linger ops"})

    def test_autostart_is_installation_wide(self) -> None:
        with self.assertRaises(SystemExit):
            with redirect_stdout(StringIO()), patch("sys.stderr", StringIO()):
                self.cli("t1-a1", "autostart")


if __name__ == "__main__":
    unittest.main()
