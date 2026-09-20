from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import record_output_artifact


class RecordOutputArtifactTests(unittest.TestCase):
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
                [str(artifact.resolve())],
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
