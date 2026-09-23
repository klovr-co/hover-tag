from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.tag_error_migrations import MIGRATION_VERSION, migrate


class ErrorReportMigrationTests(unittest.TestCase):
    def test_migration_is_idempotent_and_preserves_existing_reports(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            reports = home / "state/error-reports"
            reports.mkdir(parents=True)
            existing = reports / "ABC12345.json"
            existing.write_text("keep\n", encoding="utf-8")

            self.assertTrue(migrate(home))
            self.assertFalse(migrate(home))
            self.assertEqual("keep\n", existing.read_text(encoding="utf-8"))
            marker = json.loads((home / "state/migrations/tag-error-reporting.json").read_text())
            self.assertEqual(MIGRATION_VERSION, marker["version"])

    def test_failed_target_is_retryable_and_not_marked_complete(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            home = Path(raw_dir)
            target = home / "state/error-reports"
            target.parent.mkdir(parents=True)
            target.write_text("not a directory", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                migrate(home)
            self.assertFalse((home / "state/migrations/tag-error-reporting.json").exists())


if __name__ == "__main__":
    unittest.main()
