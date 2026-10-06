from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import slack_manifest_migrations as migrations


class SlackManifestMigrationTests(unittest.TestCase):
    def manifest(self) -> dict:
        return {
            "display_information": {"name": "Custom name"},
            "features": {"app_home": {"home_tab_enabled": True}},
            "oauth_config": {"scopes": {"bot": ["chat:write", "users:read"]}},
            "settings": {"event_subscriptions": {"bot_events": ["app_mention"]}},
            "custom_operator_setting": {"kept": True},
        }

    def values(self) -> dict[str, str]:
        return {
            "SLACK_TEAM_ID": "TTEST",
            "SLACK_APP_ID": "ATEST",
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SLACK_APP_TOKEN": "xapp-test-token",
        }

    def linked_home(self, root: Path) -> tuple[Path, Path]:
        home = root / "home"
        project = home / "integrations/slack-cli"
        (project / ".slack").mkdir(parents=True)
        migrations.settings.save_config(project / ".slack/apps.dev.json", {
            "TTEST": {"team_id": "TTEST", "app_id": "ATEST"}
        })
        config = home / "config/settings.json"
        migrations.settings.save_config(config, self.values())
        return home, config

    def test_manifest_migration_is_additive_and_idempotent(self):
        original = self.manifest()
        migrated, changed = migrations.migrate_manifest(original)
        self.assertTrue(changed)
        self.assertEqual(migrated["display_information"], {"name": "Custom name"})
        self.assertEqual(migrated["custom_operator_setting"], {"kept": True})
        self.assertEqual(set(migrated["oauth_config"]["scopes"]["bot"]), set(migrations.REQUIRED_BOT_SCOPES))
        self.assertEqual(set(migrated["settings"]["event_subscriptions"]["bot_events"]),
                         set(migrations.REQUIRED_MANIFEST["settings"]["event_subscriptions"]["bot_events"]))
        self.assertFalse(migrations.migrate_manifest(migrated)[1])

    def test_search_migration_updates_an_existing_dm_manifest(self):
        original = migrations.migrate_manifest(self.manifest())[0]
        original["oauth_config"]["scopes"]["bot"].remove("users:read")
        migrated, changed = migrations.migrate_manifest(original)
        self.assertTrue(changed)
        self.assertIn("users:read", migrated["oauth_config"]["scopes"]["bot"])
        self.assertNotIn("users:read", original["oauth_config"]["scopes"]["bot"])
        self.assertEqual(original["features"], migrated["features"])

    def test_migration_covers_current_manifest_and_preserves_custom_settings(self):
        from scripts.check_manifest import validate_manifest
        import yaml
        original = self.manifest()
        original["oauth_config"]["scopes"]["bot"].append("reactions:read")
        original["settings"]["event_subscriptions"]["bot_events"].append("reaction_added")
        original["settings"]["interactivity"] = {"request_url": "https://example.test/actions"}
        current, _ = migrations.migrate_manifest(original)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "slack-app-manifest.yaml").write_text(yaml.safe_dump(current), encoding="utf-8")
            self.assertEqual([], validate_manifest(root))
        self.assertIn("reactions:read", current["oauth_config"]["scopes"]["bot"])
        self.assertIn("reaction_added", current["settings"]["event_subscriptions"]["bot_events"])
        self.assertEqual("https://example.test/actions", current["settings"]["interactivity"]["request_url"])

    def test_legacy_assistant_conversion_requires_explicit_approval(self):
        original = self.manifest()
        original["features"]["assistant_view"] = {"assistant_description": "Custom assistant"}
        with self.assertRaisesRegex(RuntimeError, "irreversible"):
            migrations.migrate_manifest(original)
        self.assertNotIn("agent_view", original["features"])

    def test_sync_success_without_remote_changes_does_not_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            home, config = self.linked_home(Path(directory))
            with patch.object(migrations.shutil, "which", return_value="slack"), patch.object(
                migrations, "remote_manifest", return_value=self.manifest()
            ), patch.object(migrations, "granted_bot_scopes", return_value=set()), patch.object(
                migrations, "_sync_command", return_value=["slack", "manifest", "sync"]
            ), patch.object(migrations, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")), patch.object(
                migrations.slack_credentials, "receive"
            ) as receive:
                with self.assertRaisesRegex(RuntimeError, "did not save"):
                    migrations.reconcile(home, config, self.values())
            receive.assert_not_called()
            self.assertFalse((home / "state/slack-manifest-migrations.json").exists())

    def test_agent_view_migration_preserves_manifest_and_is_idempotent(self):
        original = self.manifest()
        migrated, changed, replaces_legacy = migrations.migrate_agent_view(original)
        self.assertTrue(changed)
        self.assertFalse(replaces_legacy)
        self.assertEqual(
            migrated["features"]["agent_view"]["agent_description"],
            migrations.AGENT_DESCRIPTION,
        )
        self.assertEqual(migrated["custom_operator_setting"], {"kept": True})
        self.assertNotIn("agent_view", original["features"])
        self.assertEqual(migrations.migrate_agent_view(migrated)[1:], (False, False))

    def test_agent_view_migration_maps_legacy_presentation(self):
        original = self.manifest()
        original["features"]["assistant_view"] = {
            "assistant_description": "A custom helper",
            "actions": [{"id": "compose"}],
            "suggested_prompts": [{"title": "Catch me up"}],
        }
        migrated, changed, replaces_legacy = migrations.migrate_agent_view(original)
        self.assertTrue(changed)
        self.assertTrue(replaces_legacy)
        self.assertNotIn("assistant_view", migrated["features"])
        self.assertEqual(migrated["features"]["agent_view"], {
            "agent_description": "A custom helper",
            "actions": [{"id": "compose"}],
            "suggested_prompts": [{"title": "Catch me up"}],
        })

    def test_enable_agent_view_requires_approval_before_replacing_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            home, _ = self.linked_home(Path(directory))
            project = home / "integrations/slack-cli"
            remote = self.manifest()
            remote["features"]["assistant_view"] = {}
            approve = Mock(return_value=False)
            with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                migrations, "remote_manifest", return_value=remote
            ), patch.object(migrations, "_run") as run:
                self.assertFalse(migrations.enable_agent_view(
                    project, "ATEST", "TTEST", approve_legacy=approve
                ))
            approve.assert_called_once_with()
            run.assert_not_called()

    def test_enable_agent_view_syncs_and_verifies_remote_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home, _ = self.linked_home(Path(directory))
            project = home / "integrations/slack-cli"
            verified = migrations.migrate_agent_view(self.manifest())[0]
            success = subprocess.CompletedProcess([], 0, "", "")
            approve = Mock(return_value=True)
            with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                migrations, "remote_manifest", side_effect=[self.manifest(), verified]
            ) as remote, patch.object(
                migrations, "_sync_command", return_value=["/bin/slack", "manifest", "sync"]
            ), patch.object(migrations, "_run", return_value=success) as run:
                self.assertTrue(migrations.enable_agent_view(
                    project, "ATEST", "TTEST", approve_legacy=approve
                ))
            approve.assert_not_called()
            self.assertEqual(remote.call_count, 2)
            self.assertEqual(run.call_args.args[0], ["/bin/slack", "manifest", "sync"])

    def test_current_manifest_and_grant_only_write_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, config = self.linked_home(root)
            current = migrations.migrate_manifest(self.manifest())[0]
            with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                migrations, "remote_manifest", return_value=current
            ), patch.object(migrations, "granted_bot_scopes", return_value=set(migrations.REQUIRED_BOT_SCOPES)), patch.object(
                migrations, "_run"
            ) as run:
                self.assertFalse(migrations.reconcile(home, config, self.values()))
            run.assert_not_called()
            marker = json.loads((home / "state/slack-manifest-migrations.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["version"], migrations.MIGRATION_VERSION)

    def test_background_start_syncs_refreshes_and_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, config = self.linked_home(root)
            # An installation that completed the previous release's migration
            # must still acquire the newly required search permission.
            marker = home / "state/slack-manifest-migrations.json"
            migrations.settings.save_config(marker, {
                "version": 1, "team_id": "TTEST", "app_id": "ATEST",
            })
            remote = migrations.migrate_manifest(self.manifest())[0]
            remote["oauth_config"]["scopes"]["bot"].remove("users:read")
            success = subprocess.CompletedProcess([], 0, "", "")
            fresh = {"SLACK_APP_TOKEN": "xapp-fresh-token", "SLACK_BOT_TOKEN": "xoxb-fresh-token"}
            with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                migrations, "remote_manifest", side_effect=[remote, migrations.migrate_manifest(remote)[0]]
            ), patch.object(migrations, "granted_bot_scopes", side_effect=[set(), set(migrations.REQUIRED_BOT_SCOPES)]), patch.object(
                migrations, "_sync_command", return_value=["/bin/slack", "manifest", "sync"]
            ), patch.object(migrations, "_run", return_value=success) as run, patch.object(
                migrations.slack_credentials, "receive", return_value=fresh
            ) as receive:
                self.assertTrue(migrations.reconcile(home, config, self.values()))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args_list[0].args[0], ["/bin/slack", "manifest", "sync"])
            receive.assert_called_once()
            saved = migrations.settings.read_config(config)
            self.assertEqual(saved["SLACK_BOT_TOKEN"], "xoxb-fresh-token")
            self.assertTrue((home / "state/slack-manifest-migrations.json").is_file())
            self.assertEqual(migrations.MIGRATION_VERSION, json.loads(marker.read_text(encoding="utf-8"))["version"])
            with patch.object(migrations, "remote_manifest") as inspect:
                self.assertFalse(migrations.reconcile(home, config, self.values() | fresh))
                inspect.assert_not_called()

    def test_current_manifest_with_stale_grant_refreshes_without_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            home, config = self.linked_home(Path(directory))
            current = migrations.migrate_manifest(self.manifest())[0]
            fresh = {"SLACK_APP_TOKEN": "xapp-fresh", "SLACK_BOT_TOKEN": "xoxb-fresh"}
            with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                migrations, "remote_manifest", return_value=current
            ), patch.object(migrations, "granted_bot_scopes", side_effect=[
                {"im:history"}, set(migrations.REQUIRED_BOT_SCOPES),
            ]), patch.object(migrations, "_run") as run, patch.object(
                migrations.slack_credentials, "receive", return_value=fresh
            ) as receive:
                self.assertTrue(migrations.reconcile(home, config, self.values()))
            run.assert_not_called()
            receive.assert_called_once()
            self.assertEqual("xoxb-fresh", migrations.settings.read_config(config)["SLACK_BOT_TOKEN"])

    def test_denied_authorization_or_missing_grant_does_not_checkpoint(self):
        for denied in (True, False):
            with self.subTest(denied=denied), tempfile.TemporaryDirectory() as directory:
                home, config = self.linked_home(Path(directory))
                current = migrations.migrate_manifest(self.manifest())[0]
                with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                    migrations, "remote_manifest", return_value=current
                ), patch.object(migrations, "granted_bot_scopes", return_value={"im:history"}), patch.object(
                    migrations.slack_credentials, "receive",
                    side_effect=RuntimeError("Slack approval required") if denied else None,
                    return_value={"SLACK_APP_TOKEN": "xapp-fresh", "SLACK_BOT_TOKEN": "xoxb-fresh"},
                ):
                    with self.assertRaisesRegex(RuntimeError, "approval required|users:read"):
                        migrations.reconcile(home, config, self.values())
                self.assertFalse((home / "state/slack-manifest-migrations.json").exists())

    def test_optional_team_read_never_blocks_an_older_installation(self):
        # An installation from the previous release: every required permission, no team:read.
        with tempfile.TemporaryDirectory() as directory:
            home, config = self.linked_home(Path(directory))
            marker = home / "state/slack-manifest-migrations.json"
            migrations.settings.save_config(marker, {"version": 3, "team_id": "TTEST", "app_id": "ATEST"})
            remote = migrations.migrate_manifest(self.manifest())[0]
            remote["oauth_config"]["scopes"]["bot"].remove("team:read")
            needed = set(migrations.NEEDED_BOT_SCOPES)
            with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                migrations, "remote_manifest", side_effect=[remote, migrations.migrate_manifest(remote)[0]]
            ), patch.object(migrations, "granted_bot_scopes", return_value=needed), patch.object(
                migrations, "_sync_command", return_value=["/bin/slack", "manifest", "sync"]
            ), patch.object(migrations, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")), patch.object(
                migrations.slack_credentials, "receive", side_effect=RuntimeError("An admin must approve this app")
            ):
                # The workspace needs admin approval: the start continues and the request is remembered.
                self.assertFalse(migrations.reconcile(home, config, self.values()))
            saved = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], migrations.MIGRATION_VERSION)
            self.assertEqual(saved["optional_pending"], ["team:read"])
            self.assertIn("admin must approve", saved["optional_error"])
            self.assertEqual(migrations.optional_pending(home), ["team:read"])
            self.assertEqual("xoxb-test-token", migrations.settings.read_config(config)["SLACK_BOT_TOKEN"])

            # Repeated starts within a day don't ask Slack again.
            with patch.object(migrations, "remote_manifest") as inspect:
                self.assertFalse(migrations.reconcile(home, config, self.values()))
                inspect.assert_not_called()

            # A day later Tag asks again; once approved, the new token carries team:read.
            later = migrations._now() + migrations.OPTIONAL_RETRY
            fresh = {"SLACK_APP_TOKEN": "xapp-fresh", "SLACK_BOT_TOKEN": "xoxb-fresh"}
            current = migrations.migrate_manifest(remote)[0]
            with patch.object(migrations, "_now", return_value=later), patch.object(
                migrations.shutil, "which", return_value="/bin/slack"
            ), patch.object(migrations, "remote_manifest", return_value=current), patch.object(
                migrations, "granted_bot_scopes", side_effect=[needed, set(migrations.REQUIRED_BOT_SCOPES)]
            ), patch.object(migrations, "_run") as run, patch.object(
                migrations.slack_credentials, "receive", return_value=fresh
            ):
                self.assertTrue(migrations.reconcile(home, config, self.values() | fresh))
            run.assert_not_called()
            self.assertEqual(json.loads(marker.read_text(encoding="utf-8")),
                             {"version": migrations.MIGRATION_VERSION, "team_id": "TTEST", "app_id": "ATEST"})
            self.assertEqual(migrations.optional_pending(home), [])
            self.assertEqual("xoxb-fresh", migrations.settings.read_config(config)["SLACK_BOT_TOKEN"])

    def test_reinstall_without_team_read_saves_credentials_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            home, config = self.linked_home(Path(directory))
            remote = migrations.migrate_manifest(self.manifest())[0]
            remote["oauth_config"]["scopes"]["bot"].remove("users:read")
            fresh = {"SLACK_APP_TOKEN": "xapp-fresh", "SLACK_BOT_TOKEN": "xoxb-fresh"}
            with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                migrations, "remote_manifest", side_effect=[remote, migrations.migrate_manifest(remote)[0]]
            ), patch.object(migrations, "granted_bot_scopes", side_effect=[set(), set(migrations.NEEDED_BOT_SCOPES)]), patch.object(
                migrations, "_sync_command", return_value=["/bin/slack", "manifest", "sync"]
            ), patch.object(migrations, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")), patch.object(
                migrations.slack_credentials, "receive", return_value=fresh
            ):
                self.assertTrue(migrations.reconcile(home, config, self.values()))
            self.assertEqual("xoxb-fresh", migrations.settings.read_config(config)["SLACK_BOT_TOKEN"])
            self.assertEqual(migrations.optional_pending(home), ["team:read"])

    def test_required_permissions_still_block_while_team_read_is_optional(self):
        for pending in (False, True):
            with self.subTest(retry_of_optional=pending), tempfile.TemporaryDirectory() as directory:
                home, config = self.linked_home(Path(directory))
                marker = home / "state/slack-manifest-migrations.json"
                if pending:  # Even a retry for team:read stops if a reinstall dropped a required permission.
                    migrations.settings.save_config(marker, {
                        "version": migrations.MIGRATION_VERSION, "team_id": "TTEST", "app_id": "ATEST",
                        "optional_pending": ["team:read"], "optional_checked_at": "2000-01-01T00:00:00+00:00"})
                current = migrations.migrate_manifest(self.manifest())[0]
                granted = [set(migrations.NEEDED_BOT_SCOPES), {"im:history"}] if pending else [{"im:history"}, {"im:history"}]
                with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                    migrations, "remote_manifest", return_value=current
                ), patch.object(migrations, "granted_bot_scopes", side_effect=granted), patch.object(
                    migrations.slack_credentials, "receive", return_value={"SLACK_APP_TOKEN": "xapp-f", "SLACK_BOT_TOKEN": "xoxb-f"}
                ):
                    with self.assertRaisesRegex(RuntimeError, "users:read"):
                        migrations.reconcile(home, config, self.values())
                if not pending:
                    self.assertFalse(marker.exists())

    def test_optional_scope_is_requested_but_not_needed(self):
        self.assertIn("team:read", migrations.REQUIRED_BOT_SCOPES)
        self.assertNotIn("team:read", migrations.NEEDED_BOT_SCOPES)
        original = self.manifest()
        self.assertIn("team:read", migrations.migrate_manifest(original)[0]["oauth_config"]["scopes"]["bot"])
        without = migrations.migrate_manifest(original, include_optional=False)[0]
        self.assertNotIn("team:read", without["oauth_config"]["scopes"]["bot"])

    def test_temporary_project_uses_local_source_without_changing_linked_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, _ = self.linked_home(root)
            project = home / "integrations/slack-cli"
            with tempfile.TemporaryDirectory() as transaction:
                temporary = migrations._migration_project(
                    project, self.manifest(), "TTEST", "ATEST", Path(transaction)
                )
                config = json.loads((temporary / ".slack/config.json").read_text(encoding="utf-8"))
                self.assertEqual(config["manifest"]["source"], "local")
            self.assertFalse((project / ".slack/config.json").exists())

    def test_sync_command_supports_stable_and_transitional_slack_cli(self):
        success = lambda output: subprocess.CompletedProcess([], 0, output, "")
        with patch.object(migrations, "_run", return_value=success("--manifest-source string")):
            command = migrations._sync_command("slack", Path("."), "ATEST", "TTEST")
        self.assertEqual(command[-2:], ["--manifest-source", "local"])
        run = Mock(side_effect=[success("old help"), success("--force-remote")])
        with patch.object(migrations, "_run", run):
            command = migrations._sync_command("slack", Path("."), "ATEST", "TTEST")
        self.assertEqual(command[-3:], ["--experiment", "manifest-sync", "--force"])


if __name__ == "__main__":
    unittest.main()


class SlackCliFailureTests(unittest.TestCase):
    def failure(self, stdout="", stderr=""):
        result = subprocess.CompletedProcess(["slack"], 1, stdout=stdout, stderr=stderr)
        return str(migrations._cli_failure(result, "Slack could not change the description", 'tag t1 describe "x"'))

    def test_says_what_slack_reported_and_only_suggests_login_for_sign_in_problems(self):
        busy = self.failure(stderr="\x1b[31m✗ The request to Slack timed out (ratelimited)\x1b[0m\n")
        self.assertEqual(busy, "Slack could not change the description (The request to Slack timed out (ratelimited)); "
                               'retry `tag t1 describe "x"`')
        signed_out = self.failure(stderr="Error: invalid_auth: Your session has expired\n")
        self.assertIn("run `slack login`, then retry", signed_out)
        self.assertIn("invalid_auth", signed_out)

    def test_never_repeats_a_token_and_falls_back_to_login_without_output(self):
        self.assertNotIn("xoxb-secret", self.failure(stdout="token xoxb-secret-123 was rejected"))
        self.assertEqual(self.failure(), 'Slack could not change the description; run `slack login`, then retry `tag t1 describe "x"`')
