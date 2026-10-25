from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import tag_connector_backends as backends, tag_connectors as connectors

# Fake CLIs: they log argv to $FAKE_LOG and keep their servers in a JSON file
# under the temp config directory, shaped like the real CLIs' output.
FAKE_CLAUDE = """
import json, os, sys
from pathlib import Path
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["claude", *sys.argv[1:]]) + "\\n")
path = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"
data = json.loads(path.read_text()) if path.exists() else {}
servers = data.setdefault("mcpServers", {})
action, name = sys.argv[2], sys.argv[-2 if sys.argv[2] == "add-json" else -1]
if os.environ.get("FAKE_FAIL") == "claude":
    sys.exit("claude: something went wrong")
if action == "add-json":
    if name in servers:
        sys.exit(f"MCP server {name} already exists in user config")
    servers[name] = json.loads(sys.argv[-1])
elif action == "remove":
    if name not in servers:
        sys.exit(f'No MCP server named "{name}" in user scope')
    del servers[name]
path.write_text(json.dumps(data))
"""

FAKE_CODEX = """
import json, os, sys
from pathlib import Path
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["codex", *sys.argv[1:]]) + "\\n")
path = Path(os.environ["CODEX_HOME"]) / "servers.json"
servers = json.loads(path.read_text()) if path.exists() else {}
action = sys.argv[2]
if os.environ.get("FAKE_FAIL") == "codex" and action != "list":
    sys.exit("Error: something went wrong")
if action == "list":
    print(json.dumps([{"name": name, "enabled": True, "disabled_reason": None, "transport": transport,
                       "startup_timeout_sec": None, "auth_status": "unknown"}
                      for name, transport in sorted(servers.items())]))
elif action == "add":
    name, rest = sys.argv[3], sys.argv[4:]
    if rest[0] == "--url":
        servers[name] = {"type": "streamable_http", "url": rest[1], "bearer_token_env_var": None}
    else:
        servers[name] = {"type": "stdio", "command": rest[1], "args": rest[2:], "env": None,
                         "env_vars": [], "cwd": None}
elif action == "remove":
    servers.pop(sys.argv[3], None)
path.write_text(json.dumps(servers))
if action == "add":
    print(f"Added global MCP server '{name}'.", flush=True)
    if os.environ.get("FAKE_OAUTH"):
        # Like the real CLI, an OAuth server starts a sign-in flow that waits for a browser.
        import time
        time.sleep(120)
"""


class FakeBackendsCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.bin, home = base / "tag", base / "bin", base / "home"
        self.claude_dir, self.codex_home = base / "claude", base / "codex"
        for path in (self.root, self.bin, home, self.claude_dir, self.codex_home):
            path.mkdir()
        self.log = base / "argv.jsonl"
        self.log.touch()
        # HOME points at a temp dir too, so nothing can reach the real ~/.claude* or ~/.codex.
        env = patch.dict(os.environ, {"TAG_HOME": str(self.root), "HOME": str(home), "PATH": str(self.bin),
                                      "CLAUDE_CONFIG_DIR": str(self.claude_dir),
                                      "CODEX_HOME": str(self.codex_home), "FAKE_LOG": str(self.log)})
        env.start()
        self.addCleanup(env.stop)
        self.install("claude", FAKE_CLAUDE)
        self.install("codex", FAKE_CODEX)

    def install(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)

    def uninstall(self, name: str) -> None:
        (self.bin / name).unlink()

    def run_cli(self, *arguments: str) -> dict:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(connectors.cli([*arguments, "--json"], root=self.root), 0)
        return json.loads(out.getvalue())

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def writes(self) -> list[list[str]]:
        return [call for call in self.calls() if call[2] in {"add", "add-json", "remove"}]

    def claude_servers(self) -> dict:
        path = self.claude_dir / ".claude.json"
        return json.loads(path.read_text()).get("mcpServers", {}) if path.exists() else {}

    def codex_servers(self) -> dict:
        path = self.codex_home / "servers.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def edit_claude(self, change) -> None:
        path = self.claude_dir / ".claude.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        change(data.setdefault("mcpServers", {}))
        path.write_text(json.dumps(data))

    def edit_codex(self, change) -> None:
        servers = self.codex_servers()
        change(servers)
        (self.codex_home / "servers.json").write_text(json.dumps(servers))


class ConnectorBackendTests(FakeBackendsCase):
    def test_add_writes_both_backends_and_records_ownership(self):
        result = self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")
        self.assertEqual(result["connector"]["backends"], {"claude": {"state": "written"},
                                                           "codex": {"state": "written"}})
        self.assertEqual(self.writes(), [
            ["claude", "mcp", "add-json", "--scope", "user", "linear",
             '{"type":"http","url":"https://mcp.linear.app/mcp"}'],
            ["codex", "mcp", "add", "linear", "--url", "https://mcp.linear.app/mcp"],
        ])
        self.assertEqual(self.claude_servers()["linear"], {"type": "http", "url": "https://mcp.linear.app/mcp"})
        self.assertEqual(self.codex_servers()["linear"]["url"], "https://mcp.linear.app/mcp")
        saved = json.loads(connectors.store_path(self.root).read_text())["connectors"]["linear"]
        self.assertEqual(sorted(saved["backends"]), ["claude", "codex"])
        self.assertRegex(saved["backends"]["claude"]["fingerprint"], "^[0-9a-f]{64}$")
        self.assertNotIn("pending", saved)

    def test_add_refuses_a_name_another_tool_already_uses(self):
        self.edit_claude(lambda servers: servers.update(linear={"type": "http", "url": "https://mine.example"}))
        with self.assertRaisesRegex(connectors.ConnectorError, "claude already has .*'linear'.*another name"):
            connectors.cli(["add", "linear", "--url", "https://mcp.linear.app/mcp"], root=self.root)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.run_cli("list")["connectors"], [])
        self.assertEqual(self.claude_servers()["linear"]["url"], "https://mine.example")

    def test_entries_changed_or_removed_outside_tag_are_reported_and_left_alone(self):
        self.edit_claude(lambda servers: servers.update(other={"type": "http", "url": "https://other.example"}))
        self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")
        self.edit_claude(lambda servers: servers["linear"].update(url="https://edited.example/mcp"))
        self.edit_codex(lambda servers: servers.pop("linear"))
        shown = self.run_cli("show", "linear")["connector"]["backends"]
        self.assertEqual((shown["claude"]["state"], shown["codex"]["state"]), ("changed_outside_tag", "missing"))
        self.assertEqual(self.run_cli("list")["connectors"][0]["backends"], shown)
        # sync doesn't overwrite or re-add them.
        self.run_cli("sync")
        before = len(self.writes())
        removed = self.run_cli("remove", "linear")["connector"]["backends"]
        self.assertEqual((removed["claude"]["state"], removed["codex"]["state"]),
                         ("changed_outside_tag", "missing"))
        self.assertEqual(len(self.writes()), before)
        self.assertEqual(self.claude_servers(), {"other": {"type": "http", "url": "https://other.example"},
                                                 "linear": {"type": "http", "url": "https://edited.example/mcp"}})

    def test_remove_removes_only_what_tag_owns(self):
        self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")
        self.edit_claude(lambda servers: servers.update(other={"type": "http", "url": "https://other.example"}))
        removed = self.run_cli("remove", "linear")
        self.assertEqual(removed["connector"]["backends"], {"claude": {"state": "removed"},
                                                            "codex": {"state": "removed"}})
        self.assertEqual(self.writes()[-2:], [["claude", "mcp", "remove", "--scope", "user", "linear"],
                                              ["codex", "mcp", "remove", "linear"]])
        self.assertEqual(list(self.claude_servers()), ["other"])
        self.assertEqual(self.codex_servers(), {})

    def test_missing_cli_is_pending_and_sync_writes_it_later(self):
        self.uninstall("codex")
        added = self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")["connector"]["backends"]
        self.assertEqual(added["claude"], {"state": "written"})
        self.assertEqual(added["codex"]["state"], "pending")
        self.assertIn("isn't installed", added["codex"]["detail"])
        self.install("codex", FAKE_CODEX)
        synced = self.run_cli("sync")
        self.assertEqual(synced["ok"], True)
        self.assertEqual(synced["connectors"][0]["backends"], {"claude": {"state": "written"},
                                                               "codex": {"state": "written"}})
        self.assertIn("linear", self.codex_servers())
        # A second sync changes nothing.
        before = len(self.writes())
        self.run_cli("sync")
        self.assertEqual(len(self.writes()), before)

    def test_a_failing_cli_leaves_the_backend_pending_with_its_reason(self):
        with patch.dict(os.environ, {"FAKE_FAIL": "codex"}):
            added = self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")["connector"]["backends"]
        self.assertEqual(added["codex"], {"state": "pending", "detail": "Error: something went wrong"})
        self.assertEqual(self.run_cli("show", "linear")["connector"]["backends"]["codex"]["state"], "pending")
        self.assertEqual(self.run_cli("sync")["connectors"][0]["backends"]["codex"], {"state": "written"})
        saved = json.loads(connectors.store_path(self.root).read_text())["connectors"]["linear"]
        self.assertNotIn("pending", saved)

    def test_pending_name_taken_meanwhile_is_reported_not_overwritten(self):
        self.uninstall("codex")
        self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")
        self.install("codex", FAKE_CODEX)
        self.edit_codex(lambda servers: servers.update(linear={"type": "streamable_http", "url": "https://x"}))
        state = self.run_cli("sync")["connectors"][0]["backends"]["codex"]
        self.assertEqual(state["state"], "name_in_use")
        self.assertEqual(self.codex_servers()["linear"]["url"], "https://x")

    def test_stdio_env_is_written_as_names_only(self):
        secret = "s3cret-value-never-written"
        with patch.dict(os.environ, {"NOTES_KEY": secret}):
            row = self.run_cli("add", "notes", "--command", "npx -y notes-mcp", "--env", "NOTES_KEY")["connector"]
        self.assertEqual(self.claude_servers()["notes"], {"type": "stdio", "command": "npx",
                                                          "args": ["-y", "notes-mcp"],
                                                          "env": {"NOTES_KEY": "${NOTES_KEY}"}})
        # Codex's CLI can only set values, so the names reach Codex per run as env_vars.
        self.assertEqual(row["backends"]["codex"]["state"], "written")
        self.assertIn(["codex", "mcp", "add", "notes", "--", "npx", "-y", "notes-mcp"], self.writes())
        files = [self.log, self.claude_dir / ".claude.json", connectors.store_path(self.root)]
        for path in files:
            self.assertNotIn(secret, path.read_text())

    def test_stdio_without_env_is_written_to_codex(self):
        self.run_cli("add", "files", "--command", "files-mcp --root /srv")
        self.assertIn(["codex", "mcp", "add", "files", "--", "files-mcp", "--root", "/srv"], self.writes())
        self.assertEqual(self.codex_servers()["files"]["args"], ["--root", "/srv"])

    def test_unreadable_claude_config_reads_unknown(self):
        self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")
        (self.claude_dir / ".claude.json").write_text("{")
        self.assertEqual(self.run_cli("show", "linear")["connector"]["backends"]["claude"]["state"], "unknown")

    def test_unreadable_config_is_never_written(self):
        (self.claude_dir / ".claude.json").write_text("{")
        added = self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")["connector"]["backends"]
        self.assertEqual(added["claude"]["state"], "pending")
        self.assertNotIn("claude", [call[0] for call in self.writes()])

    def test_owned_entry_left_behind_when_its_cli_is_gone(self):
        self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")
        self.uninstall("claude")
        removed = self.run_cli("remove", "linear")
        self.assertEqual(removed["connector"]["backends"]["claude"]["state"], "left_behind")
        self.assertIn("claude mcp remove --scope user linear", removed["message"])
        self.assertIn("linear", self.claude_servers())

    def test_fingerprint_ignores_status_and_empty_fields(self):
        base = {"name": "x", "transport": {"type": "stdio", "command": "a", "args": ["b"]}}
        noisy = {**base, "auth_status": "o_auth", "disabled_reason": None,
                 "transport": {**base["transport"], "env": None, "env_vars": [], "cwd": None}}
        self.assertEqual(backends.fingerprint(base), backends.fingerprint(noisy))
        self.assertNotEqual(backends.fingerprint(base),
                            backends.fingerprint({**base, "transport": {**base["transport"], "args": []}}))

    def test_codex_sign_in_flow_after_add_is_stopped(self):
        with patch.dict(os.environ, {"FAKE_OAUTH": "1"}), patch.object(backends, "TIMEOUT_SECONDS", 20):
            started = time.monotonic()
            added = self.run_cli("add", "linear", "--url", "https://mcp.linear.app/mcp")["connector"]
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(added["backends"]["codex"], {"state": "written"})

    def test_json_error_from_tag_cli(self):
        self.edit_claude(lambda servers: servers.update(linear={"type": "http", "url": "https://mine.example"}))
        script = Path(__file__).resolve().parents[1] / "scripts/tag_cli.py"
        result = subprocess.run([sys.executable, str(script), "connector", "add", "linear", "--url",
                                 "https://mcp.linear.app/mcp", "--json"], capture_output=True, text=True,
                                timeout=60, stdin=subprocess.DEVNULL, check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["ok"], False)
        self.assertIn("another name", output["error"])


@unittest.skipUnless(shutil.which("codex"), "codex isn't installed")
class RealCodexTests(FakeBackendsCase):
    """The Codex adapter against the real CLI, in a temp CODEX_HOME."""

    def setUp(self):
        real = shutil.which("codex")
        super().setUp()
        self.uninstall("codex")
        (self.bin / "codex").symlink_to(real)
        # The codex launcher needs node next to it; that directory has no claude.
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{Path(real).resolve().parent}{os.pathsep}" \
                             f"{Path(real).parent}"
        result = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            self.skipTest("codex doesn't run here")

    def test_add_change_and_remove(self):
        added = self.run_cli("add", "linear", "--url", "https://mcp.example.invalid/mcp")["connector"]["backends"]
        self.assertEqual(added["codex"], {"state": "written"})
        self.assertIn('[mcp_servers.linear]', (self.codex_home / "config.toml").read_text())
        subprocess.run(["codex", "mcp", "add", "linear", "--url", "https://edited.example/mcp"],
                       capture_output=True, timeout=30, check=True)
        self.assertEqual(self.run_cli("show", "linear")["connector"]["backends"]["codex"]["state"],
                         "changed_outside_tag")
        self.run_cli("remove", "linear")
        self.assertIn("edited.example", (self.codex_home / "config.toml").read_text())


if __name__ == "__main__":
    unittest.main()
