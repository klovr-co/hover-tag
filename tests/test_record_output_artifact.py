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
