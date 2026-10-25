from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import tag_cli, tag_connectors as connectors, tag_instances


class ConnectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # No backend CLIs on PATH and temp configs, so the real ~/.claude* and ~/.codex stay untouched.
        for name in ("bin", "home", "claude", "codex"):
            (self.root / name).mkdir()
        env = patch.dict(os.environ, {"TAG_HOME": str(self.root), "PATH": str(self.root / "bin"),
                                      "HOME": str(self.root / "home"),
                                      "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
                                      "CODEX_HOME": str(self.root / "codex")})
        env.start()
        self.addCleanup(env.stop)
        self.store = connectors.Store(self.root)

    def run_cli(self, *arguments: str) -> dict:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(connectors.cli([*arguments, "--json"], root=self.root), 0)
        return json.loads(out.getvalue())

    def test_new_connector_is_allowed_for_no_tag(self):
        row = self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")["connector"]
        self.assertEqual({backend: found["state"] for backend, found in row.pop("backends").items()},
                         {"claude": "pending", "codex": "pending"})
        self.assertEqual(row, {"name": "linear", "transport": "http", "url": "https://mcp.linear.app/mcp",
                               "allowed_tags": [], "added_at": row["added_at"]})
        self.assertEqual(self.store.allowed_for("default"), set())

    def test_list_show_and_remove(self):
        self.assertEqual(self.run_cli("list"), {"schema_version": 1, "connectors": []})
        self.run_cli("add", "notes", "--command", "npx -y notes-mcp --read-only", "--env", "NOTES_KEY")
        shown = self.run_cli("show", "notes")["connector"]
        self.assertEqual((shown["command"], shown["args"], shown["env"]),
                         ("npx", ["-y", "notes-mcp", "--read-only"], ["NOTES_KEY"]))
        self.assertEqual([row["name"] for row in self.run_cli("list")["connectors"]], ["notes"])
        self.run_cli("remove", "notes")
        self.assertEqual(self.run_cli("list")["connectors"], [])
        with self.assertRaisesRegex(connectors.ConnectorError, "No connector named 'notes'"):
            self.store.get("notes")

    def test_allow_and_deny_resolve_tags_and_reject_unknown_ones(self):
        tag_instances.create(self.root, "sales")
        self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")
        self.assertEqual(self.run_cli("allow", "linear", "sales")["connector"]["allowed_tags"], ["sales"])
        # Allowing twice keeps one entry.
        self.assertEqual(self.run_cli("allow", "linear", "sales")["connector"]["allowed_tags"], ["sales"])
        self.assertEqual(self.store.allowed_for("sales"), {"linear"})
        self.assertEqual(self.store.allowed_for("support"), set())
        with self.assertRaisesRegex(connectors.ConnectorError, "No Tag named 'support'"):
            connectors.cli(["allow", "linear", "support"], root=self.root)
        self.assertEqual(self.run_cli("deny", "linear", "sales")["connector"]["allowed_tags"], [])

    def test_rejects_bad_names_urls_and_secret_values(self):
        cases = [
            (["add", "Linear", "--url", "https://x.example/mcp"], "lowercase"),
            (["add", "a__b", "--url", "https://x.example/mcp"], "single _"),
            (["add", "x", "--url", "http://x.example/mcp"], "https://"),
            (["add", "x", "--url", "https://user:pw@x.example/mcp"], "sign-in details"),
            (["add", "x", "--url", "ftp://x.example"], "https://"),
            (["add", "x"], "exactly one"),
            (["add", "x", "--url", "https://x.example/mcp", "--command", "run"], "exactly one"),
            (["add", "x", "--command", "run", "--env", "API_KEY=secret"], "only the variable's name"),
            (["add", "x", "--url", "https://x.example/mcp", "--env", "API_KEY"], "only for --command"),
            (["show"], "needs a connector name"),
            (["allow", "x"], "Name the Tags"),
            (["remove", "x", "--url", "https://x.example/mcp"], "only for tag connector add"),
            (["frobnicate"], "invalid choice"),
        ]
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(connectors.ConnectorError, message):
                    connectors.cli(arguments, root=self.root)
        self.assertFalse(connectors.store_path(self.root).exists())
        # A local server may use plain http.
        self.run_cli("add", "local", "--url", "http://127.0.0.1:8123/mcp")

    def test_duplicate_name_is_refused(self):
        self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")
        with self.assertRaisesRegex(connectors.ConnectorError, "already exists"):
            connectors.cli(["add", "linear", "--url", "https://other.example/mcp"], root=self.root)

    def test_store_holds_no_values_and_newer_schema_is_refused(self):
        self.run_cli("add", "notes", "--command", "notes-mcp", "--env", "NOTES_KEY")
        path = connectors.store_path(self.root)
        saved = json.loads(path.read_text())
        self.assertEqual(saved["connectors"]["notes"]["env"], ["NOTES_KEY"])
        path.write_text(json.dumps({"version": 2, "connectors": {}}))
        with self.assertRaisesRegex(connectors.ConnectorError, "newer Tag"):
            self.store.list()
        path.write_text("{")
        with self.assertRaisesRegex(connectors.ConnectorError, "Cannot read the connector list"):
            self.store.list()

    def test_tag_cli_dispatches_and_refuses_a_tag_name(self):
        out = io.StringIO()
        with patch("sys.argv", ["tag", "connector", "list", "--json"]), redirect_stdout(out):
            self.assertEqual(tag_cli._run_cli(), 0)
        self.assertEqual(json.loads(out.getvalue())["connectors"], [])
        self.assertIn("connector", tag_instances.RESERVED_NAMES)
        with patch("sys.argv", ["tag", "sales", "connector", "list"]), \
                patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            tag_cli._run_cli()


if __name__ == "__main__":
    unittest.main()
