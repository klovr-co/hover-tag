from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import tag_cli, tag_config, tag_instances, tag_knowledge as knowledge, tag_memory, tag_transfer
from scripts.tag_memory import render_context

try:
    import dulwich  # noqa: F401
    HAVE_DULWICH = True
except ImportError:
    HAVE_DULWICH = False

# Built at run time so no token-shaped literal is committed.
BOT_TOKEN = "xox" + "b-1234567890-" + "abcdefghijklmnop"
APP_TOKEN = "xa" + "pp-1-A0RESE02-" + "1234567890-abcdef"
API_KEY = "s" + "k-" + "abcdefghijklmnopqrstuvwxyz123456"


class TransferTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.old = self.base / "old" / "Tag"
        self.new = self.base / "new" / "Tag"
        environment = patch.dict(os.environ, {"TAG_HOME": str(self.old)})
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("OPENTAG_CALLER_ID", "OPENTAG_MEMORY_ROOT", "OPENTAG_MEMORY_HISTORY", "TAG_INSTANCE_HOME",
                     "OPENTAG_MEMORY_RECEIPTS"):
            os.environ.pop(name, None)
        self.context = tag_instances.create(self.old, "t0klovr1-a0rese02")
        tag_config.save_config(self.context.home / "config/settings.json", {
            "SLACK_TEAM_ID": "T0KLOVR1", "SLACK_APP_ID": "A0RESE02", "SLACK_BOT_TOKEN": BOT_TOKEN,
            "SLACK_APP_TOKEN": APP_TOKEN, "OPENTAG_CODEX_API_KEY": API_KEY, "OPENTAG_BOT_NAME": "Maya's Tag",
            "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5", "OPENTAG_THREAD_MAX_CONTEXT_TOKENS": "200000",
            "MFS_SLACK_TOKEN": BOT_TOKEN, "OPENTAG_WORKDIR": str(self.context.workspace),
            "SLACK_CHANNEL_IDS": "C0OPS0001"})
        (self.context.home / "config/credentials").mkdir(parents=True, exist_ok=True)
        (self.context.home / "config/credentials/mfs-slack-token").write_text(BOT_TOKEN, encoding="utf-8")
        self.places = knowledge.Places.for_tag(self.old, self.context.home, self.context.workspace, self.context.tag_id)
        store = tag_memory.MemoryStore(self.places.memory, "C0OPS0001", "U0MAYA001", places=self.places,
                                       thread="hover-tag://tag/t0klovr1-a0rese02/thread/C0OPS0001/1760600000.000200")
        store.save("digest-format", "Monday digest: five linked bullets.")
        store.save("digest-sources", "Merged pull requests and the #launch thread.", kind="note")
        store.change("digest-format", "Monday digest: five linked bullets, no emoji.",
                     version=store.get("digest-format")[1]["version"])
        skill = self.context.workspace / ".claude/skills/weekly-digest"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: weekly-digest\n---\nPost the digest.\n", encoding="utf-8")
        (skill / "helper.py").write_text("print('digest')\n", encoding="utf-8")
        threads = self.context.home / "state/threads"
        (threads / "requests").mkdir(parents=True)
        (threads / "C0OPS0001-1760600000.000200.jsonl").write_text(
            json.dumps({"role": "user", "text": f"here is the bot token {BOT_TOKEN}"}) + "\n", encoding="utf-8")
        (threads / "settings.json").write_text('{"retention_days": 90}\n', encoding="utf-8")
        (threads / "requests/reply-1.json").write_text("{}", encoding="utf-8")
        (self.context.home / "routines.json").write_text(json.dumps({"schema_version": 1, "routines": [
            {"id": "r-1", "name": "Weekly digest", "enabled": True}]}), encoding="utf-8")
        (self.context.workspace / ".claude/settings.local.json").write_text(json.dumps(
            {"permissions": {"deny": ["Bash(git push:*)"]}, "env": {"OPENAI_API_KEY": API_KEY}}), encoding="utf-8")

    def export(self, *extra: str) -> dict:
        out = io.StringIO()
        with redirect_stdout(out):
            code = tag_transfer.run_export(self.old, self.context, ["--output", str(self.base), "--json", *extra])
        result = json.loads(out.getvalue())
        self.assertEqual(0, code, result)
        return result["export"]

    def import_into(self, root: Path, path: str) -> tuple[int, dict]:
        out = io.StringIO()
        with patch.dict(os.environ, {"TAG_HOME": str(root)}), redirect_stdout(out):
            code = tag_transfer.run_import(root, [path, "--json"])
        return code, json.loads(out.getvalue())


class ExportTests(TransferTestCase):
    def test_the_file_never_holds_passwords_or_tokens(self) -> None:
        exported = self.export()
        with zipfile.ZipFile(exported["path"]) as archive:
            blob = b"".join(archive.read(name) for name in archive.namelist())
            names = archive.namelist()
        for secret in (BOT_TOKEN, APP_TOKEN, API_KEY):
            self.assertNotIn(secret.encode(), blob)
        self.assertNotIn(str(self.context.workspace).encode(), blob)
        self.assertFalse(any("credentials" in name or "requests/" in name for name in names))
        self.assertIn("threads/C0OPS0001-1760600000.000200.jsonl", exported["redacted"])
        settings = json.loads(zipfile.ZipFile(exported["path"]).read("settings/settings.json"))
        self.assertEqual({"OPENTAG_BOT_NAME", "OPENTAG_DEFAULT_MODEL", "OPENTAG_THREAD_MAX_CONTEXT_TOKENS", "SLACK_CHANNEL_IDS"},
                         set(settings))
        self.assertEqual(0o600, Path(exported["path"]).stat().st_mode & 0o777)

    def test_it_holds_memory_skills_threads_and_settings(self) -> None:
        exported = self.export()
        self.assertTrue(exported["path"].endswith(".hovertag"))
        self.assertEqual({"team": "T0KLOVR1", "app": "A0RESE02", "workspace_name": None}, exported["slack"])
        with zipfile.ZipFile(exported["path"]) as archive:
            names = set(archive.namelist())
        self.assertLessEqual({"manifest.json", "memory/channels/C0OPS0001/core.md",
                              "memory/channels/C0OPS0001/notes/digest-sources.md", "skills/claude/weekly-digest/SKILL.md",
                              "skills/claude/weekly-digest/helper.py", "threads/settings.json", "settings/routines.json",
                              "settings/claude-permissions.json"}, names)
        self.assertEqual(1, exported["contents"]["skills"])

    @unittest.skipUnless(HAVE_DULWICH, "needs dulwich")
    def test_history_with_a_token_is_refused_unless_left_out(self) -> None:
        tag_memory.MemoryStore(self.places.memory, "C0OPS0001", "U0MAYA001", places=self.places, thread="").save(
            "oops", f"The token is {BOT_TOKEN}", kind="note")
        out = io.StringIO()
        with redirect_stdout(out):
            code = tag_transfer.run_export(self.old, self.context, ["--output", str(self.base), "--json"])
        self.assertEqual((1, "history_has_token"), (code, json.loads(out.getvalue())["code"]))
        exported = self.export("--without-history")
        with zipfile.ZipFile(exported["path"]) as archive:
            self.assertFalse(any(name.startswith("history/") for name in archive.namelist()))
            self.assertNotIn(BOT_TOKEN.encode(), b"".join(archive.read(n) for n in archive.namelist()))


@unittest.skipUnless(HAVE_DULWICH, "needs dulwich")
class ImportTests(TransferTestCase):
    def test_import_on_another_computer_keeps_what_the_tag_knows(self) -> None:
        exported = self.export()
        before = render_context(self.places.memory, "C0OPS0001")
        code, result = self.import_into(self.new, exported["path"])
        self.assertEqual(0, code, result)
        imported = result["imported"]
        context = tag_instances.resolve(self.new, imported["tag"])
        places = knowledge.Places.for_tag(self.new, context.home, context.workspace, context.tag_id)
        self.assertEqual(before, render_context(places.memory, "C0OPS0001"))
        history = knowledge.open_history(places)
        original = knowledge.open_history(self.places)
        self.assertEqual([c.id for c in original.log()], [c.id for c in history.log()], "the same history, no new changes")
        self.assertTrue((context.workspace / ".claude/skills/weekly-digest/helper.py").is_file())
        self.assertTrue((context.home / "state/threads/C0OPS0001-1760600000.000200.jsonl").is_file())
        routines = json.loads((context.home / "routines.json").read_text(encoding="utf-8"))["routines"]
        self.assertEqual(False, routines[0]["enabled"])
        settings = tag_config.load_config(context.home / "config/settings.json")
        self.assertNotIn("SLACK_BOT_TOKEN", settings)
        self.assertNotIn("SLACK_APP_ID", settings)
        self.assertEqual("Maya's Tag", settings["OPENTAG_BOT_NAME"])
        self.assertEqual(f"tag {imported['tag']} setup", imported["next"])
        self.assertEqual("A0RESE02", imported["slack"]["app"])
        permissions = json.loads((context.workspace / ".claude/settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual({"permissions": {"deny": ["Bash(git push:*)"]}}, permissions)

    def test_the_same_tag_is_not_imported_twice_on_one_computer(self) -> None:
        exported = self.export()
        code, result = self.import_into(self.old, exported["path"])
        self.assertEqual((1, "already_here"), (code, result["code"]))

    def test_unsafe_or_foreign_files_are_refused(self) -> None:
        bad = self.base / "bad.hovertag"
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": tag_transfer.FORMAT, "version": 1, "tag": {}}))
            archive.writestr("memory/../../escape.md", "x")
        code, result = self.import_into(self.new, str(bad))
        self.assertEqual((1, "unsafe_export"), (code, result["code"]))
        self.assertFalse((self.new / "instances").exists() and any((self.new / "instances").iterdir()))
        other = self.base / "other.zip"
        with zipfile.ZipFile(other, "w") as archive:
            archive.writestr("readme.txt", "hello")
        self.assertEqual("not_an_export", self.import_into(self.new, str(other))[1]["code"])

    def test_the_commands_are_reachable_from_tag(self) -> None:
        out = io.StringIO()
        with patch.object(sys, "argv", ["tag", self.context.tag_id, "export", "--output", str(self.base), "--json"]), \
                redirect_stdout(out):
            self.assertEqual(0, tag_cli.main())
        path = json.loads(out.getvalue())["export"]["path"]
        out = io.StringIO()
        with patch.dict(os.environ, {"TAG_HOME": str(self.new)}), patch.object(sys, "argv", ["tag", "import", path, "--json"]), \
                redirect_stdout(out):
            self.assertEqual(0, tag_cli.main())
        imported = json.loads(out.getvalue())
        self.assertTrue(imported["ok"])
        self.assertIn("tag-export", tag_cli.CAPABILITIES)
        # Tag.app reads protocol/examples/tag-export.json and tag-import.json.
        examples = Path(__file__).resolve().parents[1] / "protocol/examples"
        promised_export = json.loads((examples / "tag-export.json").read_text(encoding="utf-8"))["export"]
        promised_import = json.loads((examples / "tag-import.json").read_text(encoding="utf-8"))["imported"]
        exported = tag_transfer.export_tag(self.old, self.context, self.base / "again", history=False)
        self.assertEqual([], sorted(set(promised_export) - set(exported)))
        self.assertEqual([], sorted(set(promised_import) - set(imported["imported"])))


if __name__ == "__main__":
    unittest.main()
