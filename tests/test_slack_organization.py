"""Organization authorization must never become a cross-workspace capability."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import opentag_setup as setup, slack_identity as identity
from scripts import slack_app_create as creation, slack_credentials as credentials
from scripts import slack_manifest_migrations as migrations, slack_channels, tag_config


ORG_AUTH = {
    "ok": True, "enterprise_id": "EORG", "team_id": None,
    "is_enterprise_install": True, "app_id": "AAPP", "bot_id": "BBOT", "user_id": "UBOT",
}
LISTING = ("\x1b[32mSandbox (Team ID: EORG)\x1b[0m\nUser ID: WOWNER\n"
           "Authorization Level: Organization\nPersonal (Team ID: TOLD)\nUser ID: UOLD\n")


class OrganizationIdentityTests(unittest.TestCase):
    def validate(self, api, **kwargs):
        return identity.validate("private", team_id="TSELECTED", enterprise_id="EORG",
                                 app_id="AAPP", label="Bot token", api=api, **kwargs)

    def test_accounts_and_members_keep_workspace_and_organization_distinct(self):
        self.assertEqual(setup.authorized_accounts(LISTING), [("Sandbox", "EORG"), ("Personal", "TOLD")])
        self.assertEqual(setup.authorized_workspaces(LISTING), [("Personal", "TOLD")])
        self.assertEqual(setup.authorized_members(LISTING, "EORG"), ["WOWNER"])
        self.assertEqual(setup.authorized_members(LISTING, "TOLD"), ["UOLD"])
        self.assertIsNotNone(tag_config.validation_error("SLACK_TEAM_ID", "EORG"))
        self.assertIsNone(tag_config.validation_error("SLACK_ENTERPRISE_ID", "EORG"))

    def test_grant_on_later_page_is_verified(self):
        api = Mock(side_effect=[ORG_AUTH, {"teams": [{"id": "TOTHER"}],
            "response_metadata": {"next_cursor": "next"}}, {"teams": [{"id": "TSELECTED"}]}])
        self.assertEqual(self.validate(api), ORG_AUTH)
        api.assert_called_with("private", "auth.teams.list", {"limit": "200", "cursor": "next"})

    def test_wrong_org_app_or_missing_grant_is_rejected(self):
        for responses, error in (
            ([dict(ORG_AUTH, enterprise_id="EOTHER")], "organization"),
            ([dict(ORG_AUTH, app_id="AOTHER")], "app"),
            ([ORG_AUTH, {"teams": [{"id": "TOTHER"}]}], "no grant"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, error):
                self.validate(Mock(side_effect=responses))

    def test_app_identity_uses_bot_record_when_auth_test_omits_app(self):
        auth = dict(ORG_AUTH)
        del auth["app_id"]
        api = Mock(side_effect=[auth, {"bot": {"app_id": "AAPP"}}, {"teams": [{"id": "TSELECTED"}]}])
        self.validate(api)
        self.assertEqual(api.call_args_list[1].args, ("private", "bots.info", {"bot": "BBOT", "team_id": "TSELECTED"}))
        with self.assertRaisesRegex(RuntimeError, "different Slack app"):
            self.validate(Mock(side_effect=[auth, {"bot": {"app_id": "AOTHER"}}]))

    def test_repeated_pagination_cursor_fails_closed(self):
        api = Mock(side_effect=[ORG_AUTH] + [{"teams": [], "response_metadata": {"next_cursor": "same"}}] * 2)
        with self.assertRaisesRegex(RuntimeError, "no grant"):
            self.validate(api)
        self.assertEqual(api.call_count, 3)

    def test_legacy_workspace_token_still_requires_exact_team(self):
        api = Mock(return_value={"team_id": "TOLD"})
        identity.validate("private", team_id="TOLD", label="Bot", api=api)
        api.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "different Slack workspace"):
            identity.validate("private", team_id="TOTHER", label="Bot", api=api)
        with self.assertRaisesRegex(RuntimeError, "selected organization"):
            identity.validate("private", team_id="TSELECTED", label="Bot", api=Mock(return_value=ORG_AUTH))

    def test_events_require_receiving_workspace_even_with_org_authorization(self):
        for body, allowed in (
            ({"team_id": "TSELECTED", "enterprise_id": "EORG"}, True),
            ({"team_id": "TOTHER", "event": {"team": "TSELECTED"}}, False),
            ({"enterprise_id": "EORG", "authorizations": [{"team_id": "TSELECTED"}]}, False),
            ({"context_team_id": "TSELECTED"}, True),
            ({"team_id": "TSELECTED", "view": {"app_installed_team_id": "TOTHER"}}, False),
            ({"team_id": "TOTHER", "context_team_id": "TSELECTED"}, False),
            ({"team_id": "TSELECTED", "api_app_id": "AOTHER"}, False),
            ({"team_id": "TSELECTED", "enterprise_id": "EOTHER"}, False),
            ({"type": "block_actions", "team": None, "user": {"team_id": "TSELECTED"}}, True),
            ({"type": "view_submission", "team": None, "view": {"app_installed_team_id": "TOTHER"}}, False),
        ):
            with self.subTest(body=body):
                self.assertEqual(identity.event_allowed(body, "TSELECTED", "EORG", "AAPP"), allowed)

    def test_workspace_grant_api_uses_post(self):
        with patch.object(slack_channels, "slack_api_post", return_value={"teams": []}) as post:
            self.assertEqual(slack_channels.slack_api("private", "auth.teams.list", {"limit": "200"}), {"teams": []})
        post.assert_called_once_with("private", "auth.teams.list", {"limit": "200"})

    def test_channel_discovery_sends_workspace_on_every_page(self):
        with patch.object(slack_channels, "slack_api", side_effect=[
            {"channels": [], "response_metadata": {"next_cursor": "next"}}, {"channels": []},
        ]) as api:
            slack_channels.list_channels("private", team_id="TSELECTED")
        self.assertEqual([c.args[2]["team_id"] for c in api.call_args_list], ["TSELECTED", "TSELECTED"])


class OrganizationSetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.config = self.home / "config/settings.json"
        self.project = setup.slack_project(self.home)
        self.values = {"SLACK_TEAM_ID": "TSELECTED", "SLACK_ENTERPRISE_ID": "EORG", "MFS_TOKEN": "keep"}
        tag_config.save_config(self.config, self.values)
        self.output = StringIO()
        redirect = redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def save_link(self):
        tag_config.save_config(self.project / ".slack/apps.json", {
            "apps": {"EORG": {"team_id": "EORG", "app_id": "AAPP"}},
        })

    def test_org_picker_persists_both_ids_without_reauthorizing(self):
        with patch.object(setup.tag_dependencies, "ensure_slack", return_value=Path("/bin/slack")), patch.object(
            setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, LISTING, "")
        ), patch.object(setup.ui, "choose", side_effect=[0, 0]), patch.object(
            setup, "ask_validated", return_value="TSELECTED"
        ), patch.object(setup, "run_slack_cli") as login:
            self.assertEqual(setup.connect_slack_cli(config_path=self.config), "TSELECTED")
        login.assert_not_called()
        self.assertEqual(tag_config.load_config(self.config), self.values)
        self.assertIn("Organization authorization found", self.output.getvalue())

    def test_pause_at_workspace_selection_does_not_login_or_mutate_config(self):
        with patch.object(setup.tag_dependencies, "ensure_slack", return_value=Path("/bin/slack")), patch.object(
            setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, LISTING, "")
        ), patch.object(setup.ui, "choose", side_effect=[0, 1]), patch.object(setup, "run_slack_cli") as login:
            self.assertIsNone(setup.connect_slack_cli(config_path=self.config))
        login.assert_not_called()
        self.assertEqual(tag_config.load_config(self.config), self.values)

    def test_interrupted_creation_resumes_same_org_app_and_workspace_grant(self):
        def interrupted(command, **kwargs):
            self.assertEqual(command[command.index("--team") + 1], "EORG")
            self.assertEqual(command[command.index("--org-workspace-grant") + 1], "TSELECTED")
            self.save_link()
            raise KeyboardInterrupt()
        run = Mock(side_effect=interrupted)
        with patch.object(creation.ui, "choose", return_value=0), self.assertRaises(KeyboardInterrupt):
            creation.create_app(self.project, "EORG", self.config, run)
        state = creation.read_object(self.project / "tag-create.json")
        self.assertEqual((state["team_id"], state["workspace_id"], state["app_id"]), ("EORG", "TSELECTED", "AAPP"))
        manifest = creation.read_object(self.project / "manifest.json")
        self.assertTrue(manifest["settings"]["org_deploy_enabled"])
        with patch.object(creation, "is_installed", return_value=True):
            self.assertEqual(creation.create_app(self.project, "EORG", self.config, run), "AAPP")
        self.assertEqual(run.call_count, 1)
        tag_config.update_config(self.config, {"SLACK_TEAM_ID": "TOTHER"})
        with self.assertRaisesRegex(RuntimeError, "workspace grant"):
            creation.create_app(self.project, "EORG", self.config, run)
        self.assertEqual(run.call_count, 1)

    def test_credential_handoff_uses_org_link_and_one_workspace_grant(self):
        self.save_link()
        tokens = {"SLACK_APP_TOKEN": "xapp-private", "SLACK_BOT_TOKEN": "xoxb-private"}
        def cli(command, **kwargs):
            self.assertEqual(command[command.index("--team") + 1], "EORG")
            self.assertEqual(command[command.index("--org-workspace-grant") + 1], "TSELECTED")
            self.assertEqual(kwargs["input"], "")
            metadata = creation.read_object(kwargs["cwd"] / ".slack/apps.json")
            self.assertEqual(metadata["apps"]["EORG"]["team_id"], "EORG")
            tag_config.save_config(Path(kwargs["env"]["TAG_SLACK_HANDOFF_FILE"]), tokens)
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch.object(credentials.slack_app_create, "is_installed", return_value=False), patch.object(credentials.shutil, "which", return_value="slack"), patch.object(credentials.subprocess, "run", side_effect=cli):
            self.assertEqual(credentials.receive(self.project, "TSELECTED", "AAPP", enterprise_id="EORG"), tokens)
        self.assertEqual(tag_config.load_config(self.config), self.values)

    def test_refresh_preserves_existing_org_grants(self):
        self.save_link()
        tokens = {"SLACK_APP_TOKEN": "xapp-private", "SLACK_BOT_TOKEN": "xoxb-private"}
        def cli(command, **kwargs):
            self.assertNotIn("--org-workspace-grant", command)
            self.assertEqual(command[command.index("--team") + 1], "EORG")
            tag_config.save_config(Path(kwargs["env"]["TAG_SLACK_HANDOFF_FILE"]), tokens)
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch.object(credentials.slack_app_create, "is_installed", return_value=True), patch.object(
            credentials.shutil, "which", return_value="slack"
        ), patch.object(credentials.subprocess, "run", side_effect=cli):
            self.assertEqual(credentials.receive(self.project, "TSELECTED", "AAPP", enterprise_id="EORG"), tokens)

    def test_migration_retries_failed_grant_without_recording_completion(self):
        self.save_link()
        self.values.update(SLACK_APP_ID="AAPP", SLACK_BOT_TOKEN="xoxb-old")
        tag_config.save_config(self.config, self.values)
        marker = self.home / "state/slack-manifest-migrations.json"
        remote, _ = migrations.migrate_manifest(migrations.REQUIRED_MANIFEST, enterprise=True)
        with patch.object(migrations.shutil, "which", return_value="slack"), patch.object(
            migrations, "remote_manifest", return_value=remote
        ), patch.object(migrations, "granted_bot_scopes", return_value=set(migrations.REQUIRED_BOT_SCOPES)), patch.object(
            migrations.slack_identity, "validate", side_effect=[RuntimeError("no grant"), ORG_AUTH]
        ) as validate:
            with self.assertRaisesRegex(RuntimeError, "no grant"):
                migrations.reconcile(self.home, self.config, self.values)
            self.assertFalse(marker.exists())
            self.assertFalse(migrations.reconcile(self.home, self.config, self.values))
            self.assertFalse(migrations.reconcile(self.home, self.config, self.values))
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["enterprise_id"], "EORG")
        self.assertEqual(tag_config.load_config(self.config)["MFS_TOKEN"], "keep")

    def test_org_manifest_upgrade_preserves_custom_settings(self):
        remote = {"settings": {"org_deploy_enabled": False, "allowed_ip_address_ranges": ["127.0.0.1/32"]},
                  "display_information": {"name": "Custom"}}
        migrated, changed = migrations.migrate_manifest(remote, enterprise=True)
        self.assertTrue(changed)
        self.assertEqual(migrated["display_information"], remote["display_information"])
        self.assertEqual(migrated["settings"]["allowed_ip_address_ranges"], ["127.0.0.1/32"])
        self.assertFalse(migrations.migrate_manifest(migrated, enterprise=True)[1])
        self.assertFalse(remote["settings"]["org_deploy_enabled"])


class OrganizationRuntimeTests(unittest.TestCase):
    def test_real_bolt_dispatch_handles_org_authorization_with_null_team(self):
        try:
            from scripts import slack_socket_agent as bridge
            from slack_bolt import App
            from slack_bolt.request import BoltRequest
        except ImportError:
            self.skipTest("Slack runtime dependencies unavailable")
        observed = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SLACK_BOT_TOKEN": "xoxb-private", "SLACK_TEAM_ID": "TSELECTED",
            "SLACK_ENTERPRISE_ID": "EORG", "SLACK_APP_ID": "AAPP", "TAG_INSTANCE_HOME": directory,
        }, clear=True), patch.object(bridge.slack_identity, "validate", return_value=ORG_AUTH), patch.object(
            bridge, "App", side_effect=lambda **kwargs: App(process_before_response=True, **kwargs)
        ):
            app = bridge.create_app("claude", 30, frozenset({"WOWNER"}))
            @app.event("tag_org_probe")
            def probe(event, context):
                observed.append(context.client.default_params.get("team_id"))
            for team in ("TOTHER", "TSELECTED"):
                payload = {"type": "event_callback", "team_id": team, "enterprise_id": "EORG",
                    "api_app_id": "AAPP", "event": {"type": "tag_org_probe", "user": "WOWNER"},
                    "authorizations": [{"enterprise_id": "EORG", "team_id": None,
                                         "is_enterprise_install": True, "user_id": "UBOT"}]}
                response = app.dispatch(BoltRequest(body=payload, mode="socket_mode"))
                self.assertEqual(response.status, 200)
        self.assertEqual(observed, ["TSELECTED"])

    def test_bridge_rejects_other_workspace_before_any_handler(self):
        try:
            from scripts import slack_socket_agent as bridge
            from tests.test_slack_socket_agent import FakeApp
            from slack_bolt.context import BoltContext
        except ImportError:
            self.skipTest("Slack runtime dependencies unavailable")
        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-private", "SLACK_TEAM_ID": "TSELECTED",
                                     "SLACK_ENTERPRISE_ID": "EORG", "SLACK_APP_ID": "AAPP"}, clear=True), patch.object(
            bridge, "App", return_value=FakeApp()
        ) as app, patch.object(bridge.slack_identity, "validate", return_value=ORG_AUTH):
            bridge.create_app("claude", 30, frozenset({"WOWNER"}))
        boundary = app.call_args.kwargs["before_authorize"]
        authorize = app.call_args.kwargs["authorize"]
        context = BoltContext()
        context["client"] = app.call_args.kwargs["client"]
        next_handler = Mock(return_value="accepted")
        self.assertEqual(boundary({"team_id": "TOTHER"}, context, next_handler).status, 200)
        next_handler.assert_not_called()
        body = {"type": "block_actions", "team": None, "user": {"team_id": "TSELECTED"}}
        self.assertEqual(boundary(body, context, next_handler), "accepted")
        self.assertEqual(body["team"], {"id": "TSELECTED"})
        self.assertEqual(context.client.default_params["team_id"], "TSELECTED")
        self.assertEqual(authorize("EORG", "TSELECTED", "WOWNER").team_id, "TSELECTED")
        self.assertIsNone(authorize("EORG", "TOTHER", "WOWNER"))


if __name__ == "__main__":
    unittest.main()
