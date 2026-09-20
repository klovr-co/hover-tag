from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.check_docs import validate_docs
from scripts.check_manifest import validate_manifest


class ReleaseHelperTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX source launcher")
    def test_source_launcher_does_not_fall_back_to_system_python(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkout = Path(temporary_directory)
            shutil.copy2(root / "tag", checkout / "tag")
            (checkout / "tag").chmod(0o755)
            (checkout / "scripts").mkdir()
            marker = checkout / "system-python-ran"
            (checkout / "scripts/tag_cli.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [str(checkout / "tag"), "status"], capture_output=True, text=True
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("source checkout is not prepared", result.stderr)
            self.assertIn("./install.sh --dependencies-only", result.stderr)
            self.assertFalse(marker.exists())

    def test_windows_source_launcher_has_no_system_python_fallback(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "tag.cmd").read_text(encoding="utf-8")
        self.assertNotIn('python "%~dp0scripts\\tag_cli.py"', script)
        self.assertIn("install.ps1 -DependenciesOnly", script)
        self.assertIn("exit /b 2", script)

    def test_windows_dependency_bootstrap_prepares_the_source_runtime(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "install.ps1").read_text(encoding="utf-8")

        self.assertIn("[switch]$DependenciesOnly", script)
        self.assertIn(".venv'", script)
        self.assertIn("Scripts/python.exe", script)
        self.assertIn("& python -m venv $runtime", script)
        self.assertIn("-m pip install -r", script)
        self.assertIn("requirements-runtime.txt", script)

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
        self.assertIn("app_home_opened must be subscribed", errors)
        self.assertIn("App Home must be enabled", errors)
