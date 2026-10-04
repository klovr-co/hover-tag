from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import desktop_update_manifest as updates

ROOT = Path(__file__).resolve().parents[1]


def signatures(directory: Path, version: str, *suffixes: str) -> Path:
    for suffix in suffixes or ("macos.app.tar.gz", "windows-setup.exe", "linux-x86_64.AppImage"):
        (directory / f"Tag-{version}-{suffix}.sig").write_text(f"sig-{version}-{suffix}\n", encoding="utf-8")
    return directory


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)

    def test_each_platform_points_at_its_signed_release_asset(self) -> None:
        result = updates.manifest("0.3.0", signatures(self.dir, "0.3.0"), published="2026-10-02T00:00:00Z")
        self.assertEqual(result["version"], "0.3.0")
        self.assertEqual(set(result["platforms"]), set(updates.PLATFORMS))
        mac = result["platforms"]["darwin-aarch64"]
        self.assertEqual(mac, result["platforms"]["darwin-x86_64"])  # universal build
        self.assertEqual(mac["url"], "https://github.com/klovr-co/hover-tag/releases/download/v0.3.0/Tag-0.3.0-macos.app.tar.gz")
        self.assertEqual(mac["signature"], "sig-0.3.0-macos.app.tar.gz")

    def test_no_signed_updates_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            updates.manifest("0.3.0", self.dir)

    def test_releases_reach_their_own_and_less_stable_lines(self) -> None:
        self.assertEqual(updates.channels_for("0.3.0-alpha.2"), ["alpha"])
        self.assertEqual(updates.channels_for("0.3.0-beta.1"), ["beta", "alpha"])
        self.assertEqual(updates.channels_for("0.3.0"), ["stable", "beta", "alpha"])

    def test_manifests_only_move_forward(self) -> None:
        newer = {"version": "0.3.0-alpha.3", "platforms": {}}
        older = updates.manifest("0.3.0-alpha.2", signatures(self.dir, "0.3.0-alpha.2"))
        self.assertIsNone(updates.merge(newer, older))
        self.assertEqual(updates.merge(None, older), older)
        stable = updates.manifest("0.3.0", signatures(self.dir, "0.3.0"))
        self.assertEqual(updates.merge(newer, stable)["version"], "0.3.0")  # stable beats its alphas

    def test_a_platform_missing_from_a_release_is_never_offered_an_older_build(self) -> None:
        old = updates.manifest("0.3.0-alpha.1", signatures(self.dir, "0.3.0-alpha.1"))
        new = updates.manifest("0.3.0-alpha.2", signatures(self.dir, "0.3.0-alpha.2", "macos.app.tar.gz"))
        merged = updates.merge(old, new)
        self.assertEqual(set(merged["platforms"]), {"darwin-aarch64", "darwin-x86_64"})

    def test_a_rebuilt_platform_joins_the_same_version(self) -> None:
        mac = updates.manifest("0.3.0", signatures(self.dir, "0.3.0", "macos.app.tar.gz"))
        windows = updates.manifest("0.3.0", signatures(self.dir, "0.3.0", "windows-setup.exe"))
        self.assertIn("darwin-aarch64", updates.merge(mac, windows)["platforms"])

    def test_command_writes_only_the_lines_that_move(self) -> None:
        current, output = self.dir / "current", self.dir / "out"
        current.mkdir()
        (current / "tag-app-alpha.json").write_text(json.dumps({"version": "0.4.0-alpha.1", "platforms": {}}), encoding="utf-8")
        signatures(self.dir, "0.3.0")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/desktop_update_manifest.py"),
                                 "--version", "v0.3.0", "--signatures", str(self.dir),
                                 "--current", str(current), "--output", str(output)],
                                capture_output=True, text=True, check=True)
        self.assertEqual(sorted(p.name for p in output.iterdir()), ["tag-app-beta.json", "tag-app-stable.json"])
        self.assertIn("kept 0.4.0-alpha.1", result.stdout)


if __name__ == "__main__":
    unittest.main()
