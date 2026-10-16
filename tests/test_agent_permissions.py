"""Each backend's own permission settings for a Tag."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import agent_permissions, tag_config


class PermissionsTests(unittest.TestCase):
    def test_unset_keeps_todays_behavior_and_unknown_values_fail_closed(self) -> None:
        self.assertEqual("auto", agent_permissions.claude_mode({}))
        self.assertEqual({"sandbox": "workspace-write", "approvalsReviewer": "auto_review"}, agent_permissions.codex_thread({}))
        with self.assertRaisesRegex(ValueError, "OPENTAG_CODEX_SANDBOX"):
            agent_permissions.codex_thread({"OPENTAG_CODEX_SANDBOX": "none"})
        with self.assertRaisesRegex(ValueError, "OPENTAG_CLAUDE_PERMISSION_MODE"):
            agent_permissions.claude_mode({"OPENTAG_CLAUDE_PERMISSION_MODE": "plan"})

    def test_codex_and_claude_settings_are_independent(self) -> None:
        values = {"OPENTAG_CODEX_SANDBOX": "read-only", "OPENTAG_CLAUDE_PERMISSION_MODE": "bypassPermissions"}
        self.assertEqual("read-only", agent_permissions.codex_thread(values)["sandbox"])
        self.assertEqual("bypassPermissions", agent_permissions.claude_mode(values))
        result = agent_permissions.report(values)
        self.assertEqual(("read-only", "bypassPermissions"), (result["codex"]["sandbox"], result["claude"]["mode"]))
        self.assertEqual(["sandbox", "approvals", "reviewer"], list(result["options"]["codex"]))

    def test_a_codex_that_never_asks_has_no_reviewer(self) -> None:
        params = agent_permissions.codex_thread({"OPENTAG_CODEX_APPROVALS": "never", "OPENTAG_CODEX_REVIEWER": "auto_review"})
        self.assertEqual(("never", "user"), (params["approvalPolicy"], params["approvalsReviewer"]))

    def test_changes_validate_each_assignment(self) -> None:
        self.assertEqual({"OPENTAG_CODEX_SANDBOX": "read-only", "OPENTAG_CLAUDE_PERMISSION_MODE": "default"},
                         agent_permissions.changes(["codex-sandbox=read-only", "claude-mode=default"]))
        for bad in ([], ["codex-sandbox="], ["mode=auto"], ["codex-reviewer=me"]):
            with self.assertRaises(ValueError):
                agent_permissions.changes(bad)

    def test_rollback_transports_refuse_settings_they_cannot_enforce(self) -> None:
        agent_permissions.check_legacy_transport("codex", {})
        agent_permissions.check_legacy_transport("claude", {"OPENTAG_CLAUDE_PERMISSION_MODE": "bypassPermissions"})
        # claude -p always skips permission checks, so even the default Auto must be chosen as Bypass.
        for mode in ({}, {"OPENTAG_CLAUDE_PERMISSION_MODE": "auto"}):
            with self.assertRaisesRegex(ValueError, "Bypass"):
                agent_permissions.check_legacy_transport("claude", mode)
        with self.assertRaisesRegex(ValueError, "app-server"):
            agent_permissions.check_legacy_transport("codex", {"OPENTAG_CODEX_SANDBOX": "read-only"})
        with self.assertRaisesRegex(ValueError, "OPENTAG_CLAUDE_TRANSPORT=sdk"):
            agent_permissions.check_legacy_transport("claude", {"OPENTAG_CLAUDE_PERMISSION_MODE": "default"})

    def test_settings_validate_each_backend(self) -> None:
        self.assertIsNone(tag_config.validation_error("OPENTAG_CODEX_SANDBOX", "danger-full-access"))
        self.assertIsNone(tag_config.validation_error("OPENTAG_CODEX_APPROVALS", ""))
        self.assertIn("Choose", tag_config.validation_error("OPENTAG_CODEX_REVIEWER", "boss"))
        self.assertIn("Choose", tag_config.validation_error("OPENTAG_CLAUDE_PERMISSION_MODE", "plan"))



class TagRuleTests(unittest.TestCase):
    """Each Tag's own rules sit on top of the global ones the operator keeps."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        patcher = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.folder / "codex"),
                                               "CLAUDE_CONFIG_DIR": str(self.folder / "claude"),
                                               "TAG_HOME": str(self.folder / "install")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tag = self.folder / "tag"

    def write(self, path: Path, data: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def test_none_yet(self) -> None:
        result = agent_permissions.report({}, self.tag)
        self.assertEqual({"tag": [], "folder": [], "all_tags": [], "personal": []}, result["codex"]["rules"])
        self.assertEqual({"tag": [], "folder": [], "all_tags": [], "personal": []}, result["claude"]["rules"])
        self.assertEqual({"all_tags": [], "personal": []}, agent_permissions.rules()["claude"])
        self.assertNotIn("rules", agent_permissions.report({})["claude"])

    def test_each_agent_lists_the_tags_rules_and_the_global_ones_it_inherits(self) -> None:
        self.write(agent_permissions.claude_tag_file(self.tag), {"permissions": {
            "allow": ["Bash(git status:*)", 7], "deny": ["Bash(git push:*)"], "additionalDirectories": ["/srv/data"]}})
        self.write(agent_permissions.claude_personal_file(), {"permissions": {"ask": ["WebFetch"]}})
        self.write(agent_permissions.codex_personal_dir() / "default.rules",
                   'prefix_rule(pattern=["npm", "run", "build"], decision="allow")\n'
                   'network_rule(host="ads.example.com", protocol="https", decision="deny")\n# a note\n')
        self.write(agent_permissions.codex_personal_dir() / "team.rules",
                   'prefix_rule(\n    pattern = ["git", ["status", "diff"]],\n    decision = "prompt",\n)\n')
        agent_permissions.add_rule("codex", self.tag, "block", "rm -rf")
        rules = agent_permissions.rules(self.tag)
        self.assertEqual([{"kind": "rule", "value": "Bash(git status:*)", "decision": "allow"},
                          {"kind": "rule", "value": "Bash(git push:*)", "decision": "block"},
                          {"kind": "folder", "value": "/srv/data", "decision": "allow"}], rules["claude"]["tag"])
        self.assertEqual([{"kind": "rule", "value": "WebFetch", "decision": "ask"}], rules["claude"]["personal"])
        self.assertEqual([{"kind": "command", "value": "rm -rf", "decision": "block"}], rules["codex"]["tag"])
        self.assertEqual([{"kind": "command", "value": "npm run build", "decision": "allow"},
                          {"kind": "host", "value": "ads.example.com", "decision": "block"},
                          {"kind": "command", "value": "git {status,diff}", "decision": "ask"}], rules["codex"]["personal"])

    def test_adding_a_rule_again_changes_it_and_removing_keeps_everything_else(self) -> None:
        path = self.write(agent_permissions.claude_tag_file(self.tag),
                          {"permissions": {"allow": ["WebFetch"]}, "env": {"A": "1"}})
        path.chmod(0o600)
        agent_permissions.add_rule("claude", self.tag, "block", "Bash(git push:*)")
        agent_permissions.add_rule("claude", self.tag, "allow", "Bash(git push:*)")
        self.assertEqual({"permissions": {"allow": ["WebFetch", "Bash(git push:*)"], "deny": []}, "env": {"A": "1"}},
                         json.loads(path.read_text(encoding="utf-8")))
        agent_permissions.remove_rule("claude", self.tag, "WebFetch")
        self.assertEqual(["Bash(git push:*)"], json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"])
        if os.name != "nt":  # Windows has no POSIX file modes.
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
        agent_permissions.add_rule("codex", self.tag, "allow", "git push")
        agent_permissions.add_rule("codex", self.tag, "block", "git push")
        agent_permissions.save_codex_host(self.tag, "api.example.com", "https", "allow")
        self.assertEqual('prefix_rule(pattern=["git", "push"], decision="forbidden")\n'
                         'network_rule(host="api.example.com", protocol="https", decision="allow")\n',
                         agent_permissions.codex_tag_file(self.tag).read_text(encoding="utf-8"))
        agent_permissions.remove_rule("codex", self.tag, "api.example.com")
        self.assertEqual([{"kind": "command", "value": "git push", "decision": "block"}],
                         agent_permissions.rules(self.tag)["codex"]["tag"])
        for backend, missing in (("claude", "Bash(rm:*)"), ("codex", "rm")):
            with self.assertRaisesRegex(ValueError, "has no"):
                agent_permissions.remove_rule(backend, self.tag, missing)

    def test_saving_a_host_rule_keeps_the_same_host_on_other_protocols(self) -> None:
        agent_permissions.save_codex_host(self.tag, "api.example.com", "https", "block")
        agent_permissions.save_codex_host(self.tag, "api.example.com", "http", "allow")
        text = agent_permissions.codex_tag_file(self.tag).read_text(encoding="utf-8")
        self.assertIn('protocol="https", decision="deny"', text)
        self.assertIn('protocol="http", decision="allow"', text)
        agent_permissions.save_codex_host(self.tag, "api.example.com", "https", "allow")
        text = agent_permissions.codex_tag_file(self.tag).read_text(encoding="utf-8")
        self.assertNotIn("deny", text)
        self.assertEqual(2, text.count("network_rule"))

    def test_a_line_with_other_rules_is_never_rewritten(self) -> None:
        path = self.write(agent_permissions.codex_tag_file(self.tag),
                          'prefix_rule(pattern=["rm"], decision="forbidden"); prefix_rule(pattern=["git", "push"])\n')
        for change in (lambda: agent_permissions.add_rule("codex", self.tag, "block", "git push"),
                       lambda: agent_permissions.remove_rule("codex", self.tag, "git push")):
            with self.assertRaisesRegex(ValueError, "by hand"):
                change()
        self.assertIn('"rm"', path.read_text(encoding="utf-8"))

    def test_codex_rules_tag_cant_read_count_as_asking(self) -> None:
        self.write(agent_permissions.codex_tag_file(self.tag), 'prefix_rule(["rm"], "forbidden")\n')
        self.assertEqual("ask", agent_permissions.rules(self.tag)["codex"]["tag"][0]["decision"])
        self.assertTrue(agent_permissions.codex_tag_restricts(self.tag))
        for odd in ('prefix_rule(pattern={[1]: 2})\n', 'prefix_rule(pattern=[\n'):
            self.write(agent_permissions.codex_tag_file(self.tag), odd)
            self.assertTrue(agent_permissions.codex_tag_restricts(self.tag))  # Never a crash, never "allowed".
        # Other rule files in the Tag's folder are listed, and count too.
        self.write(agent_permissions.codex_tag_file(self.tag), "")
        self.write(self.tag / ".codex/rules/team.rules", 'prefix_rule(pattern=["curl"], decision="forbidden")\n')
        self.assertEqual(["curl"], [r["value"] for r in agent_permissions.rules(self.tag)["codex"]["folder"]])
        self.assertTrue(agent_permissions.codex_tag_restricts(self.tag))

    def test_codex_rules_under_another_name_or_with_odd_values_count_as_asking(self) -> None:
        for odd in ('block = prefix_rule\nblock(pattern=["rm"], decision="forbidden")\n',
                    'prefix_rule(pattern=["rm"], decision=["forbidden"])\n',
                    'prefix_rule(pattern=["rm"], decision={"a": 1})\n',
                    'prefix_rule(pattern=["rm"], **{"decision": "forbidden"})\n'):
            self.write(agent_permissions.codex_tag_file(self.tag), odd)
            self.assertIn("ask", [r["decision"] for r in agent_permissions.rules(self.tag)["codex"]["tag"]])
            self.assertTrue(agent_permissions.codex_tag_restricts(self.tag))
            self.assertTrue(agent_permissions.codex_holds(self.tag, words=["ls"]))

    def test_codex_holds_only_what_asks_or_blocks(self) -> None:
        self.write(agent_permissions.codex_personal_dir() / "team.rules",
                   'prefix_rule(pattern=["git", ["push", "reset"]], decision="prompt")\n'
                   'prefix_rule(pattern=["npm"], decision="allow")\n'
                   'network_rule(host="ads.example.com", protocol="https", decision="deny")\n')
        self.assertTrue(agent_permissions.codex_holds(self.tag, words=["git", "reset", "--hard"]))
        self.assertFalse(agent_permissions.codex_holds(self.tag, words=["git", "status"]))
        self.assertFalse(agent_permissions.codex_holds(self.tag, words=["git"]))
        self.assertFalse(agent_permissions.codex_holds(self.tag, words=["npm", "publish"]))
        self.assertTrue(agent_permissions.codex_holds(self.tag, host="ADS.example.com"))
        self.assertFalse(agent_permissions.codex_holds(self.tag, host="api.example.com"))

    def test_a_claude_rule_is_one_rule(self) -> None:
        for bad in ("Bash(git status) Bash(rm -rf ~)", "Bash(a) , WebFetch(b)", "Bash()"):
            with self.assertRaisesRegex(ValueError, "Write a Claude rule"):
                agent_permissions.add_rule("claude", self.tag, "allow", bad)
        agent_permissions.add_rule("claude", self.tag, "allow", "Bash(npm run test:*)")

    def test_claude_settings_tag_cant_read_stop_the_run(self) -> None:
        agent_permissions.check_claude_folder(self.tag)  # None yet is fine.
        for path in (agent_permissions.claude_tag_file(self.tag), self.tag / ".claude/settings.json",
                     agent_permissions.claude_personal_file()):
            self.write(path, "{ not json")
            with self.assertRaisesRegex(ValueError, "Couldn't read"):
                agent_permissions.check_claude_folder(self.tag)
            path.unlink()

    def test_a_rules_file_being_changed_waits_and_then_refuses(self) -> None:
        path = agent_permissions.codex_tag_file(self.tag)
        with agent_permissions._locked(path), mock.patch.object(agent_permissions.time, "monotonic", side_effect=[0, 10]):
            with self.assertRaisesRegex(ValueError, "in progress"):
                agent_permissions.add_rule("codex", self.tag, "block", "rm")
        agent_permissions.add_rule("codex", self.tag, "block", "rm")  # The lock left behind lets the next change in.

    def test_a_bad_saved_value_is_shown_not_crashed_on(self) -> None:
        result = agent_permissions.report({"OPENTAG_CLAUDE_PERMISSION_MODE": "plan"}, self.tag)
        self.assertEqual("plan", result["claude"]["mode"])
        self.assertIn("OPENTAG_CLAUDE_PERMISSION_MODE must be one of", result["errors"]["claude-mode"])
        with self.assertRaises(ValueError):
            agent_permissions.claude_mode({"OPENTAG_CLAUDE_PERMISSION_MODE": "plan"})  # Runs still refuse it.

    def test_codex_approvals_can_follow_your_codex_config_again(self) -> None:
        self.assertEqual({"OPENTAG_CODEX_APPROVALS": ""}, agent_permissions.changes(["codex-approvals="]))
        with self.assertRaises(ValueError):
            agent_permissions.changes(["codex-sandbox="])
        self.assertNotIn("approvalPolicy", agent_permissions.codex_thread({"OPENTAG_CODEX_APPROVALS": ""}))
        self.assertEqual("on-request", agent_permissions.codex_thread({"OPENTAG_CODEX_APPROVALS": "on-request"})["approvalPolicy"])

    def test_rules_tag_cannot_read_or_write_are_refused(self) -> None:
        for backend, bad in (("claude", "git push"), ("claude", "Bash(x)\nRead"), ("claude", ""), ("claude", "bash(rm:*)"),
                             ("codex", "-rf"), ("codex", "'unclosed"), ("codex", ""), ("mystery", "x")):
            with self.subTest(backend=backend, rule=bad), self.assertRaises(ValueError):
                agent_permissions.add_rule(backend, self.tag, "allow", bad)
        with self.assertRaises(ValueError):
            agent_permissions.add_rule("claude", self.tag, "ask", "WebFetch")
        path = self.write(agent_permissions.claude_tag_file(self.tag), "{not json")
        self.assertIsNone(agent_permissions.rules(self.tag)["claude"]["tag"])
        with self.assertRaisesRegex(ValueError, "Couldn't read"):
            agent_permissions.add_rule("claude", self.tag, "block", "WebFetch")
        self.assertEqual("{not json", path.read_text(encoding="utf-8"))

    def test_rules_for_all_tags_reach_both_agents_and_never_touch_your_own(self) -> None:
        agent_permissions.add_rule("claude", None, "block", "Bash(git push:*)")
        agent_permissions.add_rule("codex", None, "block", "git push")
        agent_permissions.add_rule("codex", None, "allow", "npm test")
        found = agent_permissions.rules(self.tag)
        self.assertEqual(([], ["git push", "npm test"]), (found["codex"]["tag"], [r["value"] for r in found["codex"]["all_tags"]]))
        self.assertFalse(agent_permissions.claude_personal_file().exists())
        self.assertFalse(agent_permissions.codex_personal_dir().exists())
        # Claude gets them as extra settings; Codex as a copy in the Tag's folder.
        self.assertEqual({"permissions": {"deny": ["Bash(git push:*)"]}}, agent_permissions.claude_all_tags_settings())
        self.assertFalse(agent_permissions.codex_tag_restricts(self.tag))
        agent_permissions.sync_codex_all_tags(self.tag)
        copy = self.tag / ".codex/rules" / agent_permissions.CODEX_ALL_TAGS_COPY
        self.assertEqual(agent_permissions.codex_all_tags_file().read_text(encoding="utf-8"), copy.read_text(encoding="utf-8"))
        self.assertTrue(agent_permissions.codex_tag_restricts(self.tag))
        self.assertEqual([], agent_permissions.rules(self.tag)["codex"]["tag"])  # The copy isn't the Tag's own.
        agent_permissions.remove_rule("codex", None, "git push")
        agent_permissions.remove_rule("codex", None, "npm test")
        agent_permissions.sync_codex_all_tags(self.tag)
        self.assertFalse(copy.exists())
        with self.assertRaisesRegex(ValueError, "All Tags has no Codex rule"):
            agent_permissions.remove_rule("codex", None, "git push")
        self.write(agent_permissions.claude_all_tags_file(), "{broken")
        with self.assertRaisesRegex(ValueError, "Couldn't read"):
            agent_permissions.claude_all_tags_settings()  # Claude doesn't run without its blocks.

    def test_only_blocks_and_asks_need_codex_to_read_the_tags_rules(self) -> None:
        self.assertFalse(agent_permissions.codex_tag_restricts(self.tag))
        agent_permissions.add_rule("codex", self.tag, "allow", "npm test")
        self.assertFalse(agent_permissions.codex_tag_restricts(self.tag))
        agent_permissions.add_rule("codex", self.tag, "block", "git push")
        self.assertTrue(agent_permissions.codex_tag_restricts(self.tag))
        agent_permissions.check_legacy_transport("codex", {})
        with self.assertRaisesRegex(ValueError, "app-server"):
            agent_permissions.check_legacy_transport("codex", {}, self.tag)


if __name__ == "__main__":
    unittest.main()
