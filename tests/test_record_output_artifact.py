from __future__ import annotations

import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scripts import record_output_artifact


class RecordOutputArtifactTests(unittest.TestCase):
    def test_serializes_concurrent_manifest_updates(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            artifacts = [root / f"result-{index}.txt" for index in range(8)]
            for artifact in artifacts:
                artifact.write_text(artifact.name, encoding="utf-8")
            manifest = root / ".manifest.json"
            original_update = record_output_artifact._update_manifest

            def slow_update(path: Path, artifact: Path, *, attach: bool) -> None:
                time.sleep(0.01)
                original_update(path, artifact, attach=attach)

            with patch.object(record_output_artifact, "_update_manifest", slow_update):
                with ThreadPoolExecutor(max_workers=len(artifacts)) as executor:
                    futures = [
                        executor.submit(
                            record_output_artifact.record_artifact,
                            manifest,
                            artifact.resolve(),
                        )
                        for artifact in artifacts
                    ]
                    for future in futures:
                        future.result()

            self.assertCountEqual(
                [
                    {"path": str(artifact.resolve()), "attach": False}
                    for artifact in artifacts
                ],
                json.loads(manifest.read_text(encoding="utf-8")),
            )
            self.assertFalse(manifest.with_suffix(".json.lock").exists())

    def test_reports_busy_manifest_after_bounded_lock_retries(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            artifact = root / "result.txt"
            artifact.write_text("result", encoding="utf-8")
            manifest = root / ".manifest.json"
            manifest.with_suffix(".json.lock").touch()

            with patch.object(record_output_artifact, "LOCK_ATTEMPTS", 2), patch.object(
                record_output_artifact, "LOCK_RETRY_SECONDS", 0
            ):
                with self.assertRaisesRegex(ValueError, "manifest is busy"):
                    record_output_artifact.record_artifact(
                        manifest, artifact.resolve()
                    )

    def test_records_any_regular_file_and_deduplicates_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            artifact = root / "result.dat"
            artifact.write_bytes(b"\x00\x01")
            manifest = root / ".manifest.json"

            path = record_output_artifact.validated_output_path(Path("result.dat"), root)
            record_output_artifact.record_artifact(manifest, path)
            record_output_artifact.record_artifact(manifest, path)

            self.assertEqual(
                [{"path": str(artifact.resolve()), "attach": False}],
                json.loads(manifest.read_text(encoding="utf-8")),
            )

    def test_explicit_attachment_upgrades_existing_local_record(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            artifact = root / "result.csv"
            artifact.write_text("owner\nAda\n", encoding="utf-8")
            manifest = root / ".manifest.json"

            record_output_artifact.record_artifact(manifest, artifact.resolve())
            record_output_artifact.record_artifact(
                manifest, artifact.resolve(), attach=True
            )

            self.assertEqual(
                [{"path": str(artifact.resolve()), "attach": True}],
                json.loads(manifest.read_text(encoding="utf-8")),
            )

    def test_rejects_files_outside_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir, tempfile.TemporaryDirectory() as outside_dir:
            root = Path(raw_dir)
            artifact = Path(outside_dir) / "result.txt"
            artifact.write_text("private", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "inside the configured workspace"):
                record_output_artifact.validated_output_path(artifact, root)

    def test_rejects_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            with self.assertRaises(FileNotFoundError):
                record_output_artifact.validated_output_path(
                    Path("missing.txt"), Path(raw_dir)
                )


class ChannelArtifactTests(unittest.TestCase):
    def test_same_filename_in_two_channels_registers_and_opens_correct_file(self):
        import sys
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts import slack_socket_agent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for channel in ('C123', 'C456'):
                output_dir = record_output_artifact.channel_artifact_directory(root, channel, create=True)
                output = output_dir / 'report.md'
                output.write_text(channel)
                manifest = root / f'{channel}.json'
                with patch.object(sys, 'argv', ['record_output_artifact', '--manifest', str(manifest),
                    '--workdir', str(root), '--channel-id', channel, '--file', 'report.md']), redirect_stdout(StringIO()):
                    self.assertEqual(record_output_artifact.main(), 0)
                entries, errors = slack_socket_agent.load_output_artifact_entries(manifest, root)
                self.assertEqual(errors, [])
                self.assertEqual(entries, [(output, False)])
                self.assertEqual(slack_socket_agent.resolve_local_artifact(f'artifacts/{channel}/report.md', root), output)
                self.assertEqual(output.read_text(), channel)

    def test_directory_initialization_is_retryable_and_preserves_older_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'report.md'
            old.write_text('legacy')
            output = record_output_artifact.channel_artifact_directory(root, 'C123', create=True)
            current = output / 'report.md'
            current.write_text('current')
            self.assertEqual(record_output_artifact.channel_artifact_directory(root, 'C123', create=True), output)
            self.assertEqual(old.read_text(), 'legacy')
            self.assertEqual(current.read_text(), 'current')
            self.assertEqual(record_output_artifact.validated_output_path(old, root), old.resolve())

    def test_invalid_ids_and_symlinked_or_conflicting_directories_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for channel in ('../outside', 'C123/../../outside', '/C123', 'general', ''):
                with self.subTest(channel=channel), self.assertRaises(ValueError):
                    record_output_artifact.channel_artifact_directory(root, channel, create=True)
            artifacts = root / 'artifacts'
            artifacts.write_text('existing file')
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                record_output_artifact.channel_artifact_directory(root, 'C123', create=True)
            artifacts.unlink()
            outside = root / 'outside'
            outside.mkdir()
            artifacts.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                record_output_artifact.channel_artifact_directory(root, 'C123', create=True)
            self.assertFalse((outside / 'C123').exists())
