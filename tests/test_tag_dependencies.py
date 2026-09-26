from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import tag_dependencies as dependencies

ROOT = Path(__file__).resolve().parents[1]


def archive(path, content=b'#!/bin/sh\nprintf "slack v4.8.0\\n"\n'):
    with tarfile.open(path, 'w:gz') as bundle:
        member = tarfile.TarInfo('bin/slack')
        member.size = len(content)
        bundle.addfile(member, io.BytesIO(content))


class DependenciesTests(unittest.TestCase):
    def test_compatibility_checks_version_and_exit_status(self):
        for value, code, expected in [('slack v4.7.0', 0, True), ('slack v4.8.0', 0, True),
                                      ('slack v4.6.0', 0, False), ('slack v5.0.0', 0, False),
                                      ('slack v4.8.0', 1, False), ('unknown', 0, False)]:
            with self.subTest(value=value, code=code), patch.object(dependencies.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], code, value, '')):
                self.assertEqual(dependencies.slack_compatible('/slack'), expected)

    def test_existing_compatible_cli_never_downloaded_or_replaced(self):
        with patch.object(dependencies.shutil, 'which', return_value='/user/bin/slack'), patch.object(
                dependencies, 'slack_compatible', return_value=True), patch.object(dependencies, 'download_verified') as download:
            self.assertEqual(dependencies.ensure_slack(Path('/unused')), Path('/user/bin/slack'))
            download.assert_not_called()

    def test_missing_and_incompatible_cli_all_targets_and_repeat_recovery(self):
        for platform, machine in dependencies.SLACK_ARTIFACTS:
            with self.subTest(platform=platform, machine=machine), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                auth = home / 'credentials.json'
                auth.write_text('keep existing authorization')
                destination = home / 'runtime/slack' / dependencies.SLACK_VERSION / 'slack'
                def compatible(command):
                    return Path(command).is_file() and Path(command).read_bytes().startswith(b'#!/bin/sh')
                def fetch(url, path, digest):
                    target, checksum = dependencies.SLACK_ARTIFACTS[(platform, machine)]
                    self.assertIn(target, url)
                    self.assertEqual(digest, checksum)
                    archive(path)
                with patch.object(dependencies.sys, 'platform', platform), patch.object(dependencies.platform, 'machine', return_value=machine), patch.object(
                        dependencies.shutil, 'which', return_value='/missing/slack'), patch.object(dependencies, 'slack_compatible', side_effect=compatible), patch.object(
                        dependencies, 'download_verified', side_effect=OSError('interrupted')):
                    with self.assertRaises(OSError):
                        dependencies.ensure_slack(home)
                    self.assertFalse(destination.exists())
                with patch.object(dependencies.sys, 'platform', platform), patch.object(dependencies.platform, 'machine', return_value=machine), patch.object(
                        dependencies.shutil, 'which', return_value=None), patch.object(dependencies, 'slack_compatible', side_effect=compatible), patch.object(
                        dependencies, 'download_verified', side_effect=fetch) as download:
                    self.assertEqual(dependencies.ensure_slack(home), destination)
                    self.assertEqual(dependencies.ensure_slack(home), destination)
                    download.assert_called_once()
                    self.assertEqual(auth.read_text(), 'keep existing authorization')

    def test_integrity_failure_and_https_redirect_rejected(self):
        for url, digest, succeeds in [('https://example.com/cli', hashlib.sha256(b'payload').hexdigest(), True),
                                     ('https://example.com/cli', '0' * 64, False), ('http://example.com/cli', '0' * 64, False)]:
            with tempfile.TemporaryDirectory() as tmp:
                response = io.BytesIO(b'payload')
                response.url = url
                with patch.object(dependencies.urllib.request, 'urlopen', return_value=response):
                    if succeeds:
                        dependencies.download_verified('https://example.com/cli', Path(tmp) / 'archive', digest)
                    else:
                        with self.assertRaises(RuntimeError):
                            dependencies.download_verified('https://example.com/cli', Path(tmp) / 'archive', digest)

    def test_failed_version_validation_does_not_publish(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(dependencies.sys, 'platform', 'darwin'), patch.object(dependencies.platform, 'machine', return_value='arm64'), patch.object(dependencies.shutil, 'which', return_value=None), patch.object(
                dependencies, 'slack_compatible', return_value=False), patch.object(dependencies, 'download_verified', side_effect=lambda u, p, d: archive(p)):
            with self.assertRaises(RuntimeError):
                dependencies.ensure_slack(Path(tmp))
            self.assertFalse((Path(tmp) / 'runtime/slack' / dependencies.SLACK_VERSION / 'slack').exists())

    @unittest.skipIf(os.name == "nt", "POSIX runtime migration")
    def test_migration_failure_has_no_checkpoint_and_preserves_active_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = json.dumps({'python': sys.executable, 'bin_dir': str(home / 'bin'), 'release': 'old'})
            (home / 'current.json').write_text(original)
            with patch.object(dependencies, 'prepare_python', side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError):
                    dependencies.migrate(home, ROOT)
            self.assertEqual((home / 'current.json').read_text(), original)
            self.assertFalse((home / 'state/dependencies-v1.json').exists())

    def test_migration_rechecks_slack_and_preserves_operator_environment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PATH': '/user/bin', 'SLACK_CONFIG_DIR': '/user/slack'}):
            home = Path(tmp)
            (home / 'current.json').write_text(json.dumps({'dependency_schema': 1, 'python': sys.executable}))
            with patch.object(dependencies, 'ensure_slack', return_value=Path('/managed/slack')) as ensure:
                dependencies.migrate(home, ROOT)
                dependencies.migrate(home, ROOT)
            self.assertEqual(ensure.call_count, 2)
            self.assertEqual(os.environ['SLACK_CONFIG_DIR'], '/user/slack')
            self.assertEqual(json.loads((home / 'state/dependencies-v1.json').read_text())['version'], 1)


@unittest.skipIf(os.name == "nt", "POSIX bootstrap; Windows uses install.ps1")
class ShellBootstrapTests(unittest.TestCase):
    def test_uv_present_without_usable_system_python_and_spaces(self):
        with tempfile.TemporaryDirectory(prefix='tag bootstrap ') as tmp:
            root = Path(tmp)
            fake = root / 'bin'
            fake.mkdir()
            python = fake / 'managed python'
            python.write_text('#!/bin/sh\nexit 0\n')
            python.chmod(0o755)
            # Deliberately unsupported default python; bootstrap must never invoke it.
            (fake / 'python3').write_text('#!/bin/sh\nexit 99\n')
            (fake / 'python3').chmod(0o755)
            uv = fake / 'uv'
            uv.write_text('#!/bin/sh\ncase "$1" in\n--version) echo "uv 0.12.19";;\npython) printf "%s\\n" "$FAKE_PYTHON";;\nesac\n')
            uv.chmod(0o755)
            result = subprocess.run(['sh', str(ROOT / 'install.sh'), '--runtime-info'], capture_output=True, text=True,
                env=dict(os.environ, PATH=str(fake) + ':/usr/bin:/bin', TAG_HOME=str(root / 'Tag home'), FAKE_PYTHON=str(python)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [str(python), str(uv)])

    def test_uv_absent_without_python_retries_and_reuses_verified_download(self):
        import shutil
        with tempfile.TemporaryDirectory(prefix='tag no python ') as tmp:
            root = Path(tmp)
            fake = root / 'bin'
            fake.mkdir()
            for command in ('awk', 'mkdir', 'mktemp', 'rm', 'tar', 'shasum', 'sha256sum', 'dirname', 'uname', 'mv', 'cp'):
                executable = shutil.which(command)
                if executable:
                    (fake / command).symlink_to(executable)
            python = root / 'managed python'
            python.write_text('#!/bin/sh\nexit 0\n')
            python.chmod(0o755)
            payload = b'#!/bin/sh\ncase "$1" in\n--version) echo "uv 0.12.19";;\npython) printf "%s\\n" "$FAKE_PYTHON";;\nesac\n'
            archive_path = root / 'uv.tar.gz'
            arch = 'aarch64' if dependencies.platform.machine() in {'arm64', 'aarch64'} else 'x86_64'
            machine = arch + ('-apple-darwin' if sys.platform == 'darwin' else '-unknown-linux-musl')
            with tarfile.open(archive_path, 'w:gz') as bundle:
                member = tarfile.TarInfo('uv-' + machine + '/uv')
                member.mode = 0o755
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
            digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            installer = root / 'install.sh'
            source = (ROOT / 'install.sh').read_text()
            # The fixture uses a locally generated archive and matching trusted pin.
            source = __import__('re').sub(r'tag_sha=[a-f0-9]{64}', 'tag_sha=' + digest, source)
            installer.write_text(source)
            curl = fake / 'curl'
            curl.write_text('#!/bin/sh\necho download >> "$DOWNLOAD_LOG"\n[ "${FAIL_DOWNLOAD:-}" != 1 ] || exit 22\nwhile [ "$1" != -o ]; do shift; done\ncp "$ARCHIVE" "$2"\n')
            curl.chmod(0o755)
            env = dict(os.environ, PATH=str(fake), TAG_HOME=str(root / 'Tag home'),
                       FAKE_PYTHON=str(python), ARCHIVE=str(archive_path), DOWNLOAD_LOG=str(root / 'downloads'))
            def run(**extra):
                return subprocess.run(['/bin/sh', str(installer), '--runtime-info'], env=dict(env, **extra), capture_output=True, text=True)
            failed = run(FAIL_DOWNLOAD='1')
            self.assertNotEqual(failed.returncode, 0)
            corrupt = root / 'corrupt.tar.gz'
            corrupt.write_bytes(b'partial download')
            rejected = run(ARCHIVE=str(corrupt))
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('checksum mismatch', rejected.stderr)
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[0], str(python))
            again = run()
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual((root / 'downloads').read_text().splitlines(), ['download', 'download', 'download'])

    def test_source_dependency_failure_keeps_existing_environment_selected(self):
        import shutil
        with tempfile.TemporaryDirectory(prefix='source recovery ') as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            shutil.copy2(ROOT / 'scripts/install_dependencies.sh', root / 'scripts/install_dependencies.sh')
            shutil.copy2(ROOT / 'requirements-runtime.txt', root / 'requirements-runtime.txt')
            active = root / '.venv'
            active.mkdir()
            (active / 'keep').write_text('existing environment')
            uv = root / 'uv'
            uv.write_text('#!/bin/sh\n[ "$1" = venv ] && exit 0\nexit 29\n')
            uv.chmod(0o755)
            result = subprocess.run(['/bin/sh', str(root / 'scripts/install_dependencies.sh'), '--dependencies-only'],
                env=dict(os.environ, TAG_BOOTSTRAP_PYTHON=sys.executable, TAG_BOOTSTRAP_UV=str(uv)), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(active.is_symlink())
            self.assertEqual((active / 'keep').read_text(), 'existing environment')
