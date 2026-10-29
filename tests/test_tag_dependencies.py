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
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import tag_dependencies as dependencies

ROOT = Path(__file__).resolve().parents[1]


def archive(path, content=b'#!/bin/sh\nprintf "slack v4.8.0\\n"\n'):
    if str(path).endswith('.zip'):
        # Slack's Windows release: bin/slack.exe beside a LICENSE.
        with zipfile.ZipFile(path, 'w') as bundle:
            bundle.writestr('LICENSE', 'license')
            bundle.writestr('bin/slack.exe', content)
        return
    with tarfile.open(path, 'w:gz') as bundle:
        member = tarfile.TarInfo('bin/slack')
        member.size = len(content)
        bundle.addfile(member, io.BytesIO(content))


class DependenciesTests(unittest.TestCase):
    def test_source_start_restores_private_slack_in_fresh_process_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            command = home / "runtime/slack" / dependencies.SLACK_VERSION / "slack"
            command.parent.mkdir(parents=True)
            for _ in range(2):
                with patch.dict(os.environ, {"PATH": "/usr/bin", "SLACK_CONFIG_DIR": "/existing/auth"}), patch.object(
                    dependencies, "ensure_slack", return_value=command
                ) as ensure:
                    dependencies.migrate(home, ROOT)
                    ensure.assert_called_once_with(home)
                    self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(command.parent))
                    self.assertEqual(os.environ["SLACK_CONFIG_DIR"], "/existing/auth")
            self.assertFalse((home / "current.json").exists())

    def test_source_start_retries_interrupted_private_slack_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "runtime/slack/old-version").mkdir(parents=True)
            command = home / "runtime/slack" / dependencies.SLACK_VERSION / "slack"
            with patch.dict(os.environ, {"PATH": "/usr/bin"}), patch.object(
                dependencies, "ensure_slack", side_effect=[RuntimeError("offline"), command]
            ) as ensure:
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    dependencies.migrate(home, ROOT)
                self.assertEqual(os.environ["PATH"], "/usr/bin")
                dependencies.migrate(home, ROOT)
                self.assertEqual(ensure.call_count, 2)
                self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(command.parent))

    def test_fresh_source_checkout_defers_slack_installation_to_setup(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(dependencies, "ensure_slack") as ensure:
            dependencies.migrate(Path(tmp), ROOT)
            ensure.assert_not_called()

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
            self.assertEqual(dependencies.ensure_slack(Path('/unused')), Path('/user/bin/slack').resolve())
            download.assert_not_called()

    def test_missing_and_incompatible_cli_all_targets_and_repeat_recovery(self):
        for platform, machine in dependencies.SLACK_ARTIFACTS:
            with self.subTest(platform=platform, machine=machine), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                auth = home / 'credentials.json'
                auth.write_text('keep existing authorization', encoding="utf-8")
                name = 'slack.exe' if platform == 'win32' else 'slack'
                destination = home / 'runtime/slack' / dependencies.SLACK_VERSION / name
                def compatible(command):
                    return Path(command).is_file() and Path(command).read_bytes().startswith(b'#!/bin/sh')
                def fetch(url, path, digest):
                    target, checksum = dependencies.SLACK_ARTIFACTS[(platform, machine)]
                    self.assertIn(target, url)
                    self.assertTrue(url.endswith('.zip' if platform == 'win32' else '.tar.gz'))
                    self.assertEqual(digest, checksum)
                    archive(path)
                with patch.object(dependencies.sys, 'platform', platform), patch.object(dependencies.platform, 'machine', return_value=machine), patch.object(
                        dependencies.shutil, 'which', return_value='/missing/slack'), patch.object(dependencies, 'slack_problem', side_effect=lambda c: None if compatible(c) else 'incompatible'), patch.object(
                        dependencies, 'download_verified', side_effect=OSError('interrupted')):
                    with self.assertRaises(OSError):
                        dependencies.ensure_slack(home)
                    self.assertFalse(destination.exists())
                with patch.object(dependencies.sys, 'platform', platform), patch.object(dependencies.platform, 'machine', return_value=machine), patch.object(
                        dependencies.shutil, 'which', return_value=None), patch.object(dependencies, 'slack_problem', side_effect=lambda c: None if compatible(c) else 'incompatible'), patch.object(
                        dependencies, 'download_verified', side_effect=fetch) as download:
                    self.assertEqual(dependencies.ensure_slack(home), destination)
                    self.assertEqual(dependencies.ensure_slack(home), destination)
                    download.assert_called_once()
                    self.assertEqual(auth.read_text(encoding="utf-8"), 'keep existing authorization')

    def test_windows_on_arm_uses_the_emulated_x64_slack_cli(self):
        # Slack ships no Windows ARM64 CLI; Windows 11 on ARM runs the x64 one.
        self.assertEqual(dependencies.SLACK_ARTIFACTS[('win32', 'arm64')],
                         dependencies.SLACK_ARTIFACTS[('win32', 'x86_64')])
        with tempfile.TemporaryDirectory() as tmp, patch.object(dependencies.sys, 'platform', 'win32'), \
                patch.object(dependencies.platform, 'machine', return_value='ARM64'), \
                patch.object(dependencies.shutil, 'which', return_value=None), \
                patch.object(dependencies, 'slack_problem', side_effect=lambda c: None if Path(c).is_file() else 'missing'), \
                patch.object(dependencies, 'download_verified', side_effect=lambda u, p, d: archive(p)) as download:
            destination = dependencies.ensure_slack(Path(tmp))
        self.assertEqual(destination.name, 'slack.exe')
        self.assertIn('windows_64-bit', download.call_args.args[0])

    def test_publish_waits_for_windows_to_release_the_checked_cli(self):
        # Windows briefly holds a just-run slack.exe open (WinError 32).
        real_replace = os.replace
        calls = []
        def replace(source, target):
            calls.append(source)
            if len(calls) < 3:
                raise PermissionError(32, 'The process cannot access the file')
            real_replace(source, target)
        with tempfile.TemporaryDirectory() as tmp, patch.object(dependencies.os, 'replace', side_effect=replace), \
                patch.object(dependencies.time, 'sleep') as sleep:
            candidate, destination = Path(tmp) / 'candidate.exe', Path(tmp) / 'slack.exe'
            candidate.write_bytes(b'cli')
            dependencies.publish(candidate, destination)
            self.assertEqual(destination.read_bytes(), b'cli')
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_publish_gives_up_when_the_cli_stays_locked(self):
        with patch.object(dependencies.os, 'replace', side_effect=PermissionError(32, 'locked')), \
                patch.object(dependencies.time, 'sleep'):
            with self.assertRaises(PermissionError):
                dependencies.publish(Path('candidate'), Path('destination'), attempts=3)

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

    def test_failed_version_validation_does_not_publish_and_says_why(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(dependencies.sys, 'platform', 'darwin'), patch.object(dependencies.platform, 'machine', return_value='arm64'), patch.object(dependencies.shutil, 'which', return_value=None), patch.object(
                dependencies, 'slack_problem', return_value='it did not finish `slack version` within 20 seconds'), patch.object(dependencies, 'download_verified', side_effect=lambda u, p, d: archive(p)):
            with self.assertRaisesRegex(RuntimeError, 'cannot run on this machine: it did not finish `slack version` within 20 seconds'):
                dependencies.ensure_slack(Path(tmp))
            self.assertFalse((Path(tmp) / 'runtime/slack' / dependencies.SLACK_VERSION / 'slack').exists())

    def test_version_check_names_why_the_cli_failed(self):
        for outcome, reason in [
                (subprocess.TimeoutExpired(['slack'], 20), 'did not finish `slack version` within 20 seconds'),
                (FileNotFoundError(2, 'The system cannot find the file specified'), 'could not start (The system cannot find the file specified)'),
                (subprocess.CompletedProcess([], 3221225477, '', ''), 'exited with code 3221225477'),
                (subprocess.CompletedProcess([], 0, 'garbage', ''), "printed no version: 'garbage'"),
                (subprocess.CompletedProcess([], 0, 'Using slack.exe v4.6.0', ''), 'version 4.6.0, not 4.7+'),
                (subprocess.CompletedProcess([], 0, 'Using slack.exe v4.8.0\n', ''), None)]:
            with self.subTest(reason=reason), patch.object(dependencies.subprocess, 'run', side_effect=[outcome]):
                problem = dependencies.slack_problem('/slack')
                if reason is None:
                    self.assertIsNone(problem)
                else:
                    self.assertIn(reason, problem)

    def test_slack_cli_runs_in_a_console_host_only_on_windows_on_arm(self):
        # In a windowless console (as in Tag.app) the CLI waits forever for a terminal
        # reply; with no console, each hook it runs opens a console window.
        for platform, machine, expected in [('win32', 'ARM64', True), ('win32', 'AMD64', False),
                                            ('linux', 'aarch64', False), ('darwin', 'arm64', False)]:
            with self.subTest(platform=platform, machine=machine), patch.object(dependencies.sys, 'platform', platform),                     patch.object(dependencies.platform, 'machine', return_value=machine):
                self.assertEqual(dependencies.slack_console_hosted(), expected)

    def test_hosted_slack_cli_runs_windowless_through_the_base_interpreter(self):
        done = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(dependencies, 'slack_console_hosted', return_value=True),                 patch.object(dependencies.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True),                 patch.object(dependencies.sys, '_base_executable', '/base/python', create=True),                 patch.object(dependencies.subprocess, 'run', return_value=done) as run:
            dependencies.run_slack([Path('/slack'), 'auth', 'list'], capture_output=True, timeout=30)
        self.assertEqual(run.call_args.args[0],
                         ['/base/python', '-I', str(ROOT / 'scripts/slack_console_host.py'), str(Path('/slack')), 'auth', 'list'])
        self.assertEqual(run.call_args.kwargs, {'capture_output': True, 'timeout': 30, 'creationflags': 0x08000000})
        with patch.object(dependencies, 'slack_console_hosted', return_value=False),                 patch.object(dependencies.subprocess, 'run', return_value=done) as run:
            dependencies.run_slack(['/slack', 'version'], timeout=20)
        self.assertEqual(run.call_args.args[0], ['/slack', 'version'])
        self.assertEqual(run.call_args.kwargs, {'timeout': 20})

    def test_version_check_runs_through_run_slack_with_no_stdin(self):
        with patch.object(dependencies, 'run_slack', return_value=subprocess.CompletedProcess([], 0, 'v4.8.0', '')) as run:
            self.assertTrue(dependencies.slack_compatible('/slack'))
        self.assertEqual(run.call_args.args[0][1:], ['version', '--skip-update', '--no-color'])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_every_captured_slack_cli_call_uses_run_slack(self):
        # Otherwise setup in Tag.app, upgrades and reset hang at the next Slack CLI
        # call on Windows on ARM, or the CLI's hooks open console windows.
        from scripts import opentag_setup, slack_app_create, slack_credentials, slack_manifest_migrations, tag_reset
        done = subprocess.CompletedProcess([], 0, 'Using slack.exe v4.8.0', '')
        host = str(ROOT / 'scripts/slack_console_host.py')
        hosted = (patch.object(dependencies, 'slack_console_hosted', return_value=True),
                  patch.object(dependencies.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True))
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / '.slack').mkdir()
            (project / '.slack/apps.json').write_text('{}', encoding='utf-8')
            calls = {
                'auth list': lambda: opentag_setup.list_slack_sign_ins(),
                'auth.test': lambda: opentag_setup.auth_test_member('T123'),
                'version': lambda: opentag_setup.slack_cli_supports_icon_upload(),
                'manifest info': lambda: opentag_setup.remote_app_settings(project, 'A123'),
                'run_slack_cli': lambda: opentag_setup.run_slack_cli(['app', 'install']),
                'app list': lambda: opentag_setup.slack_cli_apps(project, 'T123'),
                'is_installed': lambda: slack_app_create.is_installed(project, 'T123', 'A123'),
                'manifest sync': lambda: slack_manifest_migrations._run(['slack', 'manifest'], cwd=project),
            }
            for name, call in calls.items():
                with self.subTest(call=name), hosted[0], hosted[1],                         patch.object(opentag_setup.ui, 'message'), patch('subprocess.run', return_value=done) as run:
                    call()
                    self.assertTrue(run.called)
                    for invocation in run.call_args_list:
                        self.assertEqual(invocation.args[0][2], host)
                        self.assertEqual(invocation.kwargs.get('creationflags'), 0x08000000)
            with hosted[0], hosted[1], patch.object(opentag_setup.ui, 'protocol_active', return_value=False),                     patch('subprocess.run', return_value=done) as run:
                opentag_setup.run_slack_cli(['auth', 'login'], interactive=True)
            # A terminal sign-in keeps the operator's console for Slack CLI prompts.
            self.assertNotIn('creationflags', run.call_args.kwargs)
            self.assertNotIn(host, run.call_args.args[0])
        for module in (slack_credentials, tag_reset):
            self.assertIn('tag_dependencies.run_slack(', Path(module.__file__).read_text(encoding='utf-8'))

    def test_background_processes_get_a_windowless_console_not_none(self):
        # A process with no console (DETACHED_PROCESS) gives every console program it
        # starts a new, visible console window: the venv's real Python, git, the CLIs.
        offenders = [path.name for path in sorted((ROOT / 'scripts').glob('*.py'))
                     if 'DETACHED_PROCESS' in path.read_text(encoding='utf-8')]
        self.assertEqual(offenders, [])
        for name in ('tag_cli', 'tag_autostart', 'tag_telemetry', 'tag_model_download', 'tag_install', 'model_catalog'):
            with self.subTest(module=name):
                self.assertIn('CREATE_NO_WINDOW', (ROOT / f'scripts/{name}.py').read_text(encoding='utf-8'))

    @unittest.skipIf(os.name == "nt", "POSIX runtime migration")
    def test_migration_failure_has_no_checkpoint_and_preserves_active_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = json.dumps({'python': sys.executable, 'bin_dir': str(home / 'bin'), 'release': 'old'})
            (home / 'current.json').write_text(original, encoding="utf-8")
            with patch.object(dependencies, 'prepare_python', side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError):
                    dependencies.migrate(home, ROOT)
            self.assertEqual((home / 'current.json').read_text(encoding="utf-8"), original)
            self.assertFalse((home / 'state/dependencies-v1.json').exists())

    def test_older_windows_installs_move_to_the_private_python(self):
        # Before dependency_schema 1, Windows ran on a system Python. The migration
        # prepares Tag's own Python through install.ps1, then reinstalls with it.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / 'current.json').write_text(json.dumps(
                {'python': 'C:/Python312/python.exe', 'bin_dir': str(home / 'bin'), 'release': 'old'}), encoding="utf-8")
            runs = []
            def run(command, **_):
                runs.append(command)
                if '--migrate-dependencies' in command:
                    (home / 'current.json').write_text(json.dumps(
                        {'dependency_schema': 1, 'python': 'managed', 'release': 'new'}), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout='C:/Tag/python.exe\nC:/Tag/uv.exe\n')
            with patch.object(dependencies.sys, 'platform', 'win32'), \
                    patch.object(dependencies.subprocess, 'run', side_effect=run), \
                    patch.object(dependencies.subprocess, 'call', return_value=3) as rerun, \
                    patch.object(dependencies.os, 'execv') as execv:
                # Startup reruns on the new code, and its real result is this process's result.
                with self.assertRaises(SystemExit) as exit_:
                    dependencies.migrate(home, ROOT)
            self.assertEqual(exit_.exception.code, 3)
            self.assertEqual(runs[0][0], 'powershell.exe')
            self.assertIn('-RuntimeInfo', runs[0])
            self.assertEqual(Path(runs[1][0]), Path('C:/Tag/python.exe'))
            self.assertEqual(rerun.call_args.args[0][0], 'managed')
            execv.assert_not_called()  # Windows execv would report success before the start finished

    def test_runtime_info_paths_survive_a_non_ascii_tag_home(self):
        # Windows PowerShell's console code page garbles non-ASCII paths, so
        # install.ps1 writes UTF-8 bytes and prepare_python must decode UTF-8.
        script = (ROOT / 'install.ps1').read_text(encoding='utf-8')
        info = script[script.index('if ($RuntimeInfo) {'):script.index('if ($DependenciesOnly) {')]
        self.assertIn('[Console]::OpenStandardOutput()', info)
        self.assertNotIn('Write-Output', info)
        home = Path('C:/Users/Zoë/AppData/Local/Tag')
        def run(command, **options):
            self.assertEqual(options.get('encoding'), 'utf-8')
            self.assertNotIn('text', options)
            return subprocess.CompletedProcess(command, 0, stdout=f'{home}/python.exe\n{home}/uv.exe\n')
        with patch.object(dependencies.sys, 'platform', 'win32'), \
                patch.object(dependencies.subprocess, 'run', side_effect=run):
            python, uv = dependencies.prepare_python(ROOT, home)
        self.assertEqual(python, home / 'python.exe')
        self.assertEqual(uv, home / 'uv.exe')

    def test_migration_rechecks_slack_and_preserves_operator_environment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PATH': '/user/bin', 'SLACK_CONFIG_DIR': '/user/slack'}):
            home = Path(tmp)
            (home / 'current.json').write_text(json.dumps({'dependency_schema': 1, 'python': sys.executable}), encoding="utf-8")
            with patch.object(dependencies, 'ensure_slack', return_value=Path('/managed/slack')) as ensure:
                dependencies.migrate(home, ROOT)
                dependencies.migrate(home, ROOT)
            self.assertEqual(ensure.call_count, 2)
            self.assertEqual(os.environ['SLACK_CONFIG_DIR'], '/user/slack')
            self.assertEqual(json.loads((home / 'state/dependencies-v1.json').read_text(encoding="utf-8"))['version'], 1)


@unittest.skipIf(os.name == "nt", "POSIX bootstrap; Windows uses install.ps1")
class ShellBootstrapTests(unittest.TestCase):
    def test_uv_present_without_usable_system_python_and_spaces(self):
        with tempfile.TemporaryDirectory(prefix='tag bootstrap ') as tmp:
            root = Path(tmp)
            fake = root / 'bin'
            fake.mkdir()
            python = fake / 'managed python'
            python.write_text('#!/bin/sh\nexit 0\n', encoding="utf-8")
            python.chmod(0o755)
            # Deliberately unsupported default python; bootstrap must never invoke it.
            (fake / 'python3').write_text('#!/bin/sh\nexit 99\n', encoding="utf-8")
            (fake / 'python3').chmod(0o755)
            uv = fake / 'uv'
            uv.write_text('#!/bin/sh\ncase "$1" in\n--version) echo "uv 0.12.19";;\npython) printf "%s\\n" "$FAKE_PYTHON";;\nesac\n', encoding="utf-8")
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
            for command in ('awk', 'mkdir', 'mktemp', 'rm', 'tar', 'gzip', 'shasum', 'sha256sum', 'dirname', 'uname', 'mv', 'cp'):
                executable = shutil.which(command)
                if executable:
                    (fake / command).symlink_to(executable)
            python = root / 'managed python'
            python.write_text('#!/bin/sh\nexit 0\n', encoding="utf-8")
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
            source = (ROOT / 'install.sh').read_text(encoding="utf-8")
            # The fixture uses a locally generated archive and matching trusted pin.
            source = __import__('re').sub(r'tag_sha=[a-f0-9]{64}', 'tag_sha=' + digest, source)
            installer.write_text(source, encoding="utf-8")
            curl = fake / 'curl'
            curl.write_text('#!/bin/sh\necho download >> "$DOWNLOAD_LOG"\n[ "${FAIL_DOWNLOAD:-}" != 1 ] || exit 22\nwhile [ "$1" != -o ]; do shift; done\ncp "$ARCHIVE" "$2"\n', encoding="utf-8")
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
            self.assertEqual((root / 'downloads').read_text(encoding="utf-8").splitlines(), ['download', 'download', 'download'])

    def test_source_dependency_failure_keeps_existing_environment_selected(self):
        import shutil
        with tempfile.TemporaryDirectory(prefix='source recovery ') as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            shutil.copy2(ROOT / 'scripts/install_dependencies.sh', root / 'scripts/install_dependencies.sh')
            shutil.copy2(ROOT / 'requirements-runtime.txt', root / 'requirements-runtime.txt')
            active = root / '.venv'
            active.mkdir()
            (active / 'keep').write_text('existing environment', encoding="utf-8")
            uv = root / 'uv'
            uv.write_text('#!/bin/sh\n[ "$1" = venv ] && exit 0\nexit 29\n', encoding="utf-8")
            uv.chmod(0o755)
            result = subprocess.run(['/bin/sh', str(root / 'scripts/install_dependencies.sh'), '--dependencies-only'],
                env=dict(os.environ, TAG_BOOTSTRAP_PYTHON=sys.executable, TAG_BOOTSTRAP_UV=str(uv)), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(active.is_symlink())
            self.assertEqual((active / 'keep').read_text(encoding="utf-8"), 'existing environment')
