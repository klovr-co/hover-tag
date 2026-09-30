from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.release_check import validate_release, validate_stable_changelog


class ReleaseCheckTests(unittest.TestCase):
    def test_current_repository_satisfies_release_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validate_release(root), [])

    def test_missing_contract_files_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            errors = validate_release(Path(temporary_directory))

        self.assertIn("missing required file: VERSION", errors)
        self.assertIn("missing required file: LICENSE", errors)
        self.assertIn("missing required file: NOTICE", errors)

    def test_stable_release_requires_dated_changelog_with_changes(self) -> None:
        version = "0.3.0"
        self.assertTrue(validate_stable_changelog("## Unreleased\n- New feature\n", version))
        self.assertTrue(validate_stable_changelog("## [0.3.0] - 2026-10-01\n", version))
        self.assertEqual(validate_stable_changelog(
            "## [0.3.0] - 2026-10-01\n\n### Added\n\n- New feature\n", version
        ), [])

    def test_stable_release_rejects_existing_empty_changelog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "VERSION").write_text("0.3.0\n", encoding="utf-8")
            (root / "CHANGELOG.md").write_text("", encoding="utf-8")

            errors = validate_release(root)

        self.assertIn("CHANGELOG.md is missing a dated v0.3.0 stable release entry", errors)
        self.assertNotIn("missing required file: CHANGELOG.md", errors)


if __name__ == "__main__":
    unittest.main()
