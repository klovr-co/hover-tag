from __future__ import annotations

import stat
import json
import struct
import subprocess
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import opentag_setup
from scripts.opentag_setup import (
    ask_required,
    choose_backend,
    main,
    render_env,
    runtime_requirement,
    selected_backend_available,
    write_config,
)


class OpenTagSetupTests(unittest.TestCase):
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

    def test_app_menu_does_not_offer_saved_or_backup_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = opentag_setup.slack_project(home)
            opentag_setup.settings.save_config(project / "tag-kept-app.json", {
                "app_id": "AOLD", "team_id": "TTEST", "saved_bot_name": "OpenMax",
            })
            opentag_setup.settings.save_config(home / "config/backups/setup-old/settings.json", {
                "SLACK_APP_ID": "AOLD", "SLACK_TEAM_ID": "TTEST",
            })
            with patch.object(opentag_setup.ui, "choose", return_value=2) as choose, patch.object(
                opentag_setup.subprocess, "run"
            ) as remote, redirect_stdout(StringIO()), self.assertRaises(opentag_setup.ui.Paused):
                opentag_setup.choose_slack_app(home, "TTEST")
            self.assertEqual(choose.call_args.args[1], [
                "Create a new Tag app", "Use an existing app", "Save and exit",
            ])
            remote.assert_not_called()
            self.assertFalse((home / "config/settings.json").exists())

    def test_existing_app_prints_link_and_instructions_before_requesting_id(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            output = StringIO()
            def enter_id(*args):
                instructions = output.getvalue()
                self.assertIn("https://api.slack.com/apps", instructions)
                self.assertIn("workspace TTEST", instructions)
                self.assertIn("Basic Information → App Credentials → App ID", instructions)
                self.assertIn("not a token or Client ID", instructions)
                self.assertEqual(args, ("App ID", "SLACK_APP_ID"))
                return "ATEST"
            with patch.object(opentag_setup.ui, "choose", return_value=1), patch.object(
                opentag_setup, "ask_validated", side_effect=enter_id
            ), patch.object(opentag_setup, "saved_slack_app", return_value=True), patch.object(
                opentag_setup, "inspect_slack_app", return_value=True
            ) as inspect, patch.object(opentag_setup.webbrowser, "open") as browser, patch.object(
                opentag_setup.slack_app_create, "create_app"
            ) as create, redirect_stdout(output):
                self.assertEqual(opentag_setup.choose_slack_app(home, "TTEST"), "ATEST")
            browser.assert_not_called()
            create.assert_not_called()
            inspect.assert_called_once()
            self.assertEqual(opentag_setup.settings.load_config(home / "config/settings.json")["SLACK_APP_ID"], "ATEST")

    def test_existing_app_cancel_before_id_saves_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(opentag_setup.ui, "choose", return_value=1), patch.object(
                opentag_setup, "ask_validated", side_effect=KeyboardInterrupt
            ), patch.object(opentag_setup, "run_slack_cli") as link, redirect_stdout(StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    opentag_setup.choose_slack_app(home, "TTEST")
            link.assert_not_called()
            self.assertFalse((home / "config/settings.json").exists())

    def test_manual_app_link_still_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(opentag_setup.ui, "choose", side_effect=[1, 1]) as choose, patch.object(
                opentag_setup, "ask_validated", return_value="ATEST"
            ), patch.object(opentag_setup, "run_slack_cli") as link, redirect_stdout(StringIO()):
                with self.assertRaises(opentag_setup.ui.Paused):
                    opentag_setup.choose_slack_app(home, "TTEST")
            self.assertEqual(choose.call_args.args[0], "Continue with this app?")
            link.assert_not_called()
            self.assertEqual(opentag_setup.settings.load_config(home / "config/settings.json")["SLACK_APP_ID"], "ATEST")

    def test_create_app_remains_a_separate_explicit_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(opentag_setup.ui, "choose", return_value=0), patch.object(
                opentag_setup.slack_app_create, "create_app", return_value="ANEW"
            ) as create, patch.object(opentag_setup, "customize_new_app") as customize, patch.object(
                opentag_setup, "ask_validated"
            ) as manual, patch.object(
                opentag_setup, "saved_slack_app", return_value=True
            ), patch.object(opentag_setup, "inspect_slack_app", return_value=True), redirect_stdout(StringIO()):
                self.assertEqual(opentag_setup.choose_slack_app(home, "TTEST"), "ANEW")
            create.assert_called_once()
            customize.assert_called_once_with(
                home / "integrations/slack-cli", home / "config/settings.json", "TTEST"
            )
            manual.assert_not_called()

    def test_new_app_customization_saves_name_and_copies_dragged_icon(self):
        with tempfile.TemporaryDirectory(prefix="Tag icon ") as directory:
            home = Path(directory)
            project = opentag_setup.slack_project(home)
            config = home / "config/settings.json"
            source = home / "My profile.png"
            source.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                + struct.pack(">II", 512, 512) + b"\x08\x06\x00\x00\x00"
            )
            pasted_path = repr(str(source))
            with patch.object(opentag_setup, "ask", side_effect=["Helper", pasted_path]), patch.object(
                opentag_setup.ui, "choose", side_effect=[1, 0]
            ), patch.object(opentag_setup, "slack_cli_supports_icon_upload", return_value=True
            ), redirect_stdout(StringIO()):
                opentag_setup.customize_new_app(project, config)
            self.assertEqual(opentag_setup.settings.load_config(config)["OPENTAG_BOT_NAME"], "Helper")
            saved = project / "assets/tag-profile.png"
            self.assertEqual(saved.read_bytes(), source.read_bytes())

    def test_new_app_customization_reprompts_for_invalid_name_and_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = opentag_setup.slack_project(home)
            config = home / "settings.json"
            small = home / "small.gif"
            small.write_bytes(b"GIF89a" + struct.pack("<HH", 64, 64))
            valid = home / "valid.gif"
            valid.write_bytes(b"GIF89a" + struct.pack("<HH", 512, 700))
            with patch.object(
                opentag_setup, "ask",
                side_effect=["x" * 36, "Tag Two", str(small), str(valid)],
            ), patch.object(opentag_setup.ui, "choose", side_effect=[1, 0]), patch.object(
                opentag_setup, "slack_cli_supports_icon_upload", return_value=True
            ), redirect_stdout(StringIO()) as output:
                opentag_setup.customize_new_app(project, config)
            self.assertIn("1 to 35 characters", output.getvalue())
            self.assertIn("512×512", output.getvalue())
            self.assertTrue((project / "assets/tag-profile.gif").is_file())

    def test_default_choice_renders_a_deterministic_branded_waterdrop(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = opentag_setup.slack_project(home)
            assets = project / "assets"
            assets.mkdir()
            (assets / "tag-profile.png").write_bytes(b"old")
            (assets / "keep.png").write_bytes(b"keep")
            with patch.object(opentag_setup, "ask", return_value="Maxine's Tag"), patch.object(
                opentag_setup.ui, "choose", return_value=0
            ), patch.object(
                opentag_setup, "slack_cli_supports_icon_upload", return_value=True
            ), patch.object(
                opentag_setup, "suggested_assistant_name", return_value="Maxine's Tag"
            ), redirect_stdout(StringIO()):
                opentag_setup.customize_new_app(project, home / "settings.json", "TTEST")
            first = (assets / "tag-profile.png").read_bytes()
            self.assertEqual(opentag_setup.image_dimensions(assets / "tag-profile.png"), (512, 512))
            self.assertNotEqual(first, b"old")
            self.assertTrue((assets / "keep.png").exists())
            self.assertEqual(
                opentag_setup.branded_profile_icon(project, "TTEST:Maxine's Tag").read_bytes(), first
            )
            self.assertNotEqual(
                opentag_setup.branded_profile_icon(project, "TOTHER:Maxine's Tag").read_bytes(), first
            )

    def test_identity_review_can_preview_and_change_name_without_rerolling(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = opentag_setup.slack_project(home)
            config = home / "settings.json"
            with patch.object(
                opentag_setup, "ask", side_effect=["First Tag", "Final Tag"]
            ), patch.object(
                opentag_setup.ui, "choose", side_effect=[0, 3, 1, 0]
            ), patch.object(
                opentag_setup, "slack_cli_supports_icon_upload", return_value=True
            ), patch.object(
                opentag_setup.webbrowser, "open", return_value=True
            ) as browser, redirect_stdout(StringIO()) as output:
                opentag_setup.customize_new_app(project, config, "TTEST")
            self.assertEqual(
                opentag_setup.settings.load_config(config)["OPENTAG_BOT_NAME"], "Final Tag"
            )
            browser.assert_called_once_with((project / "assets/tag-profile.png").resolve().as_uri())
            assignments = json.loads(
                (project / "assets/tag-waterdrop-identities.json").read_text()
            )
            self.assertEqual(
                assignments["TTEST:First Tag"]["index"],
                assignments["TTEST:Final Tag"]["index"],
            )
            review = output.getvalue()
            self.assertIn("YOUR TAG", review)
            self.assertIn("Nothing is created in Slack until you continue.", review)

    def test_test_mode_makes_the_default_name_and_remote_warning_obvious(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = opentag_setup.slack_project(home)
            config = home / "settings.json"

            def accept_default(prompt, default=None):
                self.assertEqual((prompt, default), ("Assistant name", "TEST · Maxine's Tag"))
                return default

            with patch.object(
                opentag_setup, "suggested_assistant_name", return_value="Maxine's Tag"
            ), patch.object(
                opentag_setup, "ask", side_effect=accept_default
            ), patch.object(
                opentag_setup.ui, "choose", side_effect=[0, 0]
            ), patch.object(
                opentag_setup, "slack_cli_supports_icon_upload", return_value=True
            ), redirect_stdout(StringIO()) as output:
                opentag_setup.customize_new_app(project, config, "TTEST", test_mode=True)
            self.assertEqual(
                opentag_setup.settings.load_config(config)["OPENTAG_BOT_NAME"],
                "TEST · Maxine's Tag",
            )
            self.assertIn("Continuing creates a real Slack app", output.getvalue())

    def test_curated_waterdrop_catalog_has_144_bases_and_16_signatures(self):
        self.assertEqual(opentag_setup.WATERDROP_BASE_COUNT, 144)
        self.assertEqual(opentag_setup.WATERDROP_SIGNATURE_COUNT, 16)
        self.assertEqual(opentag_setup.WATERDROP_RECIPE_COUNT, 2304)
        expected = {
            0: ("aqua", "mist", "glass", "clean"),
            11: ("emerald", "mist", "glass", "clean"),
            12: ("aqua", "cream", "glass", "clean"),
            36: ("aqua", "mist", "pearl", "clean"),
            144: ("aqua", "mist", "glass", "rose-cheeks"),
            2303: ("emerald", "cream", "frost", "heart-mark"),
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

    def test_waterdrop_assignments_avoid_known_collisions_inside_a_workspace(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            opentag_setup.hashlib, "sha256"
        ) as digest:
            digest.return_value.digest.return_value = b"\x00" * 32
            project = Path(directory)
            first = opentag_setup._assigned_waterdrop_index(project, "TTEST:First")
            second = opentag_setup._assigned_waterdrop_index(project, "TTEST:Second")
            other_workspace = opentag_setup._assigned_waterdrop_index(project, "TOTHER:First")
            self.assertEqual((first, second, other_workspace), (0, 1, 0))
            self.assertEqual(
                opentag_setup._assigned_waterdrop_index(project, "TTEST:First"), first
            )

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

    def test_assistant_name_uses_the_authenticated_slack_user(self):
        payload = json.dumps({"ok": True, "team_id": "TTEST", "user": "maxine.tan", "user_id": "U1"})
        with patch.object(
            opentag_setup.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, payload, ""),
        ):
            self.assertEqual(opentag_setup.suggested_assistant_name("TTEST"), "Maxine's Tag")

    def test_assistant_name_falls_back_to_the_local_first_name(self):
        with patch.object(
            opentag_setup.subprocess, "run", side_effect=OSError("missing")
        ), patch.object(opentag_setup.getpass, "getuser", return_value="river_song"):
            self.assertEqual(opentag_setup.suggested_assistant_name("TTEST"), "River's Tag")

    def test_profile_upload_pauses_cleanly_for_an_old_slack_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = opentag_setup.slack_project(home)
            config = home / "settings.json"
            with patch.object(opentag_setup, "ask", return_value="Tag"), patch.object(
                opentag_setup.ui, "choose", return_value=0
            ), patch.object(
                opentag_setup, "slack_cli_supports_icon_upload", return_value=False
            ), redirect_stdout(StringIO()) as output, self.assertRaises(opentag_setup.ui.Paused):
                opentag_setup.customize_new_app(project, config)
            self.assertIn("Slack CLI 4.7 or newer", output.getvalue())
            self.assertEqual(opentag_setup.settings.load_config(config)["OPENTAG_BOT_NAME"], "Tag")

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


    def test_members_are_scoped_deduplicated_and_never_inferred_from_bot(self):
        listing = (
            "User ID: UORPHAN\n\x1b[32mTeam (Team ID: T123)\x1b[0m\n User ID: U111\n"
            "Team (Team ID: T123)\nUser ID: U111\n"
            "Team (Team ID: T123)\nUser ID: W222\n"
            "Other (Team ID: T456)\nUser ID: UOTHER\n"
            "Malformed (Team ID: unknown)\nUser ID: UUNKNOWN\n"
        )
        self.assertEqual(opentag_setup.authorized_members(listing, "T123"), ["U111", "W222"])
        self.assertEqual(opentag_setup.authorized_members(listing, "TNONE"), [])

    def test_allowed_users_preserves_saved_policy_without_cli(self):
        with patch.object(opentag_setup.subprocess, "run") as run:
            self.assertEqual(opentag_setup.choose_allowed_users("T123", "U111,U222"), "U111,U222")
        run.assert_not_called()

    def test_allowed_users_offers_cli_identity_without_manual_entry(self):
        result = subprocess.CompletedProcess([], 0, "", "Team (Team ID: T123)\nUser ID: U111\n")
        with patch.object(opentag_setup.shutil, "which", return_value="/bin/slack"), patch.object(
            opentag_setup.subprocess, "run", return_value=result
        ), patch.object(opentag_setup.ui, "choose", return_value=0) as choose, patch.object(
            opentag_setup, "ask_validated"
        ) as manual:
            self.assertEqual(opentag_setup.choose_allowed_users("T123"), "U111")
        manual.assert_not_called()
        self.assertIn("Use my Slack account (U111)", choose.call_args.args[1])

    def test_allowed_users_manual_and_pause_are_explicit_choices(self):
        result = subprocess.CompletedProcess([], 0, "Team (Team ID: T123)\nUser ID: U111\n", "")
        with patch.object(opentag_setup.shutil, "which", return_value="/bin/slack"), patch.object(
            opentag_setup.subprocess, "run", return_value=result
        ), patch.object(opentag_setup.ui, "choose", side_effect=[1, 2]), patch.object(
            opentag_setup, "ask_validated", return_value="U222"
        ) as manual:
            self.assertEqual(opentag_setup.choose_allowed_users("T123"), "U222")
            with self.assertRaises(opentag_setup.ui.Paused):
                opentag_setup.choose_allowed_users("T123")
            self.assertEqual(manual.call_count, 1)

    def test_allowed_users_falls_back_on_failed_or_unmatched_auth(self):
        for result in (
            subprocess.CompletedProcess([], 1, "Team (Team ID: T123)\nUser ID: U111\n", ""),
            subprocess.CompletedProcess([], 0, "Other (Team ID: T456)\nUser ID: UOTHER\n", ""),
            subprocess.TimeoutExpired("slack", 15),
            OSError("missing"),
        ):
            with self.subTest(result=result), patch.object(opentag_setup.shutil, "which", return_value="/bin/slack"), patch.object(
                opentag_setup.subprocess, "run", side_effect=[result]
            ), patch.object(opentag_setup, "ask_validated", return_value="U222"), redirect_stdout(StringIO()):
                self.assertEqual(opentag_setup.choose_allowed_users("T123"), "U222")

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
                path.write_text(json.dumps({"TTEST": {"app_id": "ATEST", "team_id": "TTEST"}}))
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
                }))
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
            path.write_text(original)
            (project / "tag-linked.json").write_text(json.dumps({"app_id": "ATEST", "team_id": "TTEST"}))
            config = home / "config/settings.json"
            opentag_setup.settings.save_config(config, {"SLACK_APP_ID": "ATEST"})
            with patch.object(opentag_setup, "run_slack_cli") as link, redirect_stdout(StringIO()):
                with self.assertRaisesRegex(RuntimeError, "already linked to another app"):
                    opentag_setup.choose_slack_app(home, "TTEST", config)
            link.assert_not_called()
            self.assertEqual(path.read_text(), original)

    def test_missing_home_event_explains_repair_without_opening_browser(self) -> None:
        manifest = (opentag_setup.ROOT / "slack-app-manifest.yaml").read_text().replace("      - app_home_opened\n", "")
        with patch.object(opentag_setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, manifest, "")), patch.object(
            opentag_setup.webbrowser, "open"
        ) as browser, redirect_stdout(StringIO()) as output:
            self.assertFalse(opentag_setup.inspect_slack_app(Path("."), "ATEST"))
        self.assertIn("Event Subscriptions → Subscribe to bot events", output.getvalue())
        self.assertIn("Add app_home_opened", output.getvalue())
        browser.assert_not_called()

    def test_missing_agent_view_omits_redundant_manual_guidance(self) -> None:
        manifest = (opentag_setup.ROOT / "slack-app-manifest.yaml").read_text().replace(
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
            self.assertEqual(json.loads(hooks.read_text()), {"hooks": {}})
            config = project / ".slack/config.json"
            self.assertEqual(json.loads(config.read_text())["manifest"]["source"], "remote")
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

    def test_workspace_picker_returns_selected_id_without_login(self) -> None:
        result = subprocess.CompletedProcess([], 0, "Example Team (Team ID: T123)\nSecond Team (Team ID: T456)\n", "")
        with patch.object(opentag_setup.shutil, "which", return_value="/bin/slack"), patch.object(
            opentag_setup.subprocess, "run", return_value=result
        ), patch.object(opentag_setup, "run_slack_cli") as login, patch(
            "builtins.input", side_effect=["T456", "9", "2"]
        ), redirect_stdout(StringIO()) as output:
            self.assertEqual(opentag_setup.connect_slack_cli(), "T456")
        login.assert_not_called()
        self.assertIn("2. Second Team", output.getvalue())
        self.assertNotIn("sandbox", output.getvalue().lower())

    def test_workspace_picker_refreshes_accounts_after_login(self) -> None:
        results = [subprocess.CompletedProcess([], 0, "", ""),
                   subprocess.CompletedProcess([], 0, "Example Team (Team ID: T123)\n", "")]
        with patch.object(opentag_setup.shutil, "which", return_value="/bin/slack"), patch.object(
            opentag_setup.subprocess, "run", side_effect=results
        ), patch.object(opentag_setup, "run_slack_cli", return_value=0) as login, patch(
            "builtins.input", side_effect=["1", "1"]
        ), redirect_stdout(StringIO()):
            self.assertEqual(opentag_setup.connect_slack_cli(), "T123")
        login.assert_called_once_with(["auth", "login"], interactive=True)

    def test_workspace_picker_can_exit_when_listing_fails(self) -> None:
        result = subprocess.CompletedProcess([], 1, "Example Team (Team ID: T123)\n", "error")
        with patch.object(opentag_setup.shutil, "which", return_value="/bin/slack"), patch.object(
            opentag_setup.subprocess, "run", return_value=result
        ), patch.object(opentag_setup, "run_slack_cli") as login, patch(
            "builtins.input", return_value="2"
        ), redirect_stdout(StringIO()):
            self.assertIsNone(opentag_setup.connect_slack_cli())
        login.assert_not_called()

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

    @patch("scripts.opentag_setup.shutil.which", side_effect=lambda command: f"/bin/{command}")
    @patch("builtins.input", return_value="")
    def test_backend_menu_defaults_to_codex(
        self, _mock_input: object, _mock_which: object
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            backend = choose_backend()

        self.assertEqual(backend, "codex")
        self.assertIn("Codex        Recommended and supported", output.getvalue())
        self.assertIn("Claude Code  Experimental", output.getvalue())

    @patch(
        "scripts.opentag_setup.shutil.which",
        side_effect=lambda command: "/bin/codex" if command == "codex" else None,
    )
    @patch("builtins.input", return_value="2")
    def test_backend_menu_can_select_experimental_claude(
        self, _mock_input: object, _mock_which: object
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            backend = choose_backend()

        self.assertEqual(backend, "claude")
        self.assertIn("Claude Code  Experimental", output.getvalue())
        self.assertIn("not found", output.getvalue())

    @patch("scripts.opentag_setup.shutil.which", return_value="/bin/claude")
    @patch("builtins.input", return_value="Claude Code")
    def test_backend_menu_accepts_agent_name(
        self, _mock_input: object, _mock_which: object
    ) -> None:
        with redirect_stdout(StringIO()):
            self.assertEqual(choose_backend(), "claude")

    @patch("scripts.opentag_setup.shutil.which", return_value="/bin/codex")
    def test_selected_backend_is_available(self, _mock_which: object) -> None:
        self.assertTrue(selected_backend_available("codex"))

    @patch("scripts.opentag_setup.shutil.which", return_value=None)
    def test_missing_selected_backend_has_actionable_guidance(
        self, _mock_which: object
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            available = selected_backend_available("claude")

        self.assertFalse(available)
        self.assertIn("Claude Code was selected", output.getvalue())
        self.assertIn("run ./tag setup again", output.getvalue())

    @patch("scripts.opentag_setup.shutil.which", return_value="/bin/codex")
    @patch("builtins.input", side_effect=["other", "codex"])
    def test_backend_menu_reprompts_after_invalid_choice(
        self, _mock_input: object, _mock_which: object
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            backend = choose_backend()

        self.assertEqual(backend, "codex")
        self.assertIn("Choose 1 for Codex or 2 for Claude Code.", output.getvalue())

    @patch("scripts.opentag_setup.ask_secret")
    @patch("scripts.opentag_setup.selected_backend_available", return_value=False)
    @patch("scripts.opentag_setup.choose_backend", return_value="claude")
    def test_setup_stops_before_secrets_when_backend_is_missing(
        self,
        _mock_choose: object,
        _mock_available: object,
        mock_ask_secret: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = Path(temporary_directory) / ".env"
            with patch("sys.argv", ["opentag_setup.py", "--config", str(config)]), patch(
                "sys.stdin.isatty", return_value=True
            ), patch.dict(os.environ, {"TAG_HOME": temporary_directory}):
                with redirect_stdout(StringIO()):
                    result = main()

        self.assertEqual(result, 1)
        mock_ask_secret.assert_not_called()

    def test_guided_setup_expands_and_validates_workspace_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home = root / "app/instances/default"
            user_home = root / "person"
            config = home / "config/settings.json"
            environment = {
                "HOME": str(user_home),
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
