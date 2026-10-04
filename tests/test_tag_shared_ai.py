"""Installation-wide connection lifecycle and legacy restart recovery."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import tag_ai, tag_chatgpt, tag_cli
from scripts.tag_locks import LifecycleLock


class SharedAITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"TAG_HOME": str(self.root), "TAG_INSTANCE_HOME": str(self.root / "instance")})
        environment.start()
        self.addCleanup(environment.stop)

    def test_connection_changes_pause_and_restore_for_both_providers(self):
        for backend in ("codex", "claude"):
            with self.subTest(backend=backend):
                calls = []
                live = {"value": True}
                def lifecycle(action):
                    calls.append(action)
                    if action == "start":
                        # Browser operation must release the startup guard before restoring Tags.
                        with LifecycleLock(self.root / "state/ai-connection.lock"):
                            pass
                    live["value"] = action == "start"
                    return 0
                def sign_in(*args, **kwargs):
                    self.assertFalse(live["value"])
                    with self.assertRaises(RuntimeError):
                        LifecycleLock(self.root / "state/ai-connection.lock").acquire()
                    return {"state": "connected", "account": "fixture"}
                target = tag_ai.Target(self.root, "", lambda: live["value"], lifecycle, "Your Tags")
                with patch.object(tag_ai, "sign_in", side_effect=sign_in), redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(0, tag_ai.cli(["sign-in", backend], target, restart=True))
                self.assertEqual(["stop", "start"], calls)
                self.assertTrue(live["value"])

    def test_global_target_restores_only_previously_running_tags_after_partial_stop(self):
        state = {"one": True, "two": True, "idle": False}
        calls = []
        targets = {}
        for name in state:
            home = self.root / name
            home.mkdir()
            def lifecycle(action, name=name):
                calls.append((name, action))
                if name == "two" and action == "stop":
                    return 1
                state[name] = action == "start"
                return 0
            targets[name] = tag_ai.Target(home, name, lambda name=name: state[name], lifecycle)
        rows = [{"id": name, "home": str(t.home), "valid": True} for name, t in targets.items()]
        with patch.object(tag_cli.tag_instances, "discover", return_value=rows), \
             patch.object(tag_cli.tag_instances, "resolve", side_effect=lambda root, name: name), \
             patch.object(tag_cli, "_ai_target", side_effect=lambda name: targets[name]):
            target = tag_cli._global_ai_target(self.root)
            with patch.object(tag_ai, "sign_in") as sign_in, redirect_stdout(io.StringIO()):
                self.assertEqual(1, tag_ai.cli(["sign-in", "claude"], target, restart=True))
        sign_in.assert_not_called()
        self.assertEqual([("one", "stop"), ("two", "stop"), ("one", "start"), ("two", "start")], calls)
        self.assertFalse(state["idle"])

    def test_migration_retries_pending_restarts_after_data_was_published(self):
        store = tag_chatgpt.Store()
        tag_chatgpt.atomic_write(store.path, {"schema_version": 1, "mode": "codex", "active": None, "accounts": {}})
        checkpoint = self.root / "shared/ai/migration-v1.json"
        tag_chatgpt.atomic_write(checkpoint, {"version": 1, "restart": ["one", "two"]})
        targets = {name: tag_ai.Target(self.root / name, name, lambda: False, lambda action: 0) for name in ("one", "two")}
        with patch.object(tag_cli.tag_instances, "resolve", side_effect=lambda root, name: name), \
             patch.object(tag_cli, "_ai_target", side_effect=lambda name: targets[name]), \
             patch.object(tag_cli.subprocess, "call", side_effect=[1, 0]) as start:
            with self.assertRaisesRegex(RuntimeError, "two could not restart"):
                tag_cli._migrate_shared_ai(self.root, targets["one"].home)
            self.assertEqual(["one", "two"], tag_chatgpt.read_object(checkpoint)["restart"])
            tag_cli._migrate_shared_ai(self.root, targets["one"].home)
            self.assertEqual(["one"], tag_chatgpt.read_object(checkpoint)["restart"])
            self.assertEqual("1", start.call_args.kwargs["env"]["TAG_AI_MIGRATION_RESTART"])
        self.assertTrue((store.path.parent / "migration-v1.json").exists())

    def test_cli_rejects_named_tag_connections_before_reading_any_tag(self):
        for args in (["missing", "settings", "ai", "sign-in", "claude"], ["missing", "chatgpt", "use-codex"]):
            result = subprocess.run([os.sys.executable, str(tag_cli.ROOT / "scripts/tag_cli.py"), *args],
                                    capture_output=True, text=True)
            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("shared by all Tags", result.stderr)
            self.assertFalse((self.root / "tags").exists())
