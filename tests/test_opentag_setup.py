from __future__ import annotations

import stat
import json
import struct
import subprocess
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import opentag_setup
from scripts.opentag_setup import (
    ask_required,
    main,
    render_env,
    runtime_requirement,
    write_config,
)


class OpenTagSetupTests(unittest.TestCase):
    def test_finish_setup_leaves_start_as_a_separate_command(self):
        channel = opentag_setup.slack_channels.SlackChannel(
            "C123", "general", False, True
        )
        values = {"OPENTAG_BACKEND": "claude", "OPENTAG_BOT_NAME": "Tag", "SLACK_ALLOWED_USER_IDS": "UOWNER"}
        connected = {"name": "Claude", "state": "connected", "account": "Claude Max · maya@example.com"}

        with patch.dict(os.environ, {"TAG_ID": "personal"}), patch.object(
            opentag_setup.tag_ai, "connection", return_value=connected
        ) as check, patch.object(opentag_setup.tag_ai, "setup_step") as step, patch.object(
            opentag_setup.subprocess, "run"
        ) as run, redirect_stdout(StringIO()) as output:
            result = opentag_setup.finish_setup(Path("settings.json"), values, [channel])

        self.assertEqual(result, 0)
        # Only the default agent's connection is checked; setup never starts services.
        self.assertEqual("claude", check.call_args.args[1])
        step.assert_not_called()
        run.assert_not_called()
        self.assertIn("Claude connected · Claude Max · maya@example.com",
                      " ".join(output.getvalue().split()))
        self.assertNotIn("MFS client", output.getvalue())
        self.assertIn("Next step · start Tag", output.getvalue())
        self.assertIn("tag personal start", output.getvalue())
        self.assertIn("Tag is still stopped. Run this command", output.getvalue())
        self.assertIn("No services were started", output.getvalue())
        self.assertIn("welcome DM with the community help link", output.getvalue())

    def test_finish_setup_returns_to_the_ai_step_when_the_default_agent_lapsed(self):
        expired = {"name": "Codex", "state": "expired", "account": None, "detail": "Sign in to Codex again."}
        connected = {"name": "Claude", "state": "connected", "account": "Claude Pro"}
        with patch.object(opentag_setup.tag_ai, "connection", side_effect=[expired, connected]), patch.object(
            opentag_setup.tag_ai, "setup_step",
            return_value={"OPENTAG_BACKEND": "claude", "OPENTAG_DEFAULT_MODEL": "claude:claude-opus-5-5"},
        ) as step, redirect_stdout(StringIO()) as output:
            result = opentag_setup.finish_setup(Path("settings.json"), {"OPENTAG_BACKEND": "codex"}, [])
        self.assertEqual(result, 0)
        step.assert_called_once()
        self.assertIn("Codex isn't ready: Sign-in expired · Sign in to Codex again.", " ".join(output.getvalue().split()))
        self.assertIn("✓ Claude connected · Claude Pro", " ".join(output.getvalue().split()))
    def test_bot_name_rejects_unicode_controls_and_line_separators(self):
        error = "Use a name from 1 to 35 characters without line breaks"
        for character in ("\x7f", "\x85", "\u2028", "\u2029"):
            with self.subTest(character=ascii(character)):
                self.assertEqual(
                    opentag_setup.settings.validation_error(
                        "OPENTAG_BOT_NAME", f"Tag{character}Name"
                    ),
                    error,
                )

    def test_text_entry_prompts_share_the_tui_content_gutter(self):
        with patch("builtins.input", return_value="") as entered:
            self.assertEqual(opentag_setup.ask("Assistant name", "Maxine's Tag"), "Maxine's Tag")
        entered.assert_called_once_with("  Assistant name [Maxine's Tag]: ")

    def test_uv_is_optional_when_managed_runtime_and_backend_are_available(self):
        def executable(command: str) -> str | None:
            return "/usr/local/bin/codex" if command == "codex" else None

        with patch.object(opentag_setup.shutil, "which", side_effect=executable), patch.object(
            opentag_setup.Path, "is_file", return_value=True
        ), patch.object(opentag_setup.ui, "message") as message:
            self.assertTrue(opentag_setup.check_prerequisites("codex"))

        self.assertTrue(any("optional" in call.args[0] for call in message.call_args_list))

    def test_curated_waterdrop_catalog_has_five_elements_and_16_signatures(self):
        self.assertEqual(opentag_setup.WATERDROP_BASE_COUNT, 60)
        self.assertEqual(opentag_setup.WATERDROP_SIGNATURE_COUNT, 16)
        self.assertEqual(opentag_setup.WATERDROP_RECIPE_COUNT, 960)
        expected = {
            0: ("metal", "mist", "glass", "clean"),
            2: ("water", "mist", "glass", "clean"),
            4: ("soil", "mist", "glass", "clean"),
            15: ("metal", "mist", "pearl", "clean"),
            60: ("metal", "mist", "glass", "rose-cheeks"),
            959: ("soil", "veil", "frost", "heart-mark"),
        }
        for index, identity in expected.items():
            with self.subTest(index=index):
                recipe = opentag_setup.waterdrop_recipe("ignored", index)
                actual = (
                    recipe["body"].name,
                    recipe["background"].name,
                    recipe["highlight"],
                    recipe["signature"],
                )
                self.assertEqual(actual, identity)

    def test_waterdrop_body_and_background_share_the_element_color(self):
        palettes = {
            body.name: opentag_setup._waterdrop_palette(
                opentag_setup.waterdrop_recipe("ignored", index)
            )
            for index, body in enumerate(opentag_setup.WATERDROP_BODIES)
        }

        for key in ("E", "P"):
            with self.subTest(element="metal", palette_key=key):
                self.assertLessEqual(max(palettes["metal"][key]) - min(palettes["metal"][key]), 20)
        for element, dominant_channel in (("wood", 1), ("water", 2), ("fire", 0)):
            for key in ("E", "P"):
                with self.subTest(element=element, palette_key=key):
                    color = palettes[element][key]
                    self.assertEqual(color[dominant_channel], max(color))
        for key in ("E", "P"):
            with self.subTest(element="soil", palette_key=key):
                red, green, blue = palettes["soil"][key]
                self.assertGreater(red, blue)
                self.assertGreater(green, blue)

    def test_first_100_curated_waterdrops_are_visually_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            icons = {
                opentag_setup.branded_profile_icon(
                    project, "preview", recipe_index=index
                ).read_bytes()
                for index in range(100)
            }
            self.assertEqual(len(icons), 100)

    def test_icon_upload_version_check_parses_slack_cli_output(self):
        for output, expected in (("Using slack v4.7.0", True), ("Using slack v4.6.9", False)):
            with self.subTest(output=output), patch.object(
                opentag_setup.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, output, ""),
            ):
                self.assertIs(opentag_setup.slack_cli_supports_icon_upload(), expected)

    def test_slack_cli_receives_the_tag_managed_icon_path(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            assets = project / "assets"
            assets.mkdir()
            icon = assets / "tag-profile.png"
            icon.write_bytes(b"icon")
            completed = subprocess.CompletedProcess([], 0, "", "")
            with patch.object(opentag_setup.shutil, "which", return_value="/bin/slack"), patch.object(
                opentag_setup.subprocess, "run", return_value=completed
            ) as run:
                self.assertEqual(opentag_setup.run_slack_cli(["app", "install"], cwd=project), 0)
            self.assertEqual(run.call_args.kwargs["env"]["SLACK_CLI_APP_ICON_PATH"], str(icon))


    def test_connector_files_are_separate_for_each_tag_home(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            channels = [opentag_setup.slack_channels.SlackChannel("C1", "test", False, True)]
            original = opentag_setup.write_slack_connector("T123", channels, "30", home=root / "working")
            before = original.read_bytes()
            test = opentag_setup.write_slack_connector("T123", channels, "7", home=root / "test")
            self.assertNotEqual(original, test)
            self.assertEqual(original.read_bytes(), before)
            self.assertEqual(test.parent, root / "test/integrations/mfs/connectors")
            self.assertFalse((root / ".mfs").exists())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(test.stat().st_mode), 0o600)

    def test_connector_rejects_workspace_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                opentag_setup.write_slack_connector("T/../../outside", [], "7", home=Path(directory))

    def test_app_repair_saves_link_and_resumes_without_relinking_or_popup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / "config/settings.json"
            opentag_setup.settings.save_config(config, {"SLACK_APP_ID": "ATEST", "SLACK_TEAM_ID": "TTEST"})
            def link_app(*args, **kwargs):
                path = kwargs["cwd"] / ".slack/apps.dev.json"
                path.write_text(json.dumps({"TTEST": {"app_id": "ATEST", "team_id": "TTEST"}}), encoding="utf-8")
                return 0
            with patch.object(opentag_setup, "run_slack_cli", side_effect=link_app) as link, patch.object(
                opentag_setup, "inspect_slack_app", side_effect=[False, True]
            ), patch.object(opentag_setup.ui, "choose", side_effect=[0, 2]), patch.object(
                opentag_setup.webbrowser, "open"
            ) as browser, redirect_stdout(StringIO()):
                with self.assertRaises(opentag_setup.ui.Paused):
                    opentag_setup.choose_slack_app(home, "TTEST", config)
                self.assertEqual(opentag_setup.choose_slack_app(home, "TTEST", config), "ATEST")
            link.assert_called_once()
            browser.assert_not_called()
            self.assertEqual(opentag_setup.settings.read_config(config)["SLACK_APP_ID"], "ATEST")

    def test_existing_cli_link_without_tag_marker_skips_link_and_checks_app(self):
        for filename in ("apps.dev.json", "apps.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                project = opentag_setup.slack_project(home)
                (project / ".slack" / filename).write_text(json.dumps({
                    "TTEST": {"app_id": "ATEST", "team_id": "TTEST", "user_id": "UTEST"}
                }), encoding="utf-8")
                config = home / "config/settings.json"
                opentag_setup.settings.save_config(config, {"SLACK_APP_ID": "ATEST"})
                with patch.object(opentag_setup, "run_slack_cli") as link, patch.object(
                    opentag_setup, "inspect_slack_app", return_value=True
                ) as inspect, patch.object(opentag_setup.ui, "choose") as choose, redirect_stdout(StringIO()):
                    self.assertEqual(opentag_setup.choose_slack_app(home, "TTEST", config), "ATEST")
                link.assert_not_called()
                choose.assert_not_called()
                inspect.assert_called_once_with(project, "ATEST", issues=[])

    def test_conflicting_cli_link_is_preserved_even_with_matching_tag_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = opentag_setup.slack_project(home)
            path = project / ".slack/apps.dev.json"
            original = json.dumps({"TTEST": {"app_id": "AOTHER", "team_id": "TTEST"}})
            path.write_text(original, encoding="utf-8")
            (project / "tag-linked.json").write_text(json.dumps({"app_id": "ATEST", "team_id": "TTEST"}), encoding="utf-8")
            config = home / "config/settings.json"
            opentag_setup.settings.save_config(config, {"SLACK_APP_ID": "ATEST"})
            with patch.object(opentag_setup, "run_slack_cli") as link, redirect_stdout(StringIO()):
                with self.assertRaisesRegex(RuntimeError, "already linked to another app"):
                    opentag_setup.choose_slack_app(home, "TTEST", config)
            link.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_missing_home_event_explains_repair_without_opening_browser(self) -> None:
        manifest = (opentag_setup.ROOT / "slack-app-manifest.yaml").read_text(encoding="utf-8").replace("      - app_home_opened\n", "")
        with patch.object(opentag_setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, manifest, "")), patch.object(
            opentag_setup.webbrowser, "open"
        ) as browser, redirect_stdout(StringIO()) as output:
            self.assertFalse(opentag_setup.inspect_slack_app(Path("."), "ATEST"))
        self.assertIn("Event Subscriptions → Subscribe to bot events", output.getvalue())
        self.assertIn("Add app_home_opened", output.getvalue())
        browser.assert_not_called()

    def test_missing_agent_view_omits_redundant_manual_guidance(self) -> None:
        manifest = (opentag_setup.ROOT / "slack-app-manifest.yaml").read_text(encoding="utf-8").replace(
            "  agent_view:\n    agent_description: Run approved Codex or Claude tasks from Slack.\n",
            "",
        )
        with patch.object(
            opentag_setup.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, manifest, ""),
        ), redirect_stdout(StringIO()) as output:
            self.assertFalse(opentag_setup.inspect_slack_app(Path("."), "ATEST"))
        rendered = output.getvalue()
        self.assertNotIn("App configuration needs attention", rendered)
        self.assertNotIn("Agent view enabled", rendered)
        self.assertNotIn("Tag can enable Agent messaging", rendered)
        self.assertNotIn("In Slack app settings", rendered)
        self.assertNotIn("Agents & AI Apps", rendered)
        self.assertNotIn("Keep existing settings", rendered)

    def test_multiple_app_issues_offer_only_browser_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / "config/settings.json"
            opentag_setup.settings.save_config(config, {"SLACK_APP_ID": "ATEST"})
            def failed_check(project, app_id, *, issues):
                issues[:] = ["Home tab enabled", "public channel join"]
                return False
            with patch.object(opentag_setup, "saved_slack_app", return_value=True), patch.object(
                opentag_setup, "inspect_slack_app", side_effect=failed_check
            ), patch.object(opentag_setup.ui, "choose", side_effect=[0, 2]) as choose, patch.object(
                opentag_setup.webbrowser, "open"
            ) as browser, redirect_stdout(StringIO()):
                with self.assertRaises(opentag_setup.ui.Paused):
                    opentag_setup.choose_slack_app(home, "TTEST", config)
            self.assertEqual(choose.call_args_list[0].args[1], ["Open app settings", "Check again", "Save and exit"])
            browser.assert_called_once_with("https://api.slack.com/apps/ATEST")

    def test_missing_agent_view_is_repaired_automatically_and_rechecked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / "config/settings.json"
            opentag_setup.settings.save_config(config, {"SLACK_APP_ID": "ATEST"})

            def inspect(project, app_id, *, issues):
                if inspect.calls == 0:
                    issues[:] = ["Agent view enabled"]
                    inspect.calls += 1
                    return False
                return True

            inspect.calls = 0
            with patch.object(opentag_setup, "saved_slack_app", return_value=True), patch.object(
                opentag_setup, "inspect_slack_app", side_effect=inspect
            ), patch.object(opentag_setup.ui, "choose") as choose, patch.object(
                opentag_setup.slack_manifest_migrations, "enable_agent_view", return_value=True
            ) as enable, redirect_stdout(StringIO()):
                self.assertEqual(opentag_setup.choose_slack_app(home, "TTEST", config), "ATEST")
            choose.assert_not_called()
            enable.assert_called_once()
            self.assertEqual(enable.call_args.args[:3], (opentag_setup.slack_project(home), "ATEST", "TTEST"))
            self.assertTrue(callable(enable.call_args.kwargs["approve_legacy"]))

    def test_already_enabled_agent_messaging_does_not_offer_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / "config/settings.json"
            opentag_setup.settings.save_config(config, {"SLACK_APP_ID": "ATEST"})

            def stale_check(project, app_id, *, issues):
                issues[:] = ["Agent view enabled"]
                return False

            with patch.object(opentag_setup, "saved_slack_app", return_value=True), patch.object(
                opentag_setup, "inspect_slack_app", side_effect=stale_check
            ), patch.object(opentag_setup.ui, "choose") as choose, patch.object(
                opentag_setup.slack_manifest_migrations, "enable_agent_view", return_value=False
            ), redirect_stdout(StringIO()):
                self.assertEqual(opentag_setup.choose_slack_app(home, "TTEST", config), "ATEST")
            choose.assert_not_called()

    def test_failed_automatic_agent_repair_offers_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / "config/settings.json"
            opentag_setup.settings.save_config(config, {"SLACK_APP_ID": "ATEST"})

            def failed_check(project, app_id, *, issues):
                issues[:] = ["Agent view enabled"]
                return False

            with patch.object(opentag_setup, "saved_slack_app", return_value=True), patch.object(
                opentag_setup, "inspect_slack_app", side_effect=failed_check
            ), patch.object(opentag_setup.ui, "choose", return_value=3) as choose, patch.object(
                opentag_setup.slack_manifest_migrations,
                "enable_agent_view",
                side_effect=RuntimeError("sync failed"),
            ), redirect_stdout(StringIO()):
                with self.assertRaises(opentag_setup.ui.Paused):
                    opentag_setup.choose_slack_app(home, "TTEST", config)
            self.assertEqual(choose.call_args.args[1], [
                "Retry Agent messaging with Slack CLI",
                "Open app settings",
                "Check again",
                "Save and exit",
            ])

    def test_slack_project_repairs_missing_hooks_and_preserves_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = opentag_setup.slack_project(Path(directory))
            hooks = project / ".slack/hooks.json"
            self.assertEqual(json.loads(hooks.read_text(encoding="utf-8")), {"hooks": {}})
            config = project / ".slack/config.json"
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["manifest"]["source"], "remote")
            config_before = config.read_bytes()
            hooks.unlink()
            opentag_setup.slack_project(Path(directory))
            self.assertTrue(hooks.is_file())
            self.assertEqual(config.read_bytes(), config_before)
            hooks_before = hooks.stat().st_mtime_ns
            opentag_setup.slack_project(Path(directory))
            self.assertEqual(hooks.stat().st_mtime_ns, hooks_before)

    def test_workspace_listing_deduplicates_accounts_and_strips_color(self) -> None:
        listing = (
            "\x1b[32mExample Team (Team ID: T123)\x1b[0m\nUser ID: U111\n"
            "Example Team (Team ID: T123)\nUser ID: U222\n"
            "Second Team (Team ID: T456)\n"
        )
        self.assertEqual(opentag_setup.authorized_workspaces(listing), [
            ("Example Team", "T123"), ("Second Team", "T456"),
        ])

    def test_slack_cli_output_redacts_tokens(self) -> None:
        value = "bot=xoxb-super-secret app=xapp-1-secret"
        redacted = opentag_setup.safe_cli_output(value)
        self.assertNotIn("super-secret", redacted)
        self.assertNotIn("xapp-1-secret", redacted)

    def test_connector_is_limited_to_explicit_channels_and_window(self) -> None:
        channels = [
            opentag_setup.slack_channels.SlackChannel("C1", "team", False, True),
            opentag_setup.slack_channels.SlackChannel("G2", "private-room", True, True),
        ]
        rendered = opentag_setup.render_slack_connector("T123", channels, "30")
        self.assertIn('channel_ids = ["C1", "G2"]', rendered)
        self.assertIn('channel_types = ["private_channel", "public_channel"]', rendered)
        self.assertIn('oldest = "now-30d"', rendered)
        self.assertIn('token = "env:MFS_SLACK_TOKEN"', rendered)

    def test_guided_setup_expands_and_validates_workspace_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home = root / "app/instances/default"
            user_home = root / "person"
            config = home / "config/settings.json"
            environment = {
                "HOME": str(user_home),
                "USERPROFILE": str(user_home),  # Windows expands ~ from USERPROFILE
                "TAG_HOME": str(root / "app"),
                "TAG_INSTANCE_HOME": str(home),
                "OPENTAG_WORKDIR": "~/chosen",
            }
            with patch.dict(os.environ, environment, clear=True), patch(
                "pathlib.Path.home", return_value=user_home
            ), patch.object(
                opentag_setup.ui, "screen", side_effect=RuntimeError("stop after initialization")
            ), self.assertRaisesRegex(RuntimeError, "stop after initialization"):
                opentag_setup.guided_setup(config)

            self.assertTrue((user_home / "chosen/.codex/config.toml").is_file())

            for invalid in ("relative", ""):
                with self.subTest(invalid=invalid), patch.dict(
                    os.environ, {**environment, "OPENTAG_WORKDIR": invalid}, clear=True
                ), self.assertRaisesRegex(ValueError, "must be an absolute path"):
                    opentag_setup.guided_setup(config)

    def test_guided_setup_uses_platform_workspace_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_home = Path(temporary_directory) / "person"
            instance = user_home / "Library/Application Support/Tag/instances/default"
            environment = {"HOME": str(user_home)}
            with patch.dict(os.environ, environment, clear=True), patch(
                "pathlib.Path.home", return_value=user_home
            ), patch("sys.platform", "darwin"), patch.object(
                opentag_setup.ui, "screen", side_effect=RuntimeError("stop after initialization")
            ), self.assertRaisesRegex(RuntimeError, "stop after initialization"):
                opentag_setup.guided_setup(instance / "config/settings.json")

            self.assertTrue((user_home / "Tag/default/.codex/config.toml").is_file())
            self.assertFalse((instance / "workspace").exists())

    @patch("builtins.input", side_effect=["", "UOWNER"])
    def test_required_owner_id_reprompts_until_set(self, _mock_input: object) -> None:
        self.assertEqual("UOWNER", ask_required("Owner Slack member ID"))

    def test_runtime_dependencies_come_from_the_pinned_requirement_file(self) -> None:
        self.assertEqual(runtime_requirement("mfs-server"), "mfs-server[slack]==0.4.6")

    def test_render_env_shell_quotes_values(self) -> None:
        rendered = render_env({"OPENTAG_WORKDIR": "/tmp/Tag workspace", "TOKEN": "a'b"})

        self.assertIn("export OPENTAG_WORKDIR='/tmp/Tag workspace'", rendered)
        self.assertIn("export TOKEN='a'\"'\"'b'", rendered)

    @unittest.skipIf(os.name == "nt", "Windows uses account directory ACLs, not POSIX mode bits")
    def test_write_config_uses_owner_only_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / ".env"
            write_config(path, {"OPENTAG_BACKEND": "codex"})

            mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(mode, 0o600)

class SetupDefaultSelectionTests(unittest.TestCase):
    def test_saved_default_with_a_connected_agent_skips_the_ai_step(self):
        saved = {"OPENTAG_BACKEND": "claude", "OPENTAG_DEFAULT_MODEL": "claude:claude-opus-5-5"}
        with patch.object(opentag_setup.tag_ai, "connection", return_value={"state": "connected"}) as check, patch.object(
            opentag_setup.tag_ai, "setup_step", return_value={"asked": "yes"}
        ) as step:
            self.assertIs(saved, opentag_setup.ensure_agent(Path("settings.json"), saved))
            self.assertEqual("claude", check.call_args.args[1])
            step.assert_not_called()
            # Reviewing setup, or a missing or lapsed choice, asks again.
            self.assertEqual({"asked": "yes"}, opentag_setup.ensure_agent(Path("settings.json"), saved, review=True))
            self.assertEqual({"asked": "yes"}, opentag_setup.ensure_agent(Path("settings.json"), {"OPENTAG_BACKEND": "codex"}))
            check.return_value = {"state": "expired"}
            self.assertEqual({"asked": "yes"}, opentag_setup.ensure_agent(Path("settings.json"), saved))
        self.assertEqual(3, step.call_count)


# ---- Onboarding v2 ------------------------------------------------------------------

def png(path: Path, width: int, height: int) -> Path:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")
    return path


class Client:
    """Drive setup over JSON lines, as Tag.app does: answers in, events out."""

    def __init__(self, replies: list):
        self.stdin = StringIO("".join(json.dumps(reply) + "\n" for reply in replies))
        self.stdout = StringIO()

    def __enter__(self):
        ui = opentag_setup.ui
        ui._history.clear()
        ui._replay.clear()
        ui._target = None
        self.patches = [
            patch.dict(os.environ, {ui.PROTOCOL_ENV: "jsonl"}),
            patch.object(sys, "stdin", self.stdin),
            patch.object(sys, "__stdout__", self.stdout),
            patch.object(sys, "stdout", sys.stdout),
        ]
        for item in self.patches:
            item.start()
        ui.enter_protocol()
        return self

    def __exit__(self, *_):
        for item in reversed(self.patches):
            item.stop()

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.stdout.getvalue().splitlines()]

    def questions(self) -> list[dict]:
        return [event for event in self.events() if event["type"] == "question"]


def answer(value: object) -> dict:
    return {"answer": value}


SIGN_INS = [
    {"id": "T0KLOVR", "name": "Klovr", "kind": "workspace", "user_id": "U01MAYA", "user_name": "maya"},
    {"id": "E0HOVER", "name": "Hover Sandbox", "kind": "organization", "user_id": "W01MAYA", "user_name": None},
]


class ProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.project = opentag_setup.slack_project(self.home)
        self.config = self.home / "config/settings.json"
        for item in (patch.object(opentag_setup, "other_tag_waterdrops", return_value=set()),
                     patch.object(opentag_setup.getpass, "getuser", return_value="maya"),
                     patch.object(opentag_setup, "slack_cli_too_old", return_value=False),
                     patch.dict(os.environ, {"TAG_ID": "t1"})):
            item.start()
            self.addCleanup(item.stop)

    def test_profile_question_shuffles_then_saves_name_and_description(self):
        with Client([answer("shuffle"), answer({"name": "Maya's Tag", "description": "Helps with launches"})]) as client:
            self.assertEqual(opentag_setup.choose_profile(self.project, self.config), "new")
        first, second = client.questions()
        self.assertEqual((first["id"], first["kind"], first["prompt"]), ("profile", "profile_picture", "Meet your new Tag"))
        self.assertEqual((first["name"], first["name_limit"], first["description"], first["description_limit"]),
                         ("Maya's Tag", 35, "", 140))
        self.assertEqual((first["picture"], first["error"], first["editing"], first["can_use_existing"],
                          first["can_go_back"]), ("waterdrop", None, False, True, False))
        self.assertTrue(first["picture_label"].startswith("Water · Tag waterdrop #"))
        self.assertTrue(Path(first["preview"]).is_absolute() and Path(first["preview"]).is_file())
        self.assertNotEqual(first["picture_label"], second["picture_label"])
        # A shuffle changes only the picture, so Back never replays it.
        self.assertFalse(second["can_go_back"])
        self.assertEqual([], opentag_setup.ui._history[:-1])
        values = opentag_setup.settings.load_config(self.config)
        self.assertEqual((values["OPENTAG_BOT_NAME"], values["OPENTAG_BOT_DESCRIPTION"]),
                         ("Maya's Tag", "Helps with launches"))
        state = opentag_setup.profile_picture(self.project)
        self.assertEqual(state["label"], second["picture_label"])

    def test_shuffle_picks_another_identity_no_other_tag_uses(self):
        current = 2
        everything_else = set(range(opentag_setup.WATERDROP_RECIPE_COUNT)) - {current, 7, 8}
        same = opentag_setup.waterdrop_recipe("", current)
        for _ in range(20):
            index = opentag_setup.shuffled_waterdrop_index(current, everything_else)
            self.assertIn(index, {7, 8})
            recipe = opentag_setup.waterdrop_recipe("", index)
            self.assertTrue(recipe["body"] != same["body"] or recipe["signature"] != same["signature"])

    def test_first_picture_is_seeded_by_tag_and_name_and_skips_other_tags(self):
        with patch.object(opentag_setup.hashlib, "sha256") as digest:
            digest.return_value.digest.return_value = b"\x00" * 32
            self.assertEqual(opentag_setup._assigned_waterdrop_index(self.project, "t1:First", occupied=set()), 2)
            self.assertEqual(opentag_setup._assigned_waterdrop_index(self.project, "t2:Second", occupied={2}), 7)
            # A remembered identity is kept unless another Tag took it since.
            self.assertEqual(opentag_setup._assigned_waterdrop_index(self.project, "t1:First", occupied=set()), 2)
            self.assertEqual(opentag_setup._assigned_waterdrop_index(self.project, "t1:First", occupied={2}), 7)
        with Client([answer("existing")]) as client:
            opentag_setup.choose_profile(self.project, self.config)
        digest_seed = "t1:Maya's Tag"
        self.assertIn(digest_seed, json.loads((self.project / "assets/tag-waterdrop-identities.json").read_text(encoding="utf-8")))
        self.assertEqual(client.questions()[0]["picture"], "waterdrop")

    def test_uploaded_picture_is_validated_copied_and_errors_reask(self):
        small = png(self.home / "small.png", 300, 300)
        text = self.home / "notes.txt"
        text.write_text("not a picture", encoding="utf-8")
        good = png(self.home / "My face.png", 640, 640)
        replies = [answer({"picture": str(small)}), answer({"picture": str(text)}), answer({"picture": str(good)}),
                   answer({"name": "x" * 36, "description": ""}), answer({"name": "Ok", "description": "a\nb"}),
                   answer({"name": "Maya's Tag", "description": ""})]
        with Client(replies) as client:
            self.assertEqual(opentag_setup.choose_profile(self.project, self.config), "new")
        questions = client.questions()
        self.assertEqual([q["error"] for q in questions], [
            None, "This picture is 300×300. Use one between 512×512 and 2000×2000 pixels.",
            "Use a PNG, JPEG, or GIF image.", None,
            "Use a name from 1 to 35 characters without line breaks", "Use one line of up to 140 characters",
        ])
        self.assertEqual((questions[3]["picture"], questions[3]["picture_label"]), ("custom", "My face.png"))
        copied = Path(questions[3]["preview"])
        self.assertEqual((copied.parent, copied.read_bytes()), ((self.project / "assets").resolve(), good.read_bytes()))
        self.assertEqual(opentag_setup.settings.load_config(self.config)["OPENTAG_BOT_NAME"], "Maya's Tag")

    def test_an_old_slack_cli_refuses_an_uploaded_picture(self):
        good = png(self.home / "face.png", 640, 640)
        with patch.object(opentag_setup, "slack_cli_too_old", return_value=True), \
                Client([answer({"picture": str(good)}), answer("existing")]) as client:
            opentag_setup.choose_profile(self.project, self.config)
        self.assertIn("Slack CLI 4.7", client.questions()[1]["error"])

    def test_existing_app_keeps_its_own_name_and_editing_hides_it(self):
        with Client([answer("existing")]):
            self.assertEqual(opentag_setup.choose_profile(self.project, self.config), "existing")
        self.assertNotIn("OPENTAG_BOT_NAME", opentag_setup.settings.load_config(self.config))
        with Client([answer("existing")]) as client, self.assertRaises(RuntimeError):
            opentag_setup.choose_profile(self.project, self.config, editing=True)
        self.assertEqual((client.questions()[0]["editing"], client.questions()[0]["can_use_existing"]), (True, False))

    def test_saved_name_and_picture_come_back_on_resume(self):
        opentag_setup.settings.save_config(self.config, {"OPENTAG_BOT_NAME": "Ops Tag", "OPENTAG_BOT_DESCRIPTION": "Ops"})
        with Client([answer("shuffle")]) as client, self.assertRaises(opentag_setup.ui.Paused):
            opentag_setup.choose_profile(self.project, self.config)
        shuffled = client.questions()[1]["picture_label"]
        with Client([]) as client, self.assertRaises(opentag_setup.ui.Paused):
            opentag_setup.choose_profile(self.project, self.config)
        question = client.questions()[0]
        self.assertEqual((question["name"], question["description"], question["picture_label"]),
                         ("Ops Tag", "Ops", shuffled))

    def test_terminal_asks_name_description_then_picture(self):
        with patch.object(opentag_setup, "ask", side_effect=["Helper", "Runs launches"]), patch.object(
            opentag_setup.ui, "choose", side_effect=[1, 0]
        ) as choose, redirect_stdout(StringIO()):
            self.assertEqual(opentag_setup.choose_profile(self.project, self.config), "new")
        self.assertEqual(choose.call_args.args[:2], ("Profile picture", [
            "Keep this picture", "Shuffle picture", "Choose my own picture", "Open picture preview",
            "Use an existing app", "Save and exit"]))
        values = opentag_setup.settings.load_config(self.config)
        self.assertEqual((values["OPENTAG_BOT_NAME"], values["OPENTAG_BOT_DESCRIPTION"]), ("Helper", "Runs launches"))

    def test_terminal_test_mode_names_the_tag_as_a_test(self):
        def accept_default(prompt, default=None, **_kwargs):
            return default or ""
        with patch.object(opentag_setup, "ask", side_effect=accept_default), patch.object(
            opentag_setup.ui, "choose", return_value=0
        ), redirect_stdout(StringIO()) as output:
            opentag_setup.choose_profile(self.project, self.config, test_mode=True)
        self.assertEqual(opentag_setup.settings.load_config(self.config)["OPENTAG_BOT_NAME"], "TEST · Maya's Tag")
        self.assertIn("TEST MODE", output.getvalue())

    def test_suggested_name_uses_this_computers_account(self):
        with patch.object(opentag_setup.getpass, "getuser", return_value="river_song"):
            self.assertEqual(opentag_setup.suggested_assistant_name(), "River's Tag")


class OtherTagsTests(unittest.TestCase):
    def test_pictures_and_apps_of_other_tags_are_found_in_their_homes(self):
        from scripts import tag_instances
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            own = tag_instances.create(root, "mine").home
            other = tag_instances.create(root, "devtag").home
            assets = other / "integrations/slack-cli/assets"
            opentag_setup.settings.save_config(assets / "tag-picture.json", {"picture": "waterdrop", "index": 42})
            opentag_setup.settings.save_config(assets / "tag-waterdrop-identities.json", {"T1:Old": {"index": 9}})
            opentag_setup.settings.save_config(own / "integrations/slack-cli/assets/tag-picture.json",
                                               {"picture": "waterdrop", "index": 5})
            opentag_setup.settings.save_config(other / "config/settings.json", {
                "SLACK_APP_ID": "A08TAGDEV1", "SLACK_TEAM_ID": "T0KLOVR", "OPENTAG_BOT_NAME": "Tag Dev"})
            tag_instances.set_nickname(root, "devtag", "launch")
            with patch.dict(os.environ, {"TAG_HOME": str(root), "TAG_INSTANCE_HOME": str(own)}):
                self.assertEqual(opentag_setup.other_tag_waterdrops(), {42, 9})
                self.assertEqual(opentag_setup.other_tag_apps(), {"A08TAGDEV1": {
                    "name": "Tag Dev", "tag": "launch", "team": "T0KLOVR", "workspace": "T0KLOVR"}})


class WorkspaceAndOwnerTests(unittest.TestCase):
    LISTING = ("\x1b[32mKlovr (Team ID: T0KLOVR)\x1b[0m\nUser ID: U01MAYA\nUser Name: maya\n"
               "Authorization Level: Workspace\n\nHover Sandbox (Team ID: E0HOVER)\nUser ID: W01MAYA\n"
               "Authorization Level: Organization\nBroken (Team ID: unknown)\nUser ID: UNOPE\n")

    def test_sign_ins_carry_kind_member_and_handle(self):
        self.assertEqual(opentag_setup.slack_sign_ins(self.LISTING), [
            {"id": "T0KLOVR", "name": "Klovr", "kind": "workspace", "user_id": "U01MAYA", "user_name": "maya"},
            {"id": "E0HOVER", "name": "Hover Sandbox", "kind": "organization", "user_id": "W01MAYA", "user_name": None},
        ])

    def test_owner_comes_from_the_sign_in_then_auth_test_then_stops(self):
        self.assertEqual(opentag_setup.signed_in_member("T0KLOVR", SIGN_INS), "U01MAYA")
        unknown = [{**SIGN_INS[0], "user_id": None}]
        auth = json.dumps({"ok": True, "team_id": "T0KLOVR", "user_id": "U02MAYA"})
        with patch.object(opentag_setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, auth, "")) as run:
            self.assertEqual(opentag_setup.signed_in_member("T0KLOVR", unknown), "U02MAYA")
        self.assertEqual(run.call_args.args[0][1:5], ["api", "auth.test", "--team", "T0KLOVR"])
        for output in (json.dumps({"ok": False, "error": "not_authed"}),
                       json.dumps({"ok": True, "team_id": "TOTHER", "user_id": "U02MAYA"}),
                       json.dumps({"ok": True, "team_id": "T0KLOVR", "user_id": "U02BOT", "bot_id": "B1"})):
            with self.subTest(output=output), patch.object(
                    opentag_setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
            ), self.assertRaisesRegex(RuntimeError, "Sign in to Slack again"):
                opentag_setup.signed_in_member("T0KLOVR", unknown)

    def test_a_saved_owner_is_kept(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(opentag_setup, "list_slack_sign_ins") as listing:
            config = Path(directory) / "settings.json"
            values = {"SLACK_ALLOWED_USER_IDS": "U111,U222", "SLACK_TEAM_ID": "T0KLOVR"}
            self.assertIs(opentag_setup.ensure_owner(config, values), values)
        listing.assert_not_called()

    def test_workspace_question_lists_sign_ins_and_signs_in_only_when_asked(self):
        with patch.object(opentag_setup.tag_dependencies, "ensure_slack", return_value=Path("/bin/slack")), patch.object(
            opentag_setup.tag_dependencies, "activate_slack"
        ), patch.object(opentag_setup, "list_slack_sign_ins", return_value=SIGN_INS), patch.object(
            opentag_setup, "slack_sign_in", return_value=True
        ) as sign_in, Client([answer("sign_in"), answer("T0KLOVR")]) as client:
            selection = opentag_setup.connect_slack_workspace()
        sign_in.assert_called_once()
        self.assertEqual(selection, ("T0KLOVR", "Klovr", "", "", "T0KLOVR", "U01MAYA", "maya"))
        question = client.questions()[0]
        self.assertEqual((question["id"], question["prompt"]), ("workspace", "Which workspace?"))
        self.assertEqual(question["options"], ["Klovr", "Hover Sandbox (organization)", "Sign in to another workspace",
                                               "Exit · finish setup later"])
        self.assertEqual(question["option_ids"], ["T0KLOVR", "E0HOVER", "sign_in", "exit"])
        self.assertEqual(question["workspaces"], SIGN_INS)

    def test_without_sign_ins_setup_signs_in_first(self):
        with patch.object(opentag_setup.tag_dependencies, "ensure_slack", return_value=Path("/bin/slack")), patch.object(
            opentag_setup.tag_dependencies, "activate_slack"
        ), patch.object(opentag_setup, "list_slack_sign_ins", side_effect=[[], SIGN_INS[:1], SIGN_INS[:1]]), patch.object(
            opentag_setup, "slack_sign_in", return_value=True
        ) as sign_in, Client([answer(0)]):
            self.assertEqual(opentag_setup.connect_slack_workspace().team_id, "T0KLOVR")
        sign_in.assert_called_once()

    def test_organization_workspace_is_entered_by_address_or_id(self):
        replies = [answer("E0HOVER"), answer("manual"), answer("E0HOVER"), answer("nothing here"),
                   answer("https://app.slack.com/client/T0HOVERENG/C123")]
        with patch.object(opentag_setup.tag_dependencies, "ensure_slack", return_value=Path("/bin/slack")), patch.object(
            opentag_setup.tag_dependencies, "activate_slack"
        ), patch.object(opentag_setup, "list_slack_sign_ins", return_value=SIGN_INS), Client(replies) as client:
            selection = opentag_setup.connect_slack_workspace()
        self.assertEqual(selection, ("T0HOVERENG", "T0HOVERENG", "E0HOVER", "Hover Sandbox", "E0HOVER", "W01MAYA", ""))
        org, *texts = client.questions()[1:]
        self.assertEqual((org["id"], org["option_ids"], org["workspaces"], org["organization"]),
                         ("org_workspace", ["manual"], [], {"id": "E0HOVER", "name": "Hover Sandbox"}))
        self.assertEqual([q["id"] for q in texts], ["org_workspace_id"] * 3)
        self.assertEqual([q["error"] for q in texts], [None, opentag_setup.ORG_ID_ERROR, opentag_setup.NO_WORKSPACE_ID_ERROR])

    def test_workspace_id_parsing(self):
        for value, expected in (("T0ABC123", ("T0ABC123", None)), (" app.slack.com/client/T0ABC123/C9 ", ("T0ABC123", None)),
                                ("E0ORG1", ("", opentag_setup.ORG_ID_ERROR)), ("hover-eng", ("", opentag_setup.NO_WORKSPACE_ID_ERROR))):
            with self.subTest(value=value):
                self.assertEqual(opentag_setup.parse_workspace_id(value), expected)

    def test_terminal_pause_at_workspace_raises_paused(self):
        with patch.object(opentag_setup.tag_dependencies, "ensure_slack", return_value=Path("/bin/slack")), patch.object(
            opentag_setup.tag_dependencies, "activate_slack"
        ), patch.object(opentag_setup, "list_slack_sign_ins", return_value=SIGN_INS), patch.object(
            opentag_setup.ui, "choose", return_value=3
        ), self.assertRaises(opentag_setup.ui.Paused):
            opentag_setup.connect_slack_workspace()


class ExistingAppTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.project = opentag_setup.slack_project(self.home)
        self.config = self.home / "config/settings.json"
        opentag_setup.settings.save_config(self.config, {"SLACK_TEAM_ID": "T0KLOVR"})
        used = {"A08TAGDEV1": {"name": "Tag Dev", "tag": "Dev", "team": "T0KLOVR", "workspace": "T0KLOVR"}}
        for item in (patch.object(opentag_setup, "other_tag_apps", return_value=used),
                     patch.object(opentag_setup, "slack_cli_apps", return_value={"A06STNDUP2": "Standup Helper"})):
            item.start()
            self.addCleanup(item.stop)

    def test_known_apps_mark_apps_other_tags_use(self):
        opentag_setup.settings.save_config(self.project / ".slack/apps.dev.json",
                                           {"T0KLOVR": {"team_id": "T0KLOVR", "app_id": "A05TAGSBX3"}})
        self.assertEqual(opentag_setup.known_apps(self.home, "T0KLOVR"), [
            {"id": "A05TAGSBX3", "name": "A05TAGSBX3", "source": "linked", "used_by": None},
            {"id": "A06STNDUP2", "name": "Standup Helper", "source": "cli", "used_by": None},
            {"id": "A08TAGDEV1", "name": "Tag Dev", "source": "tag", "used_by": "Dev"},
        ])

    def test_pasted_app_another_tag_uses_is_refused(self):
        with Client([answer("api.slack.com/apps/A08TAGDEV1"), answer("nothing"),
                     answer("https://api.slack.com/apps/A09NEW1/general")]) as client:
            self.assertEqual(opentag_setup.ask_app_id("T0KLOVR"), "A09NEW1")
        self.assertEqual([q["error"] for q in client.questions()], [
            None, "Your Tag “Dev” already uses this app. Pick another one.", "No App ID found. It starts with A."])

    def test_update_needs_consent_and_connect_comes_when_checks_pass(self):
        manifest = (opentag_setup.ROOT / "slack-app-manifest.yaml").read_text(encoding="utf-8")
        missing = manifest.replace("      - channels:join\n", "").replace("      - assistant:write\n", "")
        with patch.object(opentag_setup, "remote_app_settings", side_effect=[(True, missing), (True, manifest)]), patch.object(
            opentag_setup, "update_app_settings"
        ) as update, Client([answer("update"), answer("connect")]) as client:
            self.assertTrue(opentag_setup.check_existing_app(self.home, self.config, "T0KLOVR", "A1", enterprise=False))
        update.assert_called_once_with(self.home, "T0KLOVR", "A1", False)
        first, second = client.questions()
        self.assertEqual((first["id"], first["option_ids"]), ("app_checks", ["update", "manual", "check", "back"]))
        self.assertEqual(first["options"][:2], ["Update app", "I'll do it myself"])
        self.assertIn({"label": "Missing 2 permissions", "ok": False, "detail": "assistant:write, channels:join"},
                      first["checks"])
        self.assertIn({"label": "Socket Mode", "ok": True, "detail": None}, first["checks"])
        self.assertEqual(second["option_ids"], ["connect", "back"])
        self.assertTrue(all(row["ok"] for row in second["checks"]))

    def test_manual_prints_steps_and_checks_again_without_changing_the_app(self):
        manifest = (opentag_setup.ROOT / "slack-app-manifest.yaml").read_text(encoding="utf-8")
        missing = manifest.replace("      - app_home_opened\n", "")
        with patch.object(opentag_setup, "remote_app_settings", side_effect=[(True, missing)] * 2), patch.object(
            opentag_setup, "update_app_settings"
        ) as update, Client([answer("manual"), answer("back")]) as client:
            self.assertFalse(opentag_setup.check_existing_app(self.home, self.config, "T0KLOVR", "A1", enterprise=False))
        update.assert_not_called()
        self.assertIn("Add app_home_opened", " ".join(e.get("text", "") for e in client.events()))

    def test_existing_app_flow_links_checks_and_saves(self):
        with patch.object(opentag_setup, "link_app") as link, patch.object(
            opentag_setup, "check_existing_app", return_value=True
        ), Client([answer("A06STNDUP2")]) as client:
            self.assertEqual(opentag_setup.choose_existing_app(self.home, self.config), "A06STNDUP2")
        link.assert_called_once_with(self.home, self.config, "T0KLOVR", "A06STNDUP2")
        question = client.questions()[0]
        self.assertEqual((question["id"], question["prompt"]), ("existing_app", "Which app?"))
        self.assertEqual(question["option_ids"], ["A06STNDUP2", "other", "exit"])
        self.assertEqual([app["used_by"] for app in question["apps"]], [None, "Dev"])
        self.assertEqual(opentag_setup.settings.load_config(self.config)["SLACK_APP_ID"], "A06STNDUP2")

    def test_back_from_checks_undoes_only_the_local_link_setup_made(self):
        def link(home, config, team, app_id):
            opentag_setup.settings.save_config(self.project / ".slack/apps.dev.json",
                                               {"T0KLOVR": {"team_id": "T0KLOVR", "app_id": app_id}})
            opentag_setup.save_progress(config, linked_by_setup=app_id)
        with patch.object(opentag_setup, "link_app", side_effect=link), patch.object(
            opentag_setup, "check_existing_app", side_effect=[False, True]
        ), Client([answer("A06STNDUP2"), answer("other"), answer("A09NEW1")]):
            self.assertEqual(opentag_setup.choose_existing_app(self.home, self.config), "A09NEW1")
        self.assertEqual(json.loads((self.project / ".slack/apps.dev.json").read_text(encoding="utf-8"))["T0KLOVR"]["app_id"], "A09NEW1")


class ChannelStepTests(unittest.TestCase):
    CHANNELS = [opentag_setup.slack_channels.SlackChannel("C1", "general", False, True, 48),
                opentag_setup.slack_channels.SlackChannel("C2", "launch", False, False, 12),
                opentag_setup.slack_channels.SlackChannel("G3", "secret", True, False, 3)]

    def test_channels_may_be_skipped(self):
        with patch.object(opentag_setup.slack_channels, "list_channels", return_value=self.CHANNELS), \
                Client([answer([])]) as client:
            self.assertEqual(opentag_setup.slack_channels.setup_channels("xoxb-1", tag_name="Maya's Tag"), [])
        question = client.questions()[0]
        self.assertEqual((question["id"], question["kind"], question["prompt"]), ("channels", "multi", "Where should Maya's Tag start?"))
        self.assertEqual((question["options"], question["selected"], question["allow_empty"]),
                         (["#general", "#launch"], [0], True))
        self.assertEqual(question["channels"][0], {"id": "C1", "name": "general", "member": True, "private": False, "members": 48})

    def test_choosing_a_public_channel_joins_it(self):
        joined = {"channel": {"id": "C2", "is_member": True}}
        with patch.object(opentag_setup.slack_channels, "list_channels", return_value=self.CHANNELS), patch.object(
            opentag_setup.slack_channels, "slack_api_post", return_value=joined
        ) as post, Client([answer(["#general", "#launch"])]):
            chosen = opentag_setup.slack_channels.setup_channels("xoxb-1")
        self.assertEqual([(c.channel_id, c.is_member) for c in chosen], [("C1", True), ("C2", True)])
        post.assert_called_once_with("xoxb-1", "conversations.join", {"channel": "C2"})

    def test_zero_channels_are_valid_only_when_following_invitations(self):
        base = {key: "x" for key in opentag_setup.settings.REQUIRED}
        self.assertNotIn("SLACK_CHANNEL_IDS", opentag_setup.settings.config_errors(
            {**base, "SLACK_CHANNEL_POLICY": "invited", "SLACK_CHANNEL_IDS": ""}))
        self.assertIn("SLACK_CHANNEL_IDS", opentag_setup.settings.config_errors(
            {**base, "SLACK_CHANNEL_POLICY": "selected", "SLACK_CHANNEL_IDS": ""}))


class GuidedSetupFlowTests(unittest.TestCase):
    """A whole setup over JSON lines, with Slack and the AI replaced by fakes."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "instances/t1"
        self.config = self.home / "config/settings.json"
        self.created = []

        def fake_ai(config_path, values, *, review=False):
            if values.get("OPENTAG_DEFAULT_MODEL") and not review and not opentag_setup.ui.going_back_to("default_model"):
                return values
            opentag_setup.ui.choose("Default model", ["Codex · GPT-5.5"], qid="default_model", option_ids=["codex:gpt-5.5"])
            return opentag_setup.settings.update_config(config_path, {"OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5"})

        def fake_create(home, team_id, config_path, *, test_mode=False, progress=None):
            self.created.append(team_id)
            for step in ("create", "picture", "install"):
                progress(step)
            opentag_setup.settings.update_config(config_path, {"SLACK_APP_ID": "A0MAYA"})
            return "A0MAYA"

        def fake_credentials(home, config_path, team_id, app_id):
            return opentag_setup.settings.update_config(config_path, {"SLACK_APP_TOKEN": "xapp-1", "SLACK_BOT_TOKEN": "xoxb-1"})

        patches = [
            patch.dict(os.environ, {"TAG_HOME": str(self.root), "TAG_INSTANCE_HOME": str(self.home), "TAG_ID": "t1",
                                    "OPENTAG_WORKDIR": str(self.root / "work")}),
            patch.object(opentag_setup, "ensure_agent", side_effect=fake_ai),
            patch.object(opentag_setup.tag_ai, "setup_step", side_effect=lambda home, path: fake_ai(path, {}, review=True)),
            patch.object(opentag_setup, "other_tag_waterdrops", return_value=set()),
            patch.object(opentag_setup.getpass, "getuser", return_value="maya"),
            patch.object(opentag_setup, "slack_cli_too_old", return_value=False),
            patch.object(opentag_setup, "slack_cli_supports_icon_upload", return_value=True),
            patch.object(opentag_setup.tag_dependencies, "ensure_slack", return_value=Path("/bin/slack")),
            patch.object(opentag_setup.tag_dependencies, "activate_slack"),
            patch.object(opentag_setup, "list_slack_sign_ins", return_value=SIGN_INS),
            patch.object(opentag_setup, "choose_slack_app", side_effect=fake_create),
            patch.object(opentag_setup, "connect_app_credentials", side_effect=fake_credentials),
            patch.object(opentag_setup, "validate_slack_identity", return_value={}),
            patch.object(opentag_setup, "validate_socket_token"),
            patch.object(opentag_setup.slack_channels, "list_channels", return_value=ChannelStepTests.CHANNELS),
            patch.object(opentag_setup.slack_channels, "slack_api", return_value={"ok": True}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def run_setup(self, replies: list) -> Client:
        with Client(replies) as client:
            try:
                self.result = opentag_setup.guided_setup(self.config, start_services=False)
            except opentag_setup.ui.Paused:
                self.result = "paused"
        return client

    NEW_TAG = [answer({"name": "Maya's Tag", "description": "Helps with launches"}), answer("codex:gpt-5.5"),
               answer("T0KLOVR"), answer("create")]

    def test_new_tag_follows_the_v2_order(self):
        client = self.run_setup([*self.NEW_TAG, answer(["#general"])])
        self.assertEqual(self.result, 0)
        self.assertEqual([q["id"] for q in client.questions()],
                         ["profile", "default_model", "workspace", "approve_setup", "channels"])
        self.assertEqual([e["step"] for e in client.events() if e["type"] == "progress"],
                         ["create", "picture", "install", "connect"])
        progress = {e["step"]: e["text"] for e in client.events() if e["type"] == "progress"}
        self.assertEqual(progress["install"], "Install in Klovr")
        approve = client.questions()[3]
        self.assertEqual((approve["prompt"], approve["option_ids"]), ("Ready to create it in Klovr?", ["create", "edit", "edit_ai", "back"]))
        recap = approve["recap"]
        self.assertEqual((recap["name"], recap["description"], recap["owner"], recap["approval"]),
                         ("Maya's Tag", "Helps with launches", {"id": "U01MAYA", "name": "maya"}, False))
        self.assertEqual(recap["workspace"], {"id": "T0KLOVR", "name": "Klovr", "organization": None})
        self.assertEqual((recap["ai"]["value"], recap["ai"]["backend"]), ("codex:gpt-5.5", "codex"))
        self.assertTrue(Path(recap["picture"]).is_file())
        values = opentag_setup.settings.load_config(self.config)
        self.assertEqual((values["SLACK_ALLOWED_USER_IDS"], values["SLACK_CHANNEL_POLICY"], values["SLACK_CHANNEL_IDS"]),
                         ("U01MAYA", "invited", "C1"))
        self.assertTrue(Path(values["MFS_SLACK_CONNECTOR_CONFIG"]).is_file())
        self.assertIn("C1", values["MFS_ALLOWED_SCOPES"])

    def test_zero_channels_finish_with_memory_ready_for_invitations(self):
        client = self.run_setup([*self.NEW_TAG, answer([])])
        self.assertEqual(self.result, 0)
        values = opentag_setup.settings.load_config(self.config)
        self.assertEqual((values["SLACK_CHANNEL_IDS"], values["SLACK_CHANNEL_POLICY"]), ("", "invited"))
        self.assertEqual(values["MFS_SLACK_TOKEN"], "xoxb-1")
        self.assertNotIn("MFS_SLACK_CONNECTOR_CONFIG", values)
        self.assertEqual(opentag_setup.settings.config_errors(values), {})
        self.assertNotIn("ready", [q["id"] for q in client.questions()])

    def test_recap_edit_and_edit_ai_come_back_to_the_recap(self):
        replies = [*self.NEW_TAG[:3], answer("edit"), answer({"name": "Launch Tag", "description": ""}),
                   answer("edit_ai"), answer("codex:gpt-5.5"), answer("create"), answer([])]
        client = self.run_setup(replies)
        ids = [q["id"] for q in client.questions()]
        self.assertEqual(ids, ["profile", "default_model", "workspace", "approve_setup", "profile", "approve_setup",
                               "default_model", "approve_setup", "channels"])
        edited = client.questions()[4]
        self.assertEqual((edited["editing"], edited["can_use_existing"], edited["name"]), (True, False, "Maya's Tag"))
        self.assertEqual(client.questions()[5]["recap"]["name"], "Launch Tag")

    def test_back_option_on_the_recap_chooses_the_workspace_again(self):
        replies = [*self.NEW_TAG[:3], answer("back"), answer("E0HOVER"), answer("manual"), answer("T0HOVERENG"),
                   answer("create"), answer([])]
        client = self.run_setup(replies)
        self.assertEqual([q["id"] for q in client.questions()],
                         ["profile", "default_model", "workspace", "approve_setup", "workspace", "org_workspace",
                          "org_workspace_id", "approve_setup", "channels"])
        recap = client.questions()[7]["recap"]
        self.assertEqual(recap["workspace"]["organization"], {"id": "E0HOVER", "name": "Hover Sandbox"})
        self.assertTrue(recap["approval"])
        self.assertEqual(self.created, ["T0HOVERENG"])
        values = opentag_setup.settings.load_config(self.config)
        self.assertEqual((values["SLACK_ENTERPRISE_ID"], values["SLACK_ALLOWED_USER_IDS"]), ("E0HOVER", "W01MAYA"))

    def test_back_from_the_recap_replays_earlier_answers_and_asks_the_workspace(self):
        replies = [*self.NEW_TAG[:3], {"back": True}, answer("T0KLOVR"), answer("create"), answer([])]
        with Client(replies) as client:
            with self.assertRaises(opentag_setup.ui.GoBack) as raised:
                opentag_setup.guided_setup(self.config, start_services=False)
            self.assertEqual(raised.exception.target, ("workspace", "T0KLOVR"))
            values = opentag_setup.settings.load_config(self.config)
            opentag_setup.settings.save_config(self.config, {
                key: value for key, value in values.items() if key not in opentag_setup.BACK_CLEARS["workspace"]})
            opentag_setup.ui.start_replay(raised.exception.replay, raised.exception.target)
            self.assertEqual(opentag_setup.guided_setup(self.config, start_services=False), 0)
        ids = [q["id"] for q in client.questions()]
        # The name and model are replayed silently; the workspace comes back with its answer selected.
        self.assertEqual(ids, ["profile", "default_model", "workspace", "approve_setup", "workspace",
                               "approve_setup", "channels"])
        self.assertEqual(client.questions()[4]["default"], 0)
        self.assertEqual(opentag_setup.settings.load_config(self.config)["SLACK_ALLOWED_USER_IDS"], "U01MAYA")

    def test_owner_unknown_stops_without_guessing(self):
        unknown = [{**SIGN_INS[0], "user_id": None}]
        with patch.object(opentag_setup, "list_slack_sign_ins", return_value=unknown), patch.object(
                opentag_setup, "auth_test_member", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "Sign in to Slack again"):
                self.run_setup(self.NEW_TAG)
        self.assertNotIn("SLACK_TEAM_ID", opentag_setup.settings.load_config(self.config))

    def test_existing_app_path_skips_naming_and_creation(self):
        def choose(home, config_path):
            opentag_setup.ui.choose("Which app?", ["Standup Helper"], qid="existing_app", option_ids=["A06STNDUP2"])
            opentag_setup.settings.update_config(config_path, {"SLACK_APP_ID": "A06STNDUP2"})
            return "A06STNDUP2"
        with patch.object(opentag_setup, "choose_existing_app", side_effect=choose), patch.object(
                opentag_setup, "link_app") as link:
            client = self.run_setup([answer("existing"), answer("codex:gpt-5.5"), answer("T0KLOVR"),
                                     answer("A06STNDUP2"), answer([])])
        self.assertEqual(self.result, 0)
        self.assertEqual([q["id"] for q in client.questions()],
                         ["profile", "default_model", "workspace", "existing_app", "channels"])
        self.assertEqual(self.created, [])
        link.assert_called_once()

    def test_paused_before_slack_resumes_at_profile_with_saved_values(self):
        self.run_setup([answer({"name": "Maya's Tag", "description": "Helps"})])
        self.assertEqual(self.result, "paused")
        client = self.run_setup([])
        first = client.questions()[0]
        self.assertEqual((first["id"], first["name"], first["description"]), ("profile", "Maya's Tag", "Helps"))

    def test_paused_after_sign_in_resumes_where_it_left_off(self):
        self.run_setup(self.NEW_TAG[:3])
        self.assertEqual(self.result, "paused")
        client = self.run_setup([answer("create"), answer([])])
        self.assertEqual(self.result, 0)
        self.assertEqual([q["id"] for q in client.questions()], ["approve_setup", "channels"])

    def test_setup_paused_in_the_old_order_after_choosing_a_workspace(self):
        # Before v2: workspace first, then the app question. No name, picture, or owner yet.
        opentag_setup.settings.save_config(self.config, {"SLACK_TEAM_ID": "T0KLOVR", "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5"})
        client = self.run_setup([answer({"name": "Maya's Tag", "description": ""}), answer("create"), answer([])])
        self.assertEqual(self.result, 0)
        self.assertEqual([q["id"] for q in client.questions()], ["profile", "approve_setup", "channels"])
        self.assertEqual(opentag_setup.settings.load_config(self.config)["SLACK_ALLOWED_USER_IDS"], "U01MAYA")

    def test_setup_paused_in_the_old_order_during_app_creation(self):
        opentag_setup.settings.save_config(self.config, {"SLACK_TEAM_ID": "T0KLOVR", "OPENTAG_BOT_NAME": "Maya's Tag",
                                                         "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5"})
        project = opentag_setup.slack_project(self.home)
        opentag_setup.settings.save_config(project / "tag-create.json", {"team_id": "T0KLOVR", "status": "attempting"})
        client = self.run_setup([answer([])])
        self.assertEqual(self.result, 0)
        self.assertEqual([q["id"] for q in client.questions()], ["channels"])
        self.assertEqual(self.created, ["T0KLOVR"])

    def test_setup_paused_in_the_old_order_before_its_owner_and_channels(self):
        opentag_setup.settings.save_config(self.config, {
            "SLACK_TEAM_ID": "T0KLOVR", "SLACK_APP_ID": "A0MAYA", "OPENTAG_BOT_NAME": "Maya's Tag",
            "OPENTAG_DEFAULT_MODEL": "codex:gpt-5.5", "SLACK_APP_TOKEN": "xapp-1", "SLACK_BOT_TOKEN": "xoxb-1"})
        client = self.run_setup([answer(["#general"])])
        self.assertEqual(self.result, 0)
        self.assertEqual([q["id"] for q in client.questions()], ["channels"])
        self.assertEqual(opentag_setup.settings.load_config(self.config)["SLACK_ALLOWED_USER_IDS"], "U01MAYA")
        self.assertEqual(self.created, [])
