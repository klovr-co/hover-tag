from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts import setup_ui, tag_cli, tag_telemetry as telemetry


class TagTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "Tag"
        self.configuration = patch.multiple(
            telemetry.build_config,
            POSTHOG_HOST="https://eu.example.invalid",
            POSTHOG_PROJECT_TOKEN="phc_public_fixture",
            PRIVACY_NOTICE_URL="https://example.invalid/privacy",
        )
        self.configuration.start()
        self.addCleanup(self.configuration.stop)

    def enable_without_event(self) -> None:
        with patch.object(telemetry, "telemetry_preference_enabled"):
            self.assertTrue(telemetry.enable(self.home))

    def queued_payloads(self) -> list[dict[str, object]]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(telemetry.queue_path(self.home).glob("*.json"))
        ]

    def test_hard_stop_creates_no_files_and_launches_no_worker(self) -> None:
        with patch.dict(os.environ, {"TAG_TELEMETRY": "off"}, clear=False), patch.object(
            telemetry.subprocess, "Popen"
        ) as process:
            self.assertFalse(telemetry.enable(self.home))
            telemetry.tui_started(self.home, "interactive")
            telemetry.flush(self.home)

        self.assertFalse(self.home.exists())
        process.assert_not_called()

    def test_opt_out_removes_identifier_and_queue_and_is_idempotent(self) -> None:
        self.enable_without_event()
        with patch.object(telemetry, "_launch_flush_worker"):
            telemetry.setup_started(self.home, "setup")
        self.assertTrue(telemetry.identifier_path(self.home).exists())
        self.assertTrue(telemetry.queue_path(self.home).exists())

        self.assertTrue(telemetry.disable(self.home))
        self.assertTrue(telemetry.disable(self.home))

        self.assertFalse(telemetry.identifier_path(self.home).exists())
        self.assertFalse(telemetry.queue_path(self.home).exists())
        self.assertIs(telemetry.saved_preference(self.home), False)

    def test_closed_event_methods_drop_unrecognized_values(self) -> None:
        self.enable_without_event()
        with patch.object(telemetry, "_launch_flush_worker"):
            telemetry.setup_step_completed(self.home, "secret-path", 14)
            telemetry.command_failed(self.home, "raw-command", "traceback goes here")
            telemetry.setup_step_completed(self.home, "slack", 14)

        payloads = self.queued_payloads()
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["event"], "setup_step_completed")
        self.assertEqual(payloads[0]["properties"], {
            "step": "slack", "elapsed_time": "5–30s",
        })

    def test_every_event_has_only_its_allow_listed_schema(self) -> None:
        self.enable_without_event()
        calls = (
            lambda: telemetry.tui_started(self.home, "interactive"),
            lambda: telemetry.setup_started(self.home, "setup"),
            lambda: telemetry.setup_step_completed(self.home, "app", 31),
            lambda: telemetry.setup_abandoned(self.home, "channels", 121),
            lambda: telemetry.setup_completed(self.home, 4, "codex"),
            lambda: telemetry.command_completed(self.home, "lifecycle", "succeeded", 5),
            lambda: telemetry.command_failed(self.home, "setup", "permission"),
            lambda: telemetry.telemetry_preference_enabled(self.home),
        )
        with patch.object(telemetry, "_launch_flush_worker"):
            for call in calls:
                call()

        schemas = {
            "tui_started": {"tag_version", "os_family", "cpu_architecture", "invocation_kind"},
            "setup_started": {"entry_point"},
            "setup_step_completed": {"step", "elapsed_time"},
            "setup_abandoned": {"last_step", "elapsed_time"},
            "setup_completed": {"elapsed_time", "backend_kind"},
            "command_completed": {"command_group", "outcome", "duration"},
            "command_failed": {"command_group", "error_category"},
            "telemetry_preference_changed": {"preference"},
        }
        payloads = self.queued_payloads()
        self.assertEqual({str(item["event"]) for item in payloads}, set(schemas))
        for payload in payloads:
            self.assertEqual(set(payload["properties"]), schemas[str(payload["event"])])
            serialized = json.dumps(payload)
            for secret in (
                "xoxb-secret", "/Users/person/private", "Slack message",
                "traceback goes here", "user@example.com",
            ):
                self.assertNotIn(secret, serialized)

    def test_flush_sends_privacy_flags_and_drops_failed_events(self) -> None:
        self.enable_without_event()
        with patch.object(telemetry, "_launch_flush_worker"):
            telemetry.command_failed(self.home, "lifecycle", "runtime")
            telemetry.command_completed(self.home, "lifecycle", "succeeded", 1)

        attempted = []

        def sender(payload: dict[str, object]) -> None:
            attempted.append(payload)
            if len(attempted) == 1:
                raise OSError("offline")

        telemetry.flush(self.home, sender=sender)

        self.assertEqual(len(attempted), 2)
        self.assertEqual(self.queued_payloads(), [])

    def test_posthog_request_contains_only_transport_fields_and_event_schema(self) -> None:
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        payload = {
            "event": "command_failed",
            "distinct_id": "769011d7-48b7-4689-a72d-24a5262724cc",
            "properties": {
                "command_group": "lifecycle",
                "error_category": "runtime",
            },
        }
        with patch.object(
            telemetry.urllib.request, "urlopen", return_value=Response()
        ) as request:
            telemetry._post(payload)

        outbound = request.call_args.args[0]
        body = json.loads(outbound.data)
        self.assertEqual(set(body), {"api_key", "event", "properties"})
        self.assertEqual(body["event"], "command_failed")
        self.assertEqual(set(body["properties"]), {
            "distinct_id", "$process_person_profile", "$geoip_disable",
            "command_group", "error_category",
        })
        self.assertIs(body["properties"]["$process_person_profile"], False)
        self.assertIs(body["properties"]["$geoip_disable"], True)
        self.assertNotIn("traceback", json.dumps(body).lower())

    def test_queue_is_bounded_and_expired_items_are_removed(self) -> None:
        self.enable_without_event()
        with patch.object(telemetry, "QUEUE_LIMIT", 3), patch.object(
            telemetry, "_launch_flush_worker"
        ):
            for _ in range(5):
                telemetry.setup_started(self.home, "setup")
        self.assertEqual(len(self.queued_payloads()), 3)

        first = next(telemetry.queue_path(self.home).glob("*.json"))
        payload = json.loads(first.read_text(encoding="utf-8"))
        now = time.time()
        payload["created_at"] = now - telemetry.QUEUE_TTL_SECONDS - 1
        first.write_text(json.dumps(payload), encoding="utf-8")
        telemetry._prune(telemetry.queue_path(self.home), now=now)
        self.assertEqual(len(self.queued_payloads()), 2)

    def test_unconfigured_release_never_queues_or_launches_network_worker(self) -> None:
        with patch.object(telemetry.build_config, "POSTHOG_HOST", ""), patch.object(
            telemetry.build_config, "POSTHOG_PROJECT_TOKEN", ""
        ), patch.object(telemetry, "_launch_flush_worker") as worker:
            self.assertFalse(telemetry.enable(self.home))
            telemetry.setup_started(self.home, "setup")

        self.assertFalse(self.home.exists())
        self.assertFalse(telemetry.queue_path(self.home).exists())
        worker.assert_not_called()

    def test_first_run_notice_persists_one_installation_wide_choice(self) -> None:
        class TerminalOutput(StringIO):
            def isatty(self) -> bool:
                return True

        output = TerminalOutput()
        with patch.object(os.sys.stdin, "isatty", return_value=True), redirect_stdout(
            output
        ), patch.object(setup_ui, "choose", return_value=0) as choose, patch.object(
            telemetry, "_launch_flush_worker"
        ):
            tag_cli._offer_first_run_telemetry(self.home)
            tag_cli._offer_first_run_telemetry(self.home)

        self.assertIs(telemetry.saved_preference(self.home), True)
        self.assertTrue(telemetry.identifier_path(self.home).is_file())
        choose.assert_called_once()
        self.assertIn("Turn telemetry off", choose.call_args.args[1])
        self.assertIn("Privacy notice:", output.getvalue())

    def test_noninteractive_first_run_does_not_create_installation_state(self) -> None:
        with patch.object(os.sys.stdin, "isatty", return_value=False), patch.object(
            setup_ui, "choose"
        ) as choose:
            tag_cli._offer_first_run_telemetry(self.home)

        self.assertFalse(self.home.exists())
        choose.assert_not_called()

    def test_interrupted_notice_leaves_no_preference_and_is_retryable(self) -> None:
        class TerminalOutput(StringIO):
            def isatty(self) -> bool:
                return True

        with patch.object(os.sys.stdin, "isatty", return_value=True), redirect_stdout(
            TerminalOutput()
        ), patch.object(setup_ui, "choose", side_effect=setup_ui.Paused()):
            tag_cli._offer_first_run_telemetry(self.home)

        self.assertFalse(self.home.exists())
        self.assertIsNone(telemetry.saved_preference(self.home))

    def test_setup_session_emits_only_closed_stage_values(self) -> None:
        with patch.object(telemetry, "setup_started") as started, patch.object(
            telemetry, "setup_step_completed"
        ) as step, patch.object(telemetry, "setup_completed") as completed:
            session = telemetry.SetupSession(self.home, "not-a-real-entry")
            session.start()
            session.enter("slack")
            session.enter("private/path")
            session.complete("codex")

        started.assert_called_once_with(self.home, "setup")
        self.assertEqual([call.args[1] for call in step.call_args_list], ["notice", "slack"])
        self.assertEqual(completed.call_args.args[2], "codex")

    def test_cli_controls_are_installation_wide_and_opt_out_sends_no_event(self) -> None:
        environment = dict(os.environ, TAG_HOME=str(self.home))
        with patch.dict(os.environ, environment, clear=True), patch.object(
            os.sys, "argv", ["tag", "telemetry", "on"]
        ), patch.object(telemetry, "_launch_flush_worker"), redirect_stdout(StringIO()):
            self.assertEqual(tag_cli.main(), 0)
        self.assertIs(telemetry.saved_preference(self.home), True)

        with patch.dict(os.environ, environment, clear=True), patch.object(
            os.sys, "argv", ["tag", "telemetry", "off"]
        ), patch.object(
            telemetry, "telemetry_preference_enabled"
        ) as preference_event, redirect_stdout(StringIO()):
            self.assertEqual(tag_cli.main(), 0)

        self.assertIs(telemetry.saved_preference(self.home), False)
        self.assertFalse(telemetry.identifier_path(self.home).exists())
        preference_event.assert_not_called()

    def test_help_system_exit_is_recorded_as_success(self) -> None:
        with patch.object(os.sys, "argv", ["tag", "--help"]), patch.object(
            telemetry, "saved_preference", return_value=True
        ), patch.object(telemetry, "hard_disabled", return_value=False), patch.object(
            tag_cli, "_run_cli", side_effect=SystemExit(0)
        ), patch.object(telemetry, "command_failed") as failed, patch.object(
            telemetry, "command_completed"
        ) as completed, patch.object(telemetry, "tui_started"):
            with self.assertRaises(SystemExit) as exit_status:
                tag_cli.main()

        self.assertEqual(exit_status.exception.code, 0)
        failed.assert_not_called()
        self.assertEqual(completed.call_args.args[2], "succeeded")

    def test_argparse_system_exit_is_recorded_as_validation_failure(self) -> None:
        with patch.object(os.sys, "argv", ["tag", "unknown"]), patch.object(
            tag_cli, "_offer_first_run_telemetry"
        ), patch.object(telemetry, "saved_preference", return_value=True), patch.object(
            telemetry, "hard_disabled", return_value=False
        ), patch.object(tag_cli, "_run_cli", side_effect=SystemExit(2)), patch.object(
            telemetry, "command_failed"
        ) as failed, patch.object(telemetry, "command_completed") as completed, patch.object(
            telemetry, "tui_started"
        ):
            with self.assertRaises(SystemExit) as exit_status:
                tag_cli.main()

        self.assertEqual(exit_status.exception.code, 2)
        self.assertEqual(failed.call_args.args[2], "validation")
        self.assertEqual(completed.call_args.args[2], "failed")

if __name__ == "__main__":
    unittest.main()
