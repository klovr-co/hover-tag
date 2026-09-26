from __future__ import annotations

import ast
import hashlib
import os
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import package_release
from scripts.package_release import build_archive
from scripts.release_check import validate_release
from scripts.release_automation import (
    Version,
    build_channel_index,
    derive_next_version,
    find_edge_artifact,
    promote_edge_bundle,
    select_auto_prerelease,
    successful_run,
    validate_candidate,
    validate_edge_bundle,
    validate_release_bundle,
    validate_release_tag,
    validate_selected_sha,
    validate_transition,
    wait_for_predecessor,
    workflow_gate_state,
)


class VersionTransitionTests(unittest.TestCase):
    def test_derives_normal_minor_and_maintenance_patch_lines(self) -> None:
        stable = Version.parse("0.7.0")

        self.assertEqual(str(derive_next_version(stable, "alpha")), "0.8.0-alpha.1")
        self.assertEqual(
            str(derive_next_version(stable, "alpha", maintenance=True)),
            "0.7.1-alpha.1",
        )

    def test_derives_alpha_beta_and_stable_progression(self) -> None:
        self.assertEqual(
            str(derive_next_version(Version.parse("0.8.0-alpha.2"), "alpha")),
            "0.8.0-alpha.3",
        )
        self.assertEqual(
            str(derive_next_version(Version.parse("0.8.0-alpha.3"), "beta")),
            "0.8.0-beta.1",
        )
        self.assertEqual(
            str(derive_next_version(Version.parse("0.8.0-beta.1"), "stable")),
            "0.8.0",
        )

    def test_rejects_skipped_counter_and_backward_phase(self) -> None:
        self.assertTrue(validate_transition(
            Version.parse("0.8.0-alpha.1"), Version.parse("0.8.0-alpha.3")
        ))
        self.assertTrue(validate_transition(
            Version.parse("0.8.0-beta.1"), Version.parse("0.8.0-alpha.2")
        ))
        self.assertTrue(validate_transition(
            Version.parse("0.8.0"), Version.parse("2.0.0-alpha.1")
        ))

    def test_rejects_duplicate_tag_and_phase_mismatch(self) -> None:
        errors = validate_candidate(
            "0.8.0-alpha.1", "beta", ["v0.7.0", "v0.8.0-alpha.1", "edge"]
        )

        self.assertTrue(any("already exists" in error for error in errors))
        self.assertTrue(any("not requested phase" in error for error in errors))
        self.assertEqual(validate_candidate(
            "0.8.0-alpha.1", "alpha", ["v0.8.0-alpha.1"], allow_existing_version=True
        ), [])

    def test_accepts_legacy_alpha_on_a_new_version_line(self) -> None:
        self.assertEqual(
            validate_candidate("0.1.1-alpha", "alpha", ["v0.1.0-alpha"]), []
        )
        self.assertTrue(validate_candidate("0.2.0-alpha", "alpha", ["v0.1.0"]))

    def test_automatic_alpha_starts_configured_line_then_increments_it(self) -> None:
        self.assertEqual(
            str(select_auto_prerelease(
                "0.2.0-alpha", ["v0.1.0-alpha"], []
            )),
            "0.2.0-alpha.1",
        )
        self.assertEqual(
            str(select_auto_prerelease(
                "0.2.0-alpha", ["v0.2.0-alpha.1", "v0.2.0-alpha.2"], []
            )),
            "0.2.0-alpha.3",
        )
        self.assertEqual(
            str(select_auto_prerelease(
                "0.3.0-alpha", ["v0.2.0-alpha.4"], []
            )),
            "0.3.0-alpha.1",
        )

    def test_release_tag_must_match_source_version_policy(self) -> None:
        self.assertEqual(
            validate_release_tag("0.2.0-alpha", "v0.2.0-alpha.4"), []
        )
        self.assertEqual(
            validate_release_tag("0.2.0-alpha.2", "v0.2.0-alpha.4"), []
        )
        self.assertEqual(validate_release_tag("0.2.0-beta", "v0.2.0-beta"), [])
        self.assertEqual(validate_release_tag("0.2.0-beta", "v0.2.0-beta.1"), [])
        self.assertEqual(validate_release_tag("0.2.0-beta.1", "v0.2.0-beta.4"), [])
        self.assertEqual(validate_release_tag("0.2.0", "v0.2.0"), [])
        self.assertTrue(validate_release_tag("0.2.0-alpha", "v0.3.0-alpha.1"))
        self.assertTrue(validate_release_tag("0.2.0-alpha", "v0.2.0-beta"))

    def test_automatic_alpha_honors_explicit_line_and_skip_labels(self) -> None:
        tags = ["v0.2.0-alpha.4"]

        self.assertEqual(
            str(select_auto_prerelease("0.2.0-alpha", tags, ["release:next-patch"])),
            "0.2.1-alpha.1",
        )
        self.assertEqual(
            str(select_auto_prerelease("0.2.0-alpha", tags, ["release:next-minor"])),
            "0.3.0-alpha.1",
        )
        self.assertIsNone(select_auto_prerelease(
            "0.2.0-alpha", tags, ["release:skip"]
        ))
        self.assertIsNone(select_auto_prerelease("0.2.0", tags, []))

    def test_automatic_beta_starts_after_alpha_and_then_increments(self) -> None:
        self.assertEqual(
            str(select_auto_prerelease(
                "0.2.0-beta.1", ["v0.2.0-alpha.12"], []
            )),
            "0.2.0-beta.1",
        )
        self.assertEqual(
            str(select_auto_prerelease(
                "0.2.0-beta.1", ["v0.2.0-alpha.12", "v0.2.0-beta.1"], []
            )),
            "0.2.0-beta.2",
        )
        self.assertIsNone(select_auto_prerelease(
            "0.2.0-beta.1", ["v0.2.0-beta.1"], ["release:skip"]
        ))
        with self.assertRaisesRegex(ValueError, "beta line only supports"):
            select_auto_prerelease(
                "0.2.0-beta.1", ["v0.2.0-beta.1"], ["release:next-minor"]
            )
        with self.assertRaisesRegex(ValueError, "requires a published alpha"):
            select_auto_prerelease("0.2.0-beta.1", [], [])

    def test_automatic_alpha_rejects_conflicting_release_labels(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicting automatic release labels"):
            select_auto_prerelease(
                "0.2.0-alpha", ["v0.1.0"],
                ["release:next-patch", "release:skip"],
            )
        with self.assertRaisesRegex(ValueError, "unknown automatic release labels"):
            select_auto_prerelease("0.2.0-alpha", ["v0.1.0"], ["release:maybe"])


class ChannelIndexTests(unittest.TestCase):
    @staticmethod
    def release(version: str, sha: str, *, prerelease: bool) -> dict:
        names = [f"tag-{version}.zip", "SHA256SUMS", "BUILD-PROVENANCE.json"]
        return {
            "tag_name": f"v{version}",
            "draft": False,
            "prerelease": prerelease,
            "target_commitish": sha,
            "assets": [{"name": name} for name in names],
        }

    def test_builds_phase_specific_channel_pointers_and_edge(self) -> None:
        stable = self.release("1.0.0", "a" * 40, prerelease=False)
        alpha = self.release("1.1.0-alpha.2", "b" * 40, prerelease=True)
        beta = self.release("1.1.0-beta.1", "c" * 40, prerelease=True)
        edge = {
            "tag_name": "edge",
            "draft": False,
            "prerelease": True,
            "target_commitish": "d" * 40,
            "assets": [
                {"name": "tag-edge.zip"},
                {"name": "SHA256SUMS"},
                {"name": "BUILD-PROVENANCE.json"},
            ],
        }

        index = build_channel_index(
            [stable, alpha, beta, edge],
            repository="klovr-co/hover-tag",
            generated_at="2026-09-22T00:00:00Z",
        )

        self.assertEqual(index["schema_version"], 1)
        self.assertEqual(index["repository"], "klovr-co/hover-tag")
        self.assertEqual(index["channels"]["stable"]["version"], "1.0.0")
        self.assertEqual(index["channels"]["beta"]["version"], "1.1.0-beta.1")
        self.assertEqual(index["channels"]["alpha"]["version"], "1.1.0-alpha.2")
        self.assertEqual(index["channels"]["edge"], {
            "version": "edge",
            "tag": "edge",
            "commit_sha": "d" * 40,
        })

    def test_rejects_selected_release_with_missing_verified_assets(self) -> None:
        release = self.release("1.1.0-alpha.1", "a" * 40, prerelease=True)
        release["assets"] = [{"name": "tag-1.1.0-alpha.1.zip"}]

        with self.assertRaisesRegex(ValueError, "missing required assets"):
            build_channel_index(
                [release],
                repository="klovr-co/hover-tag",
                generated_at="2026-09-22T00:00:00Z",
            )


class SelectedCommitTests(unittest.TestCase):
    def test_requires_exact_full_sha_on_main(self) -> None:
        sha = "a" * 40
        self.assertEqual(validate_selected_sha(sha, sha, True), [])
        self.assertEqual(len(validate_selected_sha("abc123", sha, False)), 3)

    def test_requires_successful_push_run_for_exact_sha(self) -> None:
        sha = "b" * 40
        runs = [
            {"head_sha": sha, "event": "pull_request", "status": "completed", "conclusion": "success"},
            {"head_sha": "c" * 40, "event": "push", "status": "completed", "conclusion": "success"},
        ]
        self.assertFalse(successful_run(runs, sha))
        runs.append({"head_sha": sha, "event": "push", "status": "completed", "conclusion": "success"})
        self.assertTrue(successful_run(runs, sha))

    def test_latest_workflow_attempt_is_authoritative(self) -> None:
        sha = "b" * 40
        runs = [
            {"id": 1, "head_sha": sha, "event": "push", "status": "completed", "conclusion": "failure"},
            {"id": 2, "head_sha": sha, "event": "push", "status": "in_progress", "conclusion": None},
        ]
        self.assertEqual(workflow_gate_state(runs, sha), "pending")
        runs[-1].update(status="completed", conclusion="success")
        self.assertEqual(workflow_gate_state(runs, sha), "success")

    def test_historical_edge_artifact_is_found_by_exact_name(self) -> None:
        sha = "b" * 40
        with patch("scripts.release_automation._github_json", return_value={
            "artifacts": [{
                "name": f"tag-edge-{sha}",
                "expired": False,
                "created_at": "2026-09-21T00:00:00Z",
                "workflow_run": {"id": 42, "head_sha": "c" * 40},
            }]
        }):
            self.assertEqual(find_edge_artifact("klovr-co/hover-tag", sha), (
                42, f"tag-edge-{sha}"
            ))

    def test_predecessor_waits_for_successful_edge_processing(self) -> None:
        sha = "b" * 40

        def github_response(repository, path, parameters=None):
            if path.startswith("actions/workflows/"):
                return {"workflow_runs": [{
                    "id": 1,
                    "head_sha": sha,
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                }]}
            if path == "actions/artifacts":
                return {"artifacts": [{
                    "name": f"tag-edge-{sha}",
                    "expired": False,
                    "created_at": "2026-09-21T00:00:00Z",
                    "workflow_run": {"id": 42},
                }]}
            if path == "actions/runs/42":
                return {"status": "completed", "conclusion": "success"}
            raise AssertionError(path)

        with patch("scripts.release_automation._github_json", side_effect=github_response):
            self.assertEqual(wait_for_predecessor("klovr-co/hover-tag", sha, 0), [])

    def test_failed_predecessor_gate_does_not_block_later_commit(self) -> None:
        sha = "b" * 40
        with patch("scripts.release_automation._github_json", return_value={
            "workflow_runs": [{
                "id": 1,
                "head_sha": sha,
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
            }]
        }):
            self.assertEqual(wait_for_predecessor("klovr-co/hover-tag", sha, 0), [])


class ReleaseArtifactTests(unittest.TestCase):
    def test_package_is_reproducible_for_the_same_commit(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            first = temporary / "first.zip"
            second = temporary / "second.zip"

            first_digest = build_archive(root, first)
            second_digest = build_archive(root, second)
            first_bytes = first.read_bytes()
            second_bytes = second.read_bytes()

        self.assertEqual(first_digest, second_digest)
        self.assertEqual(first_bytes, second_bytes)

    def test_package_can_stamp_generated_version_without_editing_source(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source_version = (root / "VERSION").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / "release.zip"
            build_archive(root, archive, version="9.8.7-alpha.6")
            with zipfile.ZipFile(archive) as bundle:
                packaged_version = bundle.read("VERSION").decode()

        self.assertEqual(packaged_version, "9.8.7-alpha.6\n")
        self.assertEqual((root / "VERSION").read_text(encoding="utf-8"), source_version)

    def test_package_can_stamp_approved_telemetry_destination(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/tag_telemetry_config.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / "release.zip"
            build_archive(
                root,
                archive,
                posthog_host="https://eu.posthog.example",
                posthog_project_token="phc_public_project",
                privacy_notice_url="https://example.com/privacy",
            )
            with zipfile.ZipFile(archive) as bundle:
                packaged = bundle.read("scripts/tag_telemetry_config.py").decode()

        self.assertIn("https://eu.posthog.example", packaged)
        self.assertIn("phc_public_project", packaged)
        self.assertIn("https://example.com/privacy", packaged)
        self.assertEqual(
            (root / "scripts/tag_telemetry_config.py").read_text(encoding="utf-8"),
            source,
        )

    def test_numbered_alpha_archive_satisfies_release_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            archive = temporary / "tag-0.2.0-alpha.11.zip"
            extracted = temporary / "extracted"
            build_archive(root, archive, version="0.2.0-alpha.11")
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(extracted)

            errors = validate_release(extracted)

        self.assertEqual(errors, [])

    def test_numbered_beta_archive_satisfies_release_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            archive = temporary / "tag-0.2.0-beta.5.zip"
            extracted = temporary / "extracted"
            build_archive(root, archive, version="0.2.0-beta.5")
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(extracted)

            errors = validate_release(extracted)

        self.assertEqual(errors, [])

    def test_package_contains_only_explicit_runtime_files(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "runtime.zip"
            build_archive(root, archive)
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
        self.assertIn("scripts/tag_cli.py", names)
        self.assertIn("scripts/release_check.py", names)  # Used by installer and doctor.
        self.assertIn("references/runtime-agent.md", names)
        self.assertIn("docs/tag-management.md", names)
        self.assertIn(".agents/skills/tag-troubleshoot/SKILL.md", names)
        for prefix in ("tests/", ".github/", "assets/", "docs/testing/", "docs/release-evidence/"):
            self.assertFalse(any(name.startswith(prefix) for name in names), prefix)
        for name in ("AGENTS.md", ".codex/config.toml", "scripts/package_release.py", "scripts/check_secrets.py"):
            self.assertNotIn(name, names)

    def test_runtime_manifest_covers_local_python_imports(self) -> None:
        root = Path(__file__).resolve().parents[1]
        names = set(package_release.runtime_files(root))
        local_modules = {p.stem for p in (root / "scripts").glob("*.py")}
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            tree = ast.parse((root / name).read_text())
            for node in ast.walk(tree):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [alias.name.split(".")[-1] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.module in {None, "scripts"}:
                        imports = [alias.name for alias in node.names]
                    else:
                        imports = [node.module.split(".")[-1]]
                for imported in set(imports) & local_modules:
                    self.assertIn(f"scripts/{imported}.py", names, f"{name} imports omitted module {imported}")

    def test_package_rejects_missing_or_unsafe_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            manifest = root / "scripts/runtime-files.json"
            def policy(name):
                manifest.write_text(json.dumps({"schema_version": 1, "files": ["scripts/runtime-files.json", name]}))
            policy("missing.txt")
            with self.assertRaisesRegex(FileNotFoundError, "Required runtime file missing"):
                build_archive(root, root / "invalid.zip", epoch=315532800)
            for name in ("../private.env", "/absolute", "C:/absolute", "directory\\secret"):
                policy(name)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    build_archive(root, root / "invalid.zip", epoch=315532800)
            policy("included.txt")
            (root / "included.txt").write_text("included")
            (root / "secret.env").write_text("must not ship")
            build_archive(root, root / "valid.zip", epoch=315532800)
            with zipfile.ZipFile(root / "valid.zip") as bundle:
                self.assertEqual(set(bundle.namelist()), {"scripts/runtime-files.json", "included.txt"})

    @unittest.skipIf(os.name == "nt", "Creating symlinks requires Windows privileges")
    def test_package_rejects_manifest_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            (root / "scripts/runtime-files.json").write_text(json.dumps({
                "schema_version": 1, "files": ["scripts/runtime-files.json", "linked.txt"]}))
            (root / "target.txt").write_text("private")
            (root / "linked.txt").symlink_to(root / "target.txt")
            with self.assertRaisesRegex(ValueError, "symlinks"):
                build_archive(root, root / "invalid.zip", epoch=315532800)

    def test_validates_and_promotes_exact_edge_bytes(self) -> None:
        sha = "d" * 40
        version = "0.8.0-alpha.1"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidate = root / "candidate"
            output = root / "release"
            candidate.mkdir()
            archive = candidate / "tag-edge.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("VERSION", version + "\n")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (candidate / "SHA256SUMS").write_text(
                f"{digest}  tag-edge.zip\n", encoding="utf-8"
            )
            (candidate / "BUILD-PROVENANCE.json").write_text(json.dumps({
                "schema_version": 1,
                "channel": "edge",
                "commit_sha": sha,
                "built_at": "2026-09-21T00:00:00Z",
                "source_ref": "refs/heads/main",
                "version": version,
                "archive": {"name": "tag-edge.zip", "sha256": digest},
            }), encoding="utf-8")

            self.assertEqual(validate_edge_bundle(candidate, sha, version), [])
            promoted = promote_edge_bundle(candidate, output, version)

            self.assertEqual(promoted.read_bytes(), archive.read_bytes())
            self.assertEqual(validate_release_bundle(output, version, sha), [])
            release_provenance = json.loads(
                (output / "BUILD-PROVENANCE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(release_provenance["channel"], "release")
            self.assertEqual(release_provenance["archive"]["name"], promoted.name)
            self.assertTrue(validate_release_bundle(output, version, "e" * 40))

    def test_rejects_malformed_release_assets_without_crashing(self) -> None:
        version = "0.8.0-alpha.1"
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            (output / f"tag-{version}.zip").write_bytes(b"not a zip")
            (output / "SHA256SUMS").write_text("not a checksum\n", encoding="utf-8")
            (output / "BUILD-PROVENANCE.json").write_text("[]\n", encoding="utf-8")

            errors = validate_release_bundle(output, version)

        self.assertTrue(any("invalid release archive" in error for error in errors))
        self.assertTrue(any("expected a JSON object" in error for error in errors))
        self.assertTrue(any("SHA256SUMS" in error for error in errors))


class ReleasePreflightTests(unittest.TestCase):
    def test_beta_guidance_uses_a_complete_source_version(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in ("RELEASE.md", ".agents/skills/tag-release/SKILL.md"):
            guidance = (root / relative_path).read_text(encoding="utf-8")
            self.assertIn("<major>.<minor>.<patch>-beta.1", guidance)
            self.assertNotIn("`VERSION` set to\n`beta.1`", guidance)
            self.assertNotIn("update `VERSION` to `beta.1`", guidance)

    def test_main_gates_preserve_every_push_and_cancel_stale_pr_runs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in (".github/workflows/ci.yml", ".github/workflows/install-smoke.yml"):
            workflow = (root / relative_path).read_text(encoding="utf-8")
            self.assertIn("github.event.pull_request.number || github.sha", workflow)
            self.assertIn(
                "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
                workflow,
            )

    def test_edge_workflow_auto_publishes_prereleases_and_supports_skip_label(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/edge-build.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("next-prerelease", workflow)
        self.assertIn("Publish immutable prerelease", workflow)
        self.assertIn('if: env.AUTO_PUBLISH == \'true\'', workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn('startswith("release:")', workflow)
        self.assertIn("edge-release-${{ github.event.workflow_run.head_sha }}", workflow)
        self.assertIn("wait-for-predecessor", workflow)

    def test_release_workflows_gate_telemetry_stamping_on_explicit_approval(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in (
            ".github/workflows/edge-build.yml",
            ".github/workflows/release-package.yml",
        ):
            workflow = (root / relative_path).read_text(encoding="utf-8")
            self.assertIn("vars.TAG_TELEMETRY_RELEASE_APPROVED", workflow)
            self.assertIn("secrets.TAG_POSTHOG_PROJECT_TOKEN", workflow)
            self.assertIn("https://eu.i.posthog.com", workflow)
            self.assertIn("docs/reference/telemetry.md", workflow)
            self.assertIn('--posthog-project-token "$TAG_POSTHOG_PROJECT_TOKEN"', workflow)

    def test_prepare_workflow_uses_candidate_preflight(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/prepare-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("run: ./scripts/release_preflight.sh\n", workflow)
        self.assertNotIn("release_preflight.sh --publish", workflow)
        self.assertIn("RELEASE_PHASE: stable", workflow)
        self.assertNotIn("options: [beta, stable]", workflow)

    def test_release_workflow_validates_tag_against_source_version(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/release-package.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("validate-release-tag", workflow)
        self.assertIn("--source-version", workflow)

    def test_channel_index_workflow_publishes_a_fixed_public_asset(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/channel-index.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("workflows: [Edge build, Release package]", workflow)
        self.assertIn("write-channel-index", workflow)
        self.assertIn("tag-release-channels.json", workflow)
        self.assertIn("gh release upload channels", workflow)
        self.assertIn("--clobber", workflow)

    def test_draft_preparation_does_not_require_publication_approval(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "scripts").mkdir()
            (root / "docs/release-evidence").mkdir(parents=True)
            shutil.copy2(
                source_root / "scripts/release_preflight.sh",
                root / "scripts/release_preflight.sh",
            )
            ci_check = root / "scripts/ci_check.sh"
            ci_check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ci_check.chmod(0o755)
            (root / "VERSION").write_text("1.2.0-alpha.1\n", encoding="utf-8")
            evidence = root / "docs/release-evidence/v1.2.0-alpha.1.md"
            evidence.write_text("# Pending evidence\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "candidate"], cwd=root, check=True)
            candidate = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            evidence.write_text(
                "\n".join((
                    "# Release evidence",
                    f"- Candidate merge commit: `{candidate}`",
                    "- Local release gate: **PASS**",
                    "- GitHub CI: **PASS**",
                    "- Clean installation: **PASS**",
                    "- Live Slack sandbox: **PASS**",
                    "- Release publication approval: **PENDING**",
                    "",
                )),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", str(evidence)], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "record evidence"], cwd=root, check=True)

            preparation = subprocess.run(
                ["./scripts/release_preflight.sh"],
                cwd=root, capture_output=True, text=True,
            )
            publication = subprocess.run(
                ["./scripts/release_preflight.sh", "--publish"],
                cwd=root, capture_output=True, text=True,
            )

            self.assertEqual(preparation.returncode, 0, preparation.stderr)
            self.assertIn("release-candidate preflight passed", preparation.stdout)
            self.assertNotEqual(publication.returncode, 0)
            self.assertIn("publication approval is not PASS", publication.stderr)

    def test_allows_only_evidence_changes_after_the_live_candidate(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "scripts").mkdir()
            (root / "docs/release-evidence").mkdir(parents=True)
            shutil.copy2(
                source_root / "scripts/release_preflight.sh",
                root / "scripts/release_preflight.sh",
            )
            ci_check = root / "scripts/ci_check.sh"
            ci_check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ci_check.chmod(0o755)
            (root / "VERSION").write_text("1.2.0-alpha.1\n", encoding="utf-8")
            (root / "application.txt").write_text("candidate\n", encoding="utf-8")
            evidence = root / "docs/release-evidence/v1.2.0-alpha.1.md"
            evidence.write_text("# Pending evidence\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "candidate"], cwd=root, check=True)
            candidate = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            evidence.write_text(
                "\n".join((
                    "# Release evidence",
                    f"- Candidate merge commit: `{candidate}`",
                    "- Local release gate: **PASS**",
                    "- GitHub CI: **PASS**",
                    "- Clean installation: **PASS**",
                    "- Live Slack sandbox: **PASS**",
                    "- Release publication approval: **PASS**",
                    "",
                )),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", str(evidence)], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "record evidence"], cwd=root, check=True)

            accepted = subprocess.run(
                ["./scripts/release_preflight.sh", "--publish"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

            (root / "application.txt").write_text("changed after validation\n", encoding="utf-8")
            subprocess.run(["git", "add", "application.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "change application"], cwd=root, check=True)
            rejected = subprocess.run(
                ["./scripts/release_preflight.sh", "--publish"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("Only", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
