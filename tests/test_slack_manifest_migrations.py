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
        self.assertEqual(migrated["oauth_config"]["scopes"]["bot"],
                         ["chat:write", "users:read", "im:history"])
        self.assertEqual(migrated["settings"]["event_subscriptions"]["bot_events"],
                         ["app_mention", "message.im"])
        self.assertFalse(migrations.migrate_manifest(migrated)[1])

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
            ), patch.object(migrations, "granted_bot_scopes", return_value={"im:history"}), patch.object(
                migrations, "_run"
            ) as run:
                self.assertFalse(migrations.reconcile(home, config, self.values(), interactive=False))
            run.assert_not_called()
            marker = json.loads((home / "state/slack-manifest-migrations.json").read_text())
            self.assertEqual(marker["version"], migrations.MIGRATION_VERSION)

    def test_headless_start_stops_before_remote_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, config = self.linked_home(root)
            with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                migrations, "remote_manifest", return_value=self.manifest()
            ), patch.object(migrations, "granted_bot_scopes", return_value=set()), patch.object(
                migrations, "_run"
            ) as run:
                with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                    migrations.reconcile(home, config, self.values(), interactive=False)
            run.assert_not_called()
            self.assertFalse((home / "state/slack-manifest-migrations.json").exists())

    def test_interactive_start_syncs_installs_refreshes_and_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, config = self.linked_home(root)
            success = subprocess.CompletedProcess([], 0, "", "")
            fresh = {"SLACK_APP_TOKEN": "xapp-fresh-token", "SLACK_BOT_TOKEN": "xoxb-fresh-token"}
            with patch.object(migrations.shutil, "which", return_value="/bin/slack"), patch.object(
                migrations, "remote_manifest", return_value=self.manifest()
            ), patch.object(migrations, "granted_bot_scopes", side_effect=[set(), {"im:history"}]), patch.object(
                migrations, "_sync_command", return_value=["/bin/slack", "manifest", "sync"]
            ), patch.object(migrations, "_run", return_value=success) as run, patch.object(
                migrations.slack_credentials, "receive", return_value=fresh
            ) as receive:
                self.assertTrue(migrations.reconcile(home, config, self.values(), interactive=True))
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[0], ["/bin/slack", "manifest", "sync"])
            self.assertEqual(run.call_args_list[1].args[0][1:3], ["app", "install"])
            receive.assert_called_once()
            saved = migrations.settings.read_config(config)
            self.assertEqual(saved["SLACK_BOT_TOKEN"], "xoxb-fresh-token")
            self.assertTrue((home / "state/slack-manifest-migrations.json").is_file())

    def test_temporary_project_uses_local_source_without_changing_linked_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, _ = self.linked_home(root)
            project = home / "integrations/slack-cli"
            with tempfile.TemporaryDirectory() as transaction:
                temporary = migrations._migration_project(
                    project, self.manifest(), "TTEST", "ATEST", Path(transaction)
                )
                config = json.loads((temporary / ".slack/config.json").read_text())
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
