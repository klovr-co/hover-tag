from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.release_check import validate_release


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


if __name__ == "__main__":
    unittest.main()
