from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_docs import validate_docs
from scripts.check_manifest import validate_manifest


class ReleaseHelperTests(unittest.TestCase):
    def test_uv_uses_the_cross_platform_virtualenv_python(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/ci_check.sh").read_text(encoding="utf-8")
        uv_commands = " ".join(
            line.strip()
            for line in script.replace("\\\n", " ").splitlines()
            if line.strip().startswith("uv run")
        )

        self.assertNotIn(" python3 ", uv_commands)
        self.assertIn(" python ", uv_commands)

    def test_current_documentation_links_resolve(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validate_docs(root), [])

    def test_manifest_rejects_missing_required_features(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "slack-app-manifest.yaml").write_text(
                "oauth_config:\n  scopes:\n    bot: []\nsettings: {}\n", encoding="utf-8"
            )

            errors = validate_manifest(root)

        self.assertTrue(any("missing bot scopes" in error for error in errors))
        self.assertIn("Socket Mode must be enabled", errors)
        self.assertIn("app_mention must be subscribed", errors)
