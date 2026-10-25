from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import tag_cli, tag_connectors as connectors, tag_instances


class ConnectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"TAG_HOME": str(self.root)})
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



# Found before any test narrows PATH; only ever run with a temp CODEX_HOME.
CODEX = shutil.which("codex")
REAL_PATH = os.environ.get("PATH", "")


class ConnectorRunTests(unittest.TestCase):
    """Each run is given only the connectors its Tag may use (ADR 0014)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.codex_home, self.workdir = base / "tag", base / "codex", base / "workspace"
        for path in (self.root, self.codex_home, self.workdir, base / "claude", base / "bin"):
            path.mkdir()
        # No backend CLIs on PATH and temp configs: these tests must never reach the user's own.
        env = patch.dict(os.environ, {"TAG_HOME": str(self.root), "TAG_ID": "sales", "HOME": str(base),
                                      "CODEX_HOME": str(self.codex_home), "CLAUDE_CONFIG_DIR": str(base / "claude"),
                                      "PATH": str(base / "bin")})
        env.start()
        self.addCleanup(env.stop)
        self.node = base / "bin/node"
        self.node.write_text("#!/bin/sh\n")
        self.node.chmod(0o755)
        self.bridge = connectors.bridge_script(self.root)
        self.bridge.parent.mkdir(parents=True)
        self.bridge.write_text("")
        self.auth = str(connectors.auth_dir(self.root))
        tag_instances.create(self.root, "sales")
        self.store = connectors.Store(self.root)
        self.store.add("linear", url="https://mcp.linear.app/mcp")
        self.store.add("gmail", url="https://gmail.example/mcp")
        self.store.add("notes", command=["notes-mcp", "--read-only"], env=["NOTES_KEY"])
        self.store.set_allowed("linear", ["sales"], True)
        self.store.set_allowed("notes", ["sales"], True)
        self.sign_in("https://mcp.linear.app/mcp")

    def sign_in(self, url):
        path = connectors.sign_in_file(self.root, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")

    def bridge_args(self, url):
        return [str(self.bridge), url, "--auth-timeout", "10"]

    def test_sign_in_file_matches_mcp_remote_layout(self):
        # mcp-remote 0.15.2: <dir>/mcp-remote-v1/<md5 of the URL>_tokens.json
        self.assertEqual(connectors.sign_in_file(self.root, "https://mcp.linear.app/mcp"),
                         connectors.auth_dir(self.root) / "mcp-remote-v1/fcc436b0d1e0a1ed9a2b15bbd638eb13_tokens.json")

    def test_codex_gets_only_allowed_connectors_through_the_bridge(self):
        self.assertEqual(connectors.codex_run_args(self.workdir), [
            "-c", 'mcp_servers.linear={"command" = ' + json.dumps(str(self.node)) + ', "args" = ['
                  + ", ".join(json.dumps(arg) for arg in self.bridge_args("https://mcp.linear.app/mcp"))
                  + '], "env" = {"MCP_REMOTE_CONFIG_DIR" = ' + json.dumps(self.auth) + '}}',
            "-c", 'mcp_servers.notes={"command" = "notes-mcp", "args" = ["--read-only"], '
                  '"env_vars" = ["NOTES_KEY"]}'])

    def test_claude_gets_allowed_connectors_and_the_workspace_wins(self):
        self.assertEqual(connectors.claude_mcp_servers(self.workdir), {
            "linear": {"type": "stdio", "command": str(self.node),
                       "args": self.bridge_args("https://mcp.linear.app/mcp"),
                       "env": {"MCP_REMOTE_CONFIG_DIR": self.auth}},
            "notes": {"type": "stdio", "command": "notes-mcp", "args": ["--read-only"],
                      "env": {"NOTES_KEY": "${NOTES_KEY}"}}})
        mine = {"command": "my-linear"}
        (self.workdir / ".mcp.json").write_text(json.dumps({"mcpServers": {"linear": mine}}))
        self.assertEqual(connectors.claude_mcp_servers(self.workdir)["linear"], mine)
        (self.workdir / ".codex").mkdir()
        (self.workdir / ".codex/config.toml").write_text('[mcp_servers.linear]\ncommand = "my-linear"\n')
        self.assertEqual([argument.split("=")[0] for argument in connectors.codex_run_args(self.workdir)],
                         ["-c", "mcp_servers.notes"])

    def test_remote_connectors_wait_for_sign_in_and_the_bridge(self):
        self.store.set_allowed("gmail", ["sales"], True)
        definitions, skipped = connectors.run_plan()
        self.assertEqual(set(definitions), {"linear", "notes"})
        self.assertEqual(skipped, {"gmail": "Not signed in; run tag connector sign-in gmail."})
        self.bridge.unlink()
        definitions, skipped = connectors.run_plan()
        self.assertEqual(set(definitions), {"notes"})
        self.assertEqual(set(skipped), {"linear", "gmail"})
        self.assertIn("tag doctor", skipped["linear"])

    def test_without_connectors_the_workspace_config_passes_as_before(self):
        self.store.forget_tag("sales")
        self.assertIsNone(connectors.claude_mcp_servers(self.workdir))
        (self.workdir / ".mcp.json").write_text('{"mcpServers": {}}')
        self.assertEqual(connectors.claude_mcp_servers(self.workdir), self.workdir / ".mcp.json")
        self.assertEqual(connectors.codex_run_args(self.workdir), [])

    def test_nothing_outside_a_tag_run_and_bad_files_fail(self):
        with patch.dict(os.environ, {"TAG_ID": ""}):
            self.assertEqual(connectors.codex_run_args(self.workdir), [])
            self.assertIsNone(connectors.claude_mcp_servers(self.workdir))
        (self.workdir / ".mcp.json").write_text("{")
        with self.assertRaisesRegex(connectors.ConnectorError, "Couldn't read"):
            connectors.claude_mcp_servers(self.workdir)
        connectors.store_path(self.root).write_text("{")
        with self.assertRaisesRegex(connectors.ConnectorError, "Cannot read the connector list"):
            connectors.codex_run_args(self.workdir)

    def test_codex_workspace_args_add_connectors_in_any_folder(self):
        from scripts import tag_paths
        self.assertEqual(tag_paths.codex_workspace_args(self.workdir), connectors.codex_run_args(self.workdir))

    def test_rename_moves_access_and_remove_forgets_it(self):
        self.store.rename_tag("sales", "acme-sales")
        self.assertEqual(self.store.allowed_for("acme-sales"), {"linear", "notes"})
        self.assertEqual(self.store.allowed_for("sales"), set())
        self.store.forget_tag("acme-sales")
        self.assertEqual(self.store.allowed_for("acme-sales"), set())

    @unittest.skipUnless(CODEX, "codex CLI not installed")
    def test_real_codex_accepts_the_definitions(self):
        listed = subprocess.run([CODEX, "mcp", "list", "--json", *connectors.codex_run_args(self.workdir)],
                                capture_output=True, text=True, timeout=60, check=True,
                                env={**os.environ, "PATH": REAL_PATH})
        rows = {row["name"]: row for row in json.loads(listed.stdout)}
        self.assertEqual(set(rows), {"linear", "notes"})
        self.assertEqual(rows["linear"]["transport"]["args"], self.bridge_args("https://mcp.linear.app/mcp"))
        self.assertEqual(rows["linear"]["transport"]["env"], {"MCP_REMOTE_CONFIG_DIR": self.auth})
        self.assertEqual(rows["notes"]["transport"]["env_vars"], ["NOTES_KEY"])

if __name__ == "__main__":
    unittest.main()
