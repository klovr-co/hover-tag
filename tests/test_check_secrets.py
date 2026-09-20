from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_secrets import validate_secrets


class SecretCheckTests(unittest.TestCase):
    def test_tracked_deletion_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            deleted = root / "deleted.txt"
            deleted.write_text("safe content\n", encoding="utf-8")
            subprocess.run(["git", "add", "deleted.txt"], cwd=root, check=True)
            deleted.unlink()

            self.assertEqual([], validate_secrets(root))

    def test_directory_entries_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory) / "skill-directory"
            directory.mkdir()

            with patch("scripts.check_secrets.tracked_files", return_value=[directory]):
                self.assertEqual([], validate_secrets(directory.parent))


if __name__ == "__main__":
    unittest.main()
