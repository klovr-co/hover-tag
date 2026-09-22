from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import tag_instances
from scripts.tag_install import (
    ADMIN_SKILL,
    API_RELEASES,
    CHANNEL_INDEX_URL,
    FetchedRelease,
    LEGACY_ADMIN_SKILL,
    MFS_CLI_RELEASES,
    ReleaseSelection,
    _default_channel,
    command_owner,
    download,
    fetch_release,
    install,
    install_mfs_cli,
    resolve_channel,
    resolve_version,
    unpack_release,
)
from scripts.tag_paths import (
    codex_workspace_args,
    initialize,
    initialize_instance,
    initialize_workspace,
    runtime_environment,
    tag_home,
    tag_temp_dir,
)
from scripts.tag_cli import (
    process_for,
    read_config,
    start_process,
    stop_process,
    upgrade_command,
    upgrade_reminder,
)
from scripts.tag_migrate import legacy_config, migrate
from scripts.opentag_setup import render_env

ROOT = Path(__file__).resolve().parents[1]


def release_record(version: str, *, prerelease: bool, names: list[str] | None = None) -> dict:
    """Build a minimal GitHub release payload for installer resolution tests."""
    names = names or []
    return {
        "tag_name": "v" + version,
        "draft": False,
        "prerelease": prerelease,
        "assets": [
            {"name": name, "browser_download_url": "https://downloads.example/" + name}
            for name in names
        ],
    }


class MfsClientBundleTests(unittest.TestCase):
    def test_installs_pinned_client_beside_managed_python(self) -> None:
        payload = b"mfs-client-fixture"
        archive_file = io.BytesIO()
        with tarfile.open(fileobj=archive_file, mode="w:xz") as bundle:
            info = tarfile.TarInfo("mfs-cli/mfs")
            info.size = len(payload)
            info.mode = 0o755
            bundle.addfile(info, io.BytesIO(payload))
        archive = archive_file.getvalue()
        digest = hashlib.sha256(archive).hexdigest()

        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary) / "release"
            python = release / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            (release / "requirements-runtime.txt").write_text(
                "mfs-server[slack]==0.4.6\n", encoding="utf-8"
            )
            with patch.dict(
                MFS_CLI_RELEASES,
                {("darwin", "arm64"): ("fixture.tar.xz", digest)},
                clear=True,
            ), patch("scripts.tag_install.sys.platform", "darwin"), patch(
                "scripts.tag_install.platform.machine", return_value="arm64"
            ), patch("scripts.tag_install.download", return_value=archive) as fetched:
                installed = install_mfs_cli(release, python)

            self.assertEqual(installed, release / ".venv/bin/mfs")
            self.assertEqual(installed.read_bytes(), payload)
            self.assertTrue(installed.stat().st_mode & 0o100)
            fetched.assert_called_once_with(
                "https://github.com/zilliztech/mfs/releases/download/v0.4.6/fixture.tar.xz"
            )


class ReleaseResolutionTests(unittest.TestCase):
    def test_repository_policy_defaults_bare_installs_to_stable(self) -> None:
        """Bare installs follow the repository's stable-channel policy."""
        self.assertEqual(_default_channel(), "stable")

    def test_channels_select_the_newest_release_in_each_phase(self) -> None:
        releases = [
            release_record("1.0.0", prerelease=False),
            release_record("1.1.0-alpha.2", prerelease=True),
            release_record("1.1.0-beta.1", prerelease=True),
            {**release_record("9.0.0", prerelease=False), "draft": True},
        ]
        with patch(
            "scripts.tag_install._resolve_channel_from_index",
            side_effect=RuntimeError("channel index unavailable"),
        ), patch("scripts.tag_install._json_download", return_value=releases):
            self.assertEqual(resolve_channel("stable")["tag_name"], "v1.0.0")
            self.assertEqual(resolve_channel("beta")["tag_name"], "v1.1.0-beta.1")
            self.assertEqual(resolve_channel("alpha")["tag_name"], "v1.1.0-alpha.2")

    def test_channels_prefer_public_index_without_calling_github_api(self) -> None:
        sha = "d" * 40
        index = {
            "schema_version": 1,
            "repository": "klovr-co/hover-tag",
            "channels": {
                "alpha": {
                    "version": "1.2.0-alpha.3",
                    "tag": "v1.2.0-alpha.3",
                    "commit_sha": sha,
                },
            },
        }
        with patch("scripts.tag_install._json_download", return_value=index) as request:
            release = resolve_channel("alpha")

        request.assert_called_once_with(CHANNEL_INDEX_URL, timeout=120)
        self.assertEqual(release["tag_name"], "v1.2.0-alpha.3")
        self.assertEqual(release["target_commitish"], sha)
        self.assertEqual(
            {asset["name"] for asset in release["assets"]},
            {"tag-1.2.0-alpha.3.zip", "SHA256SUMS", "BUILD-PROVENANCE.json"},
        )
        self.assertTrue(all(
            "/releases/download/v1.2.0-alpha.3/" in asset["browser_download_url"]
            for asset in release["assets"]
        ))

    def test_channel_rejects_cross_phase_index_pointer(self) -> None:
        index = {
            "schema_version": 1,
            "repository": "klovr-co/hover-tag",
            "channels": {
                "alpha": {
                    "version": "1.2.0-beta.3",
                    "tag": "v1.2.0-beta.3",
                    "commit_sha": "b" * 40,
                },
            },
        }
        releases = [
            release_record("1.2.0-alpha.4", prerelease=True),
            release_record("1.2.0-beta.3", prerelease=True),
        ]
        with patch(
            "scripts.tag_install._json_download", side_effect=[index, releases]
        ):
            release = resolve_channel("alpha")

        self.assertEqual(release["tag_name"], "v1.2.0-alpha.4")

    def test_channel_index_avoids_an_exhausted_api_quota(self) -> None:
        index = {
            "schema_version": 1,
            "repository": "klovr-co/hover-tag",
            "channels": {
                "alpha": {
                    "version": "1.2.0-alpha.4",
                    "tag": "v1.2.0-alpha.4",
                    "commit_sha": "a" * 40,
                },
            },
        }

        def response(url: str, *, timeout: float = 120):
            if url == CHANNEL_INDEX_URL:
                return index
            raise RuntimeError("GitHub API rate limit exceeded; try again later")

        with patch("scripts.tag_install._json_download", side_effect=response) as request:
            self.assertEqual(resolve_channel("alpha")["tag_name"], "v1.2.0-alpha.4")

        self.assertEqual(request.call_count, 1)

    def test_valid_index_does_not_fall_back_when_channel_has_no_release(self) -> None:
        index = {
            "schema_version": 1,
            "repository": "klovr-co/hover-tag",
            "channels": {},
        }
        with patch("scripts.tag_install._json_download", return_value=index), patch(
            "scripts.tag_install._resolve_channel_from_api"
        ) as api:
            with self.assertRaisesRegex(RuntimeError, "No published releases"):
                resolve_channel("stable")

        api.assert_not_called()

    def test_channel_index_transport_failures_fall_back_to_api(self) -> None:
        expected = release_record("1.0.0", prerelease=False)
        for error in (urllib.error.URLError("offline"), TimeoutError("timed out")):
            with self.subTest(error=type(error).__name__), patch(
                "scripts.tag_install._resolve_channel_from_index", side_effect=error
            ), patch(
                "scripts.tag_install._resolve_channel_from_api", return_value=expected
            ) as api, contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(resolve_channel("stable"), expected)

            api.assert_called_once_with("stable", timeout=120, page_limit=100)
            self.assertIn("Channel index unavailable:", stderr.getvalue())
            self.assertIn("falling back to the GitHub Releases API", stderr.getvalue())

    def test_channel_resolution_paginates_and_handles_no_stable_release(self) -> None:
        first_page = [
            {**release_record(f"0.0.{index}-alpha.1", prerelease=True), "draft": True}
            for index in range(100)
        ]
        stable = release_record("1.0.0", prerelease=False)
        with patch(
            "scripts.tag_install._resolve_channel_from_index",
            side_effect=RuntimeError("channel index unavailable"),
        ), patch("scripts.tag_install._json_download", side_effect=[first_page, [stable]]) as request:
            self.assertEqual(resolve_channel("stable")["tag_name"], "v1.0.0")
            self.assertEqual(request.call_count, 2)
        with patch(
            "scripts.tag_install._resolve_channel_from_index",
            side_effect=RuntimeError("channel index unavailable"),
        ), patch(
            "scripts.tag_install._json_download",
            return_value=[release_record("1.1.0-alpha.1", prerelease=True)],
        ):
            with self.assertRaisesRegex(RuntimeError, "No published releases"):
                resolve_channel("stable")

    def test_exact_version_rejects_draft_and_release_type_mismatch(self) -> None:
        beta = release_record("1.0.0-beta.1", prerelease=True)
        with patch("scripts.tag_install._json_download", return_value=beta):
            resolved, version, channel = resolve_version("v1.0.0-beta.1")
        self.assertEqual(resolved, beta)
        self.assertEqual((version, channel), ("1.0.0-beta.1", "beta"))

        with patch(
            "scripts.tag_install._json_download",
            return_value={**release_record("1.0.0", prerelease=False), "draft": True},
        ):
            with self.assertRaisesRegex(ValueError, "still a draft"):
                resolve_version("1.0.0")
        with patch(
            "scripts.tag_install._json_download",
            return_value=release_record("1.0.0-beta.1", prerelease=False),
        ):
            with self.assertRaisesRegex(ValueError, "type does not match"):
                resolve_version("1.0.0-beta.1")

    def test_edge_requires_the_published_moving_prerelease(self) -> None:
        edge = {"tag_name": "edge", "draft": False, "prerelease": True, "assets": []}
        with patch(
            "scripts.tag_install._resolve_channel_from_index",
            side_effect=RuntimeError("channel index unavailable"),
        ), patch("scripts.tag_install._json_download", return_value=edge):
            self.assertEqual(resolve_channel("edge"), edge)
        with patch(
            "scripts.tag_install._resolve_channel_from_index",
            side_effect=RuntimeError("channel index unavailable"),
        ), patch(
            "scripts.tag_install._json_download",
            return_value={**edge, "prerelease": False},
        ):
            with self.assertRaisesRegex(ValueError, "not a published prerelease"):
                resolve_channel("edge")

    def test_fetch_rejects_missing_assets(self) -> None:
        release = release_record("1.0.0", prerelease=False, names=["tag-1.0.0.zip"])
        with tempfile.TemporaryDirectory() as temporary_directory, patch(
            "scripts.tag_install.resolve_channel", return_value=release
        ):
            with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
                fetch_release(None, Path(temporary_directory), "stable")

    def test_rate_limit_failure_is_actionable(self) -> None:
        error = urllib.error.HTTPError(
            API_RELEASES, 403, "rate limited", {"X-RateLimit-Remaining": "0"}, None
        )
        with patch("scripts.tag_install.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "rate limit exceeded"):
                download(API_RELEASES)

    def test_fetch_verifies_checksum_provenance_and_archive(self) -> None:
        version = "1.2.0-alpha.1"
        sha = "d" * 40
        names = [f"tag-{version}.zip", "SHA256SUMS", "BUILD-PROVENANCE.json"]
        release = release_record(version, prerelease=True, names=names)
        release["target_commitish"] = sha
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            archive_path = destination / "fixture.zip"
            with zipfile.ZipFile(archive_path, "w") as bundle:
                bundle.writestr("VERSION", version + "\n")
            archive = archive_path.read_bytes()
            digest = hashlib.sha256(archive).hexdigest()
            assets = {
                "https://downloads.example/SHA256SUMS": f"{digest}  tag-{version}.zip\n".encode(),
                "https://downloads.example/BUILD-PROVENANCE.json": json.dumps({
                    "schema_version": 1,
                    "channel": "release",
                    "version": version,
                    "commit_sha": sha,
                    "source_ref": "refs/heads/main",
                    "built_at": "2026-09-21T00:00:00Z",
                    "archive": {"name": f"tag-{version}.zip", "sha256": digest},
                }).encode(),
                f"https://downloads.example/tag-{version}.zip": archive,
            }
            with patch("scripts.tag_install.resolve_channel", return_value=release), patch(
                "scripts.tag_install.download", side_effect=lambda url: assets[url]
            ):
                fetched = fetch_release(None, destination / "download", "alpha")

            self.assertIsInstance(fetched, FetchedRelease)
            self.assertEqual(
                fetched.selection,
                ReleaseSelection("alpha", version, sha),
            )
            self.assertEqual((fetched.source / "VERSION").read_text().strip(), version)

            with patch(
                "scripts.tag_install.resolve_version",
                return_value=(release, version, "alpha"),
            ), patch(
                "scripts.tag_install.download", side_effect=lambda url: assets[url]
            ):
                pinned = fetch_release(version, destination / "pinned")
            self.assertEqual(
                pinned.selection,
                ReleaseSelection("alpha", version, sha, "version"),
            )

            release["target_commitish"] = "e" * 40
            with patch("scripts.tag_install.resolve_channel", return_value=release), patch(
                "scripts.tag_install.download", side_effect=lambda url: assets[url]
            ):
                with self.assertRaisesRegex(ValueError, "GitHub release target"):
                    fetch_release(None, destination / "wrong-target", "alpha")
            release["target_commitish"] = sha

            valid_checksums = assets["https://downloads.example/SHA256SUMS"]
            assets["https://downloads.example/SHA256SUMS"] = ("0" * 64 + f"  tag-{version}.zip\n").encode()
            with patch("scripts.tag_install.resolve_channel", return_value=release), patch(
                "scripts.tag_install.download", side_effect=lambda url: assets[url]
            ):
                with self.assertRaisesRegex(ValueError, "checksum verification failed"):
                    fetch_release(None, destination / "bad-checksum", "alpha")
            assets["https://downloads.example/SHA256SUMS"] = valid_checksums

            bad_provenance = json.loads(assets["https://downloads.example/BUILD-PROVENANCE.json"])
            bad_provenance["commit_sha"] = "not-a-sha"
            assets["https://downloads.example/BUILD-PROVENANCE.json"] = json.dumps(
                bad_provenance
            ).encode()
            with patch("scripts.tag_install.resolve_channel", return_value=release), patch(
                "scripts.tag_install.download", side_effect=lambda url: assets[url]
            ):
                with self.assertRaisesRegex(ValueError, "invalid commit SHA"):
                    fetch_release(None, destination / "invalid", "alpha")


class TagHomeTests(unittest.TestCase):
    def test_runtime_environment_places_slack_session_journal_in_tag_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"

            environment = runtime_environment(home)

        self.assertEqual(
            str(home / "state/slack-active-sessions.json"),
            environment["OPENTAG_SLACK_SESSIONS_FILE"],
        )

    def test_migration_preserves_originals_and_existing_skills(self):
        with tempfile.TemporaryDirectory() as temp:
            home, source = Path(temp) / "home", Path(temp) / "old checkout"
            initialize_instance(home)
            skill = source / ".codex/skills/custom/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("custom")
            source_codex_config = source / ".codex/config.toml"
            source_codex_config.write_text('model_reasoning_effort = "high"\n')
            old = source / ".env"
            old.write_text(render_env({"OPENTAG_WORKDIR": str(source), "SLACK_BOT_TOKEN": "quote'and\nnewline"}))
            self.assertEqual(legacy_config(old)["SLACK_BOT_TOKEN"], "quote'and\nnewline")
            migrate(source, home)
            self.assertEqual(json.loads((home / "config/settings.json").read_text())["OPENTAG_WORKDIR"], str(home / "workspace"))
            copied = home / "workspace/.agents/skills/custom/SKILL.md"
            self.assertEqual(copied.read_text(), "custom")
            self.assertEqual(
                (home / "workspace/.codex/config.toml").read_text(),
                'model_reasoning_effort = "high"\n',
            )
            copied.write_text("edited")
            migrate(source, home)
            self.assertEqual(copied.read_text(), "edited")
            self.assertEqual(skill.read_text(), "custom")
            self.assertTrue(old.exists())

    def test_existing_command_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = root / ("tag.cmd" if os.name == "nt" else "tag")
            command.write_text("another program")
            with self.assertRaises(RuntimeError):
                install(ROOT, root / "home", root, dependencies=False)
            self.assertEqual(command.read_text(), "another program")
            self.assertFalse((root / "home").exists())

    @unittest.skipIf(os.name == "nt", "POSIX symlink migration")
    def test_legacy_source_symlink_is_migrated_to_managed_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "legacy"
            (legacy / "scripts").mkdir(parents=True)
            (legacy / "VERSION").write_text("0.0.1", encoding="utf-8")
            (legacy / "scripts/tag_cli.py").write_text("", encoding="utf-8")
            (legacy / "tag").write_text(
                '#!/bin/sh\npython3 "$(dirname "$0")/scripts/tag_cli.py" "$@"\n',
                encoding="utf-8",
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            command = bin_dir / "tag"
            command.symlink_to(legacy / "tag")

            self.assertEqual(command_owner(command), "legacy Tag source checkout")
            install(ROOT, root / "home", bin_dir, dependencies=False)

            self.assertFalse(command.is_symlink())
            self.assertIn("TAG managed launcher", command.read_text(encoding="utf-8"))
            self.assertTrue((legacy / "tag").is_file())
            legacy_record = json.loads(
                (root / "home/state/legacy-command.json").read_text(encoding="utf-8")
            )
            self.assertEqual(Path(legacy_record["command"]), (legacy / "tag").resolve())

    @unittest.skipIf(os.name == "nt", "POSIX symlink protection")
    def test_unrelated_symlink_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "another-program"
            target.write_text("#!/bin/sh\n", encoding="utf-8")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            command = bin_dir / "tag"
            command.symlink_to(target)

            with self.assertRaisesRegex(RuntimeError, "unrelated command"):
                install(ROOT, root / "home", bin_dir, dependencies=False)

            self.assertTrue(command.is_symlink())

    def test_install_preserves_import_path(self):
        with tempfile.TemporaryDirectory() as temp:
            original_path = list(sys.path)
            install(ROOT, Path(temp) / "home", Path(temp) / "bin", dependencies=False)
            self.assertEqual(sys.path, original_path)

    def test_platform_defaults_and_override(self):
        fake = Path(Path.cwd().anchor) / "users/test"
        with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", return_value=fake):
            with patch("sys.platform", "darwin"):
                self.assertEqual(tag_home(), fake / "Library/Application Support/Tag")
            with patch("sys.platform", "linux"):
                self.assertEqual(tag_home(), fake / ".local/share/tag")
            with patch("sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": str(fake / "local")}):
                self.assertEqual(tag_home(), fake / "local/Tag")
            with patch("sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": "relative"}):
                self.assertEqual(tag_home(), fake / "AppData/Local/Tag")
            with patch.dict(os.environ, {"TAG_HOME": str(fake / "custom/tag")}):
                self.assertEqual(tag_home(), fake / "custom/tag")
            with patch.dict(os.environ, {"TAG_HOME": "relative"}):
                with self.assertRaises(ValueError):
                    tag_home()

    def test_temporary_root_is_owned_by_tag_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            with patch.dict(os.environ, {"TAG_HOME": str(home)}):
                self.assertEqual(tag_temp_dir(), home / "instances/default/tmp")
                self.assertTrue((home / "instances/default/tmp").is_dir())

    def test_temporary_root_restricts_home_and_temp_acls(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "instance"
            with patch.dict(os.environ, {"TAG_INSTANCE_HOME": str(home)}), patch(
                "scripts.tag_paths.restrict_windows_acl"
            ) as restrict:
                self.assertEqual(tag_temp_dir(), home / "tmp")

            self.assertEqual([call.args[0] for call in restrict.call_args_list], [home, home / "tmp"])

    def test_scoped_mcp_overlay_preserves_global_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            initialize_instance(home)
            initialize_workspace(home / "workspace")
            (home / "workspace/.codex/config.toml").write_text('[mcp_servers.example]\ncommand="python"\nargs=["server.py"]\n')
            with patch.dict(os.environ, {
                "TAG_HOME": str(home.parent),
                "TAG_INSTANCE_HOME": str(home),
                "CODEX_HOME": "global-config",
            }):
                args = codex_workspace_args(home / "workspace")
                self.assertIn('mcp_servers.example=', args[1])
                self.assertEqual(os.environ["CODEX_HOME"], "global-config")
                self.assertEqual(codex_workspace_args(home / "another-project"), [])

    def test_config_is_data_not_executable_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "settings.json"
            config.write_text(json.dumps({"SLACK_BOT_TOKEN": "$(do-not-execute)"}))
            self.assertEqual(read_config(config)["SLACK_BOT_TOKEN"], "$(do-not-execute)")
            config.write_text('{"PATH":"unexpected"}')
            with self.assertRaises(ValueError):
                read_config(config)

    def test_stop_works_even_when_configuration_is_invalid(self):
        from scripts.tag_cli import main
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            initialize(home)
            default = tag_instances.ensure_default(home).home
            (default / "config/settings.json").write_text("invalid JSON")
            with patch.dict(os.environ, {"TAG_HOME": str(home)}), patch.object(sys, "argv", ["tag", "stop"]):
                self.assertEqual(main(), 0)

    def test_archive_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape", "bad")
            with self.assertRaises(ValueError):
                unpack_release(archive, root / "output")
            self.assertFalse((root / "escape").exists())

    def test_upgrade_removes_only_generated_admin_wrappers(self):
        for wrapper in (LEGACY_ADMIN_SKILL, ADMIN_SKILL):
            with self.subTest(wrapper=wrapper), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / "home"
                for backend in (".agents", ".claude"):
                    folder = home / "workspace" / backend / "skills/open-tag-admin"
                    folder.mkdir(parents=True)
                    (folder / "SKILL.md").write_text(wrapper)
                    (folder / "personal-notes.md").write_text("keep this")
                install(ROOT, home, root / "bin", dependencies=False)
                for backend in (".agents", ".claude"):
                    folder = home / "workspace" / backend / "skills/open-tag-admin"
                    self.assertFalse((folder / "SKILL.md").exists())
                    self.assertEqual((folder / "personal-notes.md").read_text(), "keep this")

    def test_upgrade_preserves_non_utf8_custom_admin_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            skill = home / "workspace/.agents/skills/open-tag-admin/SKILL.md"
            skill.parent.mkdir(parents=True)
            custom = b"custom admin instructions: \xff\xfe"
            skill.write_bytes(custom)

            install(ROOT, home, root / "bin", dependencies=False)

            self.assertEqual(skill.read_bytes(), custom)

    def test_install_upgrade_and_run_without_source(self):
        with tempfile.TemporaryDirectory(prefix="Tag install with spaces ") as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            first = install(ROOT, home, bin_dir, dependencies=False)
            default = home / "instances/default"
            admin = default / "workspace/.agents/skills/open-tag-admin/SKILL.md"
            self.assertFalse(admin.exists())
            self.assertFalse((first / "SKILL.md").exists())
            admin.parent.mkdir(parents=True)
            admin.write_text(LEGACY_ADMIN_SKILL)
            custom_admin = default / "workspace/.claude/skills/open-tag-admin/SKILL.md"
            custom_admin.parent.mkdir(parents=True)
            custom_admin.write_text("personal admin instructions")
            skill = default / "workspace/.agents/skills/personal/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("personal skill")
            config = default / "config/settings.json"
            config.write_text('{"OPENTAG_BACKEND":"codex"}')
            second = install(ROOT, home, bin_dir, dependencies=False)
            self.assertFalse(admin.exists())
            self.assertEqual(custom_admin.read_text(), "personal admin instructions")
            self.assertNotEqual(first, second)
            previous = json.loads((home / "previous.json").read_text())
            self.assertEqual(previous["release"], first.name)
            self.assertEqual(
                previous["installed_version"],
                (ROOT / "VERSION").read_text().strip(),
            )
            self.assertEqual(skill.read_text(), "personal skill")
            self.assertEqual(config.read_text(), '{"OPENTAG_BACKEND":"codex"}')
            command = bin_dir / ("tag.cmd" if os.name == "nt" else "tag")
            result = subprocess.run([str(command), "version"], cwd=root, capture_output=True, text=True, check=True)
            self.assertIn("Tag v", result.stdout)
            result = subprocess.run([str(command), "paths", "--json"], cwd=root, capture_output=True, text=True, check=True)
            paths = json.loads(result.stdout)
            self.assertEqual(Path(paths["workspace"]).resolve(), (default / "workspace").resolve())
            self.assertTrue(Path(paths["management_guide"]).is_file())
            self.assertNotIn("admin_skill", paths)
            self.assertEqual(paths["runtime"]["mode"], "managed")
            self.assertTrue(paths["runtime"]["active_release"])
            result = subprocess.run([str(command), "inspect", "--offline", "--json"], cwd=root, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["state"], "setup_incomplete")
            self.assertFalse((second / ".git").exists())
            self.assertFalse((second / ".env").exists())
            # Rollback chooses the old code without reverting persistent settings.
            environment = dict(os.environ, TAG_HOME=str(home))
            from scripts.tag_cli import main
            with patch.dict(os.environ, environment), patch.object(sys, "argv", ["tag", "rollback"]):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads((home / "current.json").read_text())["release"], first.name)
            self.assertEqual(skill.read_text(), "personal skill")

    def test_failed_upgrade_keeps_current_release(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            bin_dir = Path(temp) / "bin"
            install(ROOT, home, bin_dir, dependencies=False)
            previous = (home / "current.json").read_text()
            with patch("subprocess.run", side_effect=RuntimeError("dependency installation failed")), patch("shutil.which", return_value="uv"):
                with self.assertRaises(RuntimeError):
                    install(ROOT, home, bin_dir)
            self.assertEqual((home / "current.json").read_text(), previous)

    def test_remote_install_persists_channel_version_and_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            version = (ROOT / "VERSION").read_text().strip()
            selection = ReleaseSelection("alpha", version, "a" * 40)

            install(
                ROOT, home, Path(temp) / "bin", dependencies=False,
                selection=selection,
            )
            current = json.loads((home / "current.json").read_text())

            install(
                ROOT, home, Path(temp) / "bin", dependencies=False,
                selection=ReleaseSelection("edge", version, "b" * 40),
            )
            previous = json.loads((home / "previous.json").read_text())
            upgraded = json.loads((home / "current.json").read_text())

        self.assertEqual(previous["channel"], "alpha")
        self.assertEqual(current["channel"], "alpha")
        self.assertEqual(current["installed_version"], version)
        self.assertEqual(current["installed_commit"], "a" * 40)
        self.assertTrue(current["checked_at"].endswith("Z"))
        self.assertEqual(upgraded["channel"], "edge")
        self.assertEqual(upgraded["installed_commit"], "b" * 40)

    def test_upgrade_reminder_uses_fresh_installation_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "state").mkdir()
            (home / "current.json").write_text(json.dumps({
                "installed_version": "0.2.0-alpha.1",
                "installed_commit": "a" * 40,
                "channel": "alpha",
                "selection": "channel",
            }), encoding="utf-8")
            (home / "state/update-check.json").write_text(json.dumps({
                "schema_version": 1,
                "checked_at": 1000,
                "channel": "alpha",
                "target_version": "0.2.0-alpha.2",
                "target_commit": "b" * 40,
            }), encoding="utf-8")

            with patch("scripts.tag_install.resolve_channel") as resolve:
                reminder = upgrade_reminder(home, now=1001)

        resolve.assert_not_called()
        self.assertEqual(reminder, {
            "status": "available",
            "version": "0.2.0-alpha.2",
            "command": "tag upgrade",
        })

    def test_upgrade_reminder_refreshes_stale_cache_with_a_bounded_check(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            home.mkdir(exist_ok=True)
            (home / "current.json").write_text(json.dumps({
                "installed_version": "0.2.0-alpha.1",
                "installed_commit": "a" * 40,
                "channel": "alpha",
                "selection": "channel",
            }), encoding="utf-8")
            release = release_record("0.2.0-alpha.3", prerelease=True)
            release["target_commitish"] = "c" * 40

            with patch(
                "scripts.tag_install.resolve_channel", return_value=release
            ) as resolve:
                reminder = upgrade_reminder(home, now=2000)

            cached = json.loads((home / "state/update-check.json").read_text())

        resolve.assert_called_once_with("alpha", timeout=2, page_limit=1)
        self.assertEqual(reminder["version"], "0.2.0-alpha.3")
        self.assertEqual(cached["target_commit"], "c" * 40)

    def test_upgrade_reminder_checks_alpha_for_source_and_ignores_pinned_installs(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            current = home / "current.json"
            current.write_text(json.dumps({
                "installed_version": "0.2.0-alpha",
            }), encoding="utf-8")
            release = release_record("0.2.0-alpha.2", prerelease=True)
            release["target_commitish"] = "b" * 40
            with patch(
                "scripts.tag_install.resolve_channel", return_value=release
            ) as resolve:
                self.assertEqual(upgrade_reminder(home, now=1000), {
                    "status": "available",
                    "version": "0.2.0-alpha.2",
                    "command": "tag upgrade --channel alpha",
                })
            resolve.assert_called_once_with("alpha", timeout=2, page_limit=1)

            current.write_text(json.dumps({
                "installed_version": "0.2.0-alpha.1",
                "selection": "version",
            }), encoding="utf-8")
            self.assertIsNone(upgrade_reminder(home))

    def test_upgrade_reminder_does_not_downgrade_beta_source_without_current_file(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            release = release_record("0.2.0-alpha.11", prerelease=True)
            release["target_commitish"] = "b" * 40

            with patch(
                "scripts.tag_install.resolve_channel", return_value=release
            ) as resolve:
                reminder = upgrade_reminder(home, now=1000)

        resolve.assert_called_once_with("alpha", timeout=2, page_limit=1)
        self.assertIsNone(reminder)

    def test_upgrade_reminder_ignores_network_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "current.json").write_text(json.dumps({
                "installed_version": "0.2.0-alpha.1",
                "installed_commit": "a" * 40,
                "channel": "alpha",
                "selection": "channel",
            }), encoding="utf-8")

            with patch(
                "scripts.tag_install.resolve_channel",
                side_effect=RuntimeError("offline"),
            ) as resolve:
                self.assertIsNone(upgrade_reminder(home, now=2000))
                self.assertIsNone(upgrade_reminder(home, now=2001))

            cached = json.loads((home / "state/update-check.json").read_text())

        resolve.assert_called_once_with("alpha", timeout=2, page_limit=1)
        self.assertEqual(cached["checked_at"], 2000)
        self.assertEqual(cached["channel"], "alpha")
        self.assertNotIn("target_version", cached)

    def test_upgrade_reminder_failure_retains_only_same_channel_target(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "state").mkdir()
            current = home / "current.json"
            current.write_text(json.dumps({
                "installed_version": "0.2.0-alpha.1",
                "channel": "alpha",
                "selection": "channel",
            }), encoding="utf-8")
            cache = home / "state/update-check.json"
            cache.write_text(json.dumps({
                "schema_version": 1,
                "checked_at": 0,
                "channel": "alpha",
                "target_version": "0.2.0-alpha.2",
                "target_commit": "b" * 40,
            }), encoding="utf-8")

            with patch(
                "scripts.tag_install.resolve_channel",
                side_effect=RuntimeError("offline"),
            ):
                self.assertEqual(upgrade_reminder(home, now=100000), {
                    "status": "available",
                    "version": "0.2.0-alpha.2",
                    "command": "tag upgrade",
                })
            retained = json.loads(cache.read_text())

            current.write_text(json.dumps({
                "installed_version": "0.2.0-beta.1",
                "channel": "beta",
                "selection": "channel",
            }), encoding="utf-8")
            with patch(
                "scripts.tag_install.resolve_channel",
                side_effect=RuntimeError("offline"),
            ):
                self.assertIsNone(upgrade_reminder(home, now=200000))
            replaced = json.loads(cache.read_text())

        self.assertEqual(retained["checked_at"], 100000)
        self.assertEqual(retained["target_version"], "0.2.0-alpha.2")
        self.assertEqual(replaced["channel"], "beta")
        self.assertNotIn("target_version", replaced)

    def test_upgrade_follows_saved_channel_and_preserves_personal_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            version = (ROOT / "VERSION").read_text().strip()
            install(
                ROOT,
                home,
                bin_dir,
                dependencies=False,
                selection=ReleaseSelection("edge", version, "a" * 40),
            )
            config = home / "instances/default/config/settings.json"
            config.write_text('{"OPENTAG_BACKEND":"codex"}', encoding="utf-8")
            target = FetchedRelease(
                ROOT,
                ReleaseSelection("edge", version, "b" * 40),
            )
            output = io.StringIO()

            with patch(
                "scripts.tag_install.fetch_release", return_value=target
            ), patch(
                "scripts.tag_cli.process_for", return_value=None
            ), contextlib.redirect_stdout(output):
                self.assertEqual(
                    upgrade_command(
                        home, json_output=True, dependencies=False
                    ),
                    0,
                )

            result = json.loads(output.getvalue())
            current = json.loads((home / "current.json").read_text())
            previous = json.loads((home / "previous.json").read_text())
            config_text = config.read_text()

        self.assertEqual(result["status"], "upgraded")
        self.assertEqual(current["channel"], "edge")
        self.assertEqual(current["installed_commit"], "b" * 40)
        self.assertEqual(previous["installed_commit"], "a" * 40)
        self.assertEqual(config_text, '{"OPENTAG_BACKEND":"codex"}')

    def test_upgrade_dry_run_and_exact_version_pin_do_not_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            version = (ROOT / "VERSION").read_text().strip()
            install(
                ROOT,
                home,
                bin_dir,
                dependencies=False,
                selection=ReleaseSelection("alpha", version, "a" * 40),
            )
            original = (home / "current.json").read_text()
            target = FetchedRelease(
                ROOT,
                ReleaseSelection("alpha", version, "b" * 40),
            )
            output = io.StringIO()
            with patch(
                "scripts.tag_install.fetch_release", return_value=target
            ), patch(
                "scripts.tag_cli.process_for", return_value=None
            ), patch(
                "scripts.tag_install.install"
            ) as installer, contextlib.redirect_stdout(output):
                self.assertEqual(
                    upgrade_command(home, dry_run=True, json_output=True),
                    0,
                )
            installer.assert_not_called()
            self.assertEqual((home / "current.json").read_text(), original)
            self.assertEqual(json.loads(output.getvalue())["status"], "available")

            pinned = json.loads(original)
            pinned["selection"] = "version"
            (home / "current.json").write_text(json.dumps(pinned), encoding="utf-8")
            output = io.StringIO()
            with patch(
                "scripts.tag_install.fetch_release"
            ) as fetch, contextlib.redirect_stdout(output):
                self.assertEqual(upgrade_command(home, json_output=True), 0)
            fetch.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["status"], "pinned")

    def test_upgrade_does_not_reinstall_the_current_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            version = (ROOT / "VERSION").read_text().strip()
            selection = ReleaseSelection("edge", version, "a" * 40)
            install(
                ROOT,
                home,
                bin_dir,
                dependencies=False,
                selection=selection,
            )
            output = io.StringIO()
            with patch(
                "scripts.tag_install.fetch_release",
                return_value=FetchedRelease(ROOT, selection),
            ), patch(
                "scripts.tag_install.install"
            ) as installer, patch(
                "scripts.tag_cli.process_for", return_value=None
            ), contextlib.redirect_stdout(output):
                self.assertEqual(upgrade_command(home, json_output=True), 0)

        installer.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["status"], "current")

    def test_upgrade_reads_version_from_legacy_active_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            version = (ROOT / "VERSION").read_text().strip()
            selection = ReleaseSelection("edge", version, "a" * 40)
            install(
                ROOT,
                home,
                bin_dir,
                dependencies=False,
                selection=selection,
            )
            current_path = home / "current.json"
            current = json.loads(current_path.read_text())
            del current["installed_version"]
            current_path.write_text(json.dumps(current), encoding="utf-8")

            for arguments in ({"channel": "edge"}, {"version": version}):
                with self.subTest(arguments=arguments):
                    output = io.StringIO()
                    with patch(
                        "scripts.tag_install.fetch_release",
                        return_value=FetchedRelease(ROOT, selection),
                    ), patch(
                        "scripts.tag_install.install"
                    ) as installer, patch(
                        "scripts.tag_cli.process_for", return_value=None
                    ), contextlib.redirect_stdout(output):
                        self.assertEqual(
                            upgrade_command(
                                home,
                                dry_run=True,
                                json_output=True,
                                **arguments,
                            ),
                            0,
                        )

                    installer.assert_not_called()
                    result = json.loads(output.getvalue())
                    self.assertEqual(result["current"]["version"], version)
                    self.assertFalse(result["downgrade"])

    def test_upgrade_restarts_running_services_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            version = (ROOT / "VERSION").read_text().strip()
            install(
                ROOT,
                home,
                bin_dir,
                dependencies=False,
                selection=ReleaseSelection("edge", version, "a" * 40),
            )
            target = FetchedRelease(
                ROOT,
                ReleaseSelection("edge", version, "b" * 40),
            )
            output = io.StringIO()
            with patch(
                "scripts.tag_install.fetch_release", return_value=target
            ), patch(
                "scripts.tag_install.install"
            ) as installer, patch(
                "scripts.tag_cli.process_for", return_value=object()
            ), patch(
                "scripts.tag_cli.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run, contextlib.redirect_stdout(output):
                self.assertEqual(
                    upgrade_command(home, json_output=True, dependencies=False),
                    0,
                )

        installer.assert_called_once()
        self.assertEqual(run.call_args.args[0][-1], "restart")
        result = json.loads(output.getvalue())
        self.assertTrue(result["restarted"])
        self.assertFalse(result["restart_required"])

    def test_upgrade_blocks_older_exact_version_without_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            version = (ROOT / "VERSION").read_text().strip()
            install(
                ROOT,
                home,
                bin_dir,
                dependencies=False,
                selection=ReleaseSelection("alpha", version, "a" * 40),
            )
            original = (home / "current.json").read_text()
            target = FetchedRelease(
                ROOT,
                ReleaseSelection("stable", "0.1.0", "b" * 40, "version"),
            )
            output = io.StringIO()
            with patch(
                "scripts.tag_install.fetch_release", return_value=target
            ), patch(
                "scripts.tag_install.install"
            ) as installer, patch(
                "scripts.tag_cli.process_for", return_value=None
            ), contextlib.redirect_stdout(output):
                self.assertEqual(
                    upgrade_command(
                        home,
                        version="0.1.0",
                        json_output=True,
                        dependencies=False,
                    ),
                    2,
                )

            result = json.loads(output.getvalue())
            installer.assert_not_called()
            self.assertEqual((home / "current.json").read_text(), original)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "downgrade-blocked")
            self.assertTrue(result["downgrade"])
            self.assertIn("--allow-downgrade", result["next_command"])

    def test_channel_switch_keeps_newer_release_until_channel_catches_up(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            version = (ROOT / "VERSION").read_text().strip()
            install(
                ROOT,
                home,
                bin_dir,
                dependencies=False,
                selection=ReleaseSelection("alpha", version, "a" * 40),
            )
            target = FetchedRelease(
                ROOT,
                ReleaseSelection("stable", "0.1.0", "b" * 40),
            )
            output = io.StringIO()
            with patch(
                "scripts.tag_install.fetch_release", return_value=target
            ), patch(
                "scripts.tag_install.install"
            ) as installer, patch(
                "scripts.tag_cli.process_for", return_value=None
            ), contextlib.redirect_stdout(output):
                self.assertEqual(
                    upgrade_command(
                        home,
                        channel="stable",
                        json_output=True,
                        dependencies=False,
                    ),
                    0,
                )

            result = json.loads(output.getvalue())
            current = json.loads((home / "current.json").read_text())
            installer.assert_not_called()
            self.assertEqual(result["status"], "channel-updated")
            self.assertTrue(result["policy_updated"])
            self.assertEqual(current["channel"], "stable")
            self.assertEqual(current["installed_version"], version)
            self.assertEqual(current["installed_commit"], "a" * 40)

    def test_upgrade_allows_an_explicit_downgrade_and_explains_the_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, bin_dir = root / "home", root / "bin"
            version = (ROOT / "VERSION").read_text().strip()
            install(
                ROOT,
                home,
                bin_dir,
                dependencies=False,
                selection=ReleaseSelection("alpha", version, "a" * 40),
            )
            target = FetchedRelease(
                ROOT,
                ReleaseSelection("stable", "0.1.0", "b" * 40, "version"),
            )
            blocked_output = io.StringIO()
            with patch(
                "scripts.tag_install.fetch_release", return_value=target
            ), patch(
                "scripts.tag_install.install"
            ) as installer, patch(
                "scripts.tag_cli.process_for", return_value=None
            ), contextlib.redirect_stdout(blocked_output):
                self.assertEqual(
                    upgrade_command(
                        home,
                        version="0.1.0",
                        dependencies=False,
                    ),
                    2,
                )
            installer.assert_not_called()
            screen = blocked_output.getvalue()
            self.assertIn("Kept the newer installed release", screen)
            self.assertIn("--allow-downgrade", screen)

            allowed_output = io.StringIO()
            with patch(
                "scripts.tag_install.fetch_release", return_value=target
            ), patch(
                "scripts.tag_install.install"
            ) as installer, patch(
                "scripts.tag_cli.process_for", return_value=None
            ), contextlib.redirect_stdout(allowed_output):
                self.assertEqual(
                    upgrade_command(
                        home,
                        version="0.1.0",
                        allow_downgrade=True,
                        json_output=True,
                        dependencies=False,
                    ),
                    0,
                )
            installer.assert_called_once()
            result = json.loads(allowed_output.getvalue())
            self.assertEqual(result["status"], "upgraded")
            self.assertTrue(result["downgrade"])

    def test_dependency_install_falls_back_to_venv_and_pip_without_uv(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "scripts.tag_install.shutil.which", return_value=None
        ), patch(
            "scripts.tag_install.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run, patch(
            "scripts.tag_install.install_mfs_cli",
            return_value=Path(temp) / "home/releases/release/.venv/bin/mfs",
        ):
            install(ROOT, Path(temp) / "home", Path(temp) / "bin")

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][1:3], ["-m", "venv"])
        self.assertEqual(commands[1][1:4], ["-m", "pip", "install"])
        self.assertEqual(
            Path(commands[2][1]).name,
            "preload_mfs_model.py",
        )

    def test_background_lifecycle_and_stale_pid_safety(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            initialize_instance(home)
            try:
                self.assertTrue(start_process(home, "test", [sys.executable, "-c", "import time; time.sleep(90)"]))
                self.assertFalse(start_process(home, "test", ["must-not-run"]))
                self.assertIsNotNone(process_for(home / "state/test.json"))
            finally:
                stop_process(home, "test")
            self.assertIsNone(process_for(home / "state/test.json"))
            record = home / "state/test.json"
            record.write_text(json.dumps({"pid": os.getpid(), "created": 0}))
            stop_process(home, "test")
            self.assertFalse(record.exists())


if __name__ == "__main__":
    unittest.main()
