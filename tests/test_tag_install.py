from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.tag_install import install, unpack_release
from scripts.tag_paths import codex_workspace_args, initialize, tag_home
from scripts.tag_cli import process_for, read_config, start_process, stop_process
from scripts.tag_migrate import legacy_config, migrate
from scripts.opentag_setup import render_env

ROOT = Path(__file__).resolve().parents[1]


class TagHomeTests(unittest.TestCase):
    def test_migration_preserves_originals_and_existing_skills(self):
        with tempfile.TemporaryDirectory() as temp:
            home, source = Path(temp) / "home", Path(temp) / "old checkout"
            initialize(home)
            skill = source / ".codex/skills/custom/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("custom")
            old = source / ".env"
            old.write_text(render_env({"OPENTAG_WORKDIR": str(source), "SLACK_BOT_TOKEN": "quote'and\nnewline"}))
            self.assertEqual(legacy_config(old)["SLACK_BOT_TOKEN"], "quote'and\nnewline")
            migrate(source, home)
            self.assertEqual(json.loads((home / "config/settings.json").read_text())["OPENTAG_WORKDIR"], str(home / "workspace"))
            copied = home / "workspace/.agents/skills/custom/SKILL.md"
            self.assertEqual(copied.read_text(), "custom")
            copied.write_text("edited")
            migrate(source, home)
            self.assertEqual(copied.read_text(), "edited")
            self.assertEqual(skill.read_text(), "custom")
            self.assertTrue(old.exists())

    def test_existing_command_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = root / ("tag.cmd" if os.name == "nt" else "tag")
            command.write_text("another program")
            with self.assertRaises(RuntimeError):
                install(ROOT, root / "home", root, dependencies=False)
            self.assertEqual(command.read_text(), "another program")
            self.assertFalse((root / "home").exists())

    def test_platform_defaults_and_override(self):
        fake = Path(Path.cwd().anchor) / "users/test"
        with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", return_value=fake):
            with patch("sys.platform", "darwin"):
                self.assertEqual(tag_home(), fake / "Library/Application Support/Tag")
            with patch("sys.platform", "linux"):
                self.assertEqual(tag_home(), fake / ".local/share/tag")
            with patch("sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": str(fake / "local")}):
                self.assertEqual(tag_home(), fake / "local/Tag")
            with patch.dict(os.environ, {"TAG_HOME": str(fake / "custom/tag")}):
                self.assertEqual(tag_home(), fake / "custom/tag")
            with patch.dict(os.environ, {"TAG_HOME": "relative"}):
                with self.assertRaises(ValueError):
                    tag_home()

    def test_scoped_mcp_overlay_preserves_global_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            initialize(home)
            (home / "workspace/.codex/config.toml").write_text('[mcp_servers.example]\ncommand="python"\nargs=["server.py"]\n')
            with patch.dict(os.environ, {"TAG_HOME": str(home), "CODEX_HOME": "global-config"}):
                args = codex_workspace_args(home / "workspace")
                self.assertIn('mcp_servers.example=', args[1])
                self.assertEqual(os.environ["CODEX_HOME"], "global-config")
                self.assertEqual(codex_workspace_args(home / "another-project"), [])

    def test_config_is_data_not_executable_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "settings.json"
            config.write_text(json.dumps({"SLACK_BOT_TOKEN": "$(do-not-execute)"}))
            self.assertEqual(read_config(config)["SLACK_BOT_TOKEN"], "$(do-not-execute)")
            config.write_text('{"PATH":"unexpected"}')
            with self.assertRaises(ValueError):
                read_config(config)

    def test_stop_works_even_when_configuration_is_invalid(self):
        from scripts.tag_cli import main
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            initialize(home)
            (home / "config/settings.json").write_text("invalid JSON")
            with patch.dict(os.environ, {"TAG_HOME": str(home)}), patch.object(sys, "argv", ["tag", "stop"]):
                self.assertEqual(main(), 0)

    def test_archive_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape", "bad")
            with self.assertRaises(ValueError):
                unpack_release(archive, root / "output")
            self.assertFalse((root / "escape").exists())

    def test_install_upgrade_and_run_without_source(self):
        with tempfile.TemporaryDirectory(prefix="Tag install with spaces ") as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            first = install(ROOT, home, bin_dir, dependencies=False)
            skill = home / "workspace/.agents/skills/personal/SKILL.md"
            skill.parent.mkdir()
            skill.write_text("personal skill")
            config = home / "config/settings.json"
            config.write_text('{"OPENTAG_BACKEND":"codex"}')
            second = install(ROOT, home, bin_dir, dependencies=False)
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads((home / "previous.json").read_text())["release"], first.name)
            self.assertEqual(skill.read_text(), "personal skill")
            self.assertEqual(config.read_text(), '{"OPENTAG_BACKEND":"codex"}')
            command = bin_dir / ("tag.cmd" if os.name == "nt" else "tag")
            result = subprocess.run([str(command), "version"], cwd=root, capture_output=True, text=True, check=True)
            self.assertIn("Tag v", result.stdout)
            result = subprocess.run([str(command), "paths"], cwd=root, capture_output=True, text=True, check=True)
            self.assertEqual(Path(json.loads(result.stdout)["workspace"]).resolve(), (home / "workspace").resolve())
            self.assertFalse((second / ".git").exists())
            self.assertFalse((second / ".env").exists())
            # Rollback chooses the old code without reverting persistent settings.
            environment = dict(os.environ, TAG_HOME=str(home))
            from scripts.tag_cli import main
            with patch.dict(os.environ, environment), patch.object(sys, "argv", ["tag", "rollback"]):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads((home / "current.json").read_text())["release"], first.name)
            self.assertEqual(skill.read_text(), "personal skill")

    def test_failed_upgrade_keeps_current_release(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            bin_dir = Path(temp) / "bin"
            install(ROOT, home, bin_dir, dependencies=False)
            previous = (home / "current.json").read_text()
            with patch("subprocess.run", side_effect=RuntimeError("dependency installation failed")), patch("shutil.which", return_value="uv"):
                with self.assertRaises(RuntimeError):
                    install(ROOT, home, bin_dir)
            self.assertEqual((home / "current.json").read_text(), previous)

    def test_background_lifecycle_and_stale_pid_safety(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            initialize(home)
            try:
                self.assertTrue(start_process(home, "test", [sys.executable, "-c", "import time; time.sleep(90)"]))
                self.assertFalse(start_process(home, "test", ["must-not-run"]))
                self.assertIsNotNone(process_for(home / "state/test.json"))
            finally:
                stop_process(home, "test")
            self.assertIsNone(process_for(home / "state/test.json"))
            record = home / "state/test.json"
            record.write_text(json.dumps({"pid": os.getpid(), "created": 0}))
            stop_process(home, "test")
            self.assertFalse(record.exists())


if __name__ == "__main__":
    unittest.main()
