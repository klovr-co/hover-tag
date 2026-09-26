from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import tag_instances, tag_layout


class LayoutMigrationTests(unittest.TestCase):
    def test_legacy_settings_integrations_state_and_workspace_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'Tag'
            (root / 'config').mkdir(parents=True)
            connector = root / 'integrations/mfs/slack.toml'
            connector.parent.mkdir(parents=True)
            connector.write_text('token = "env:MFS_SLACK_TOKEN"\n')
            original = {'SLACK_TEAM_ID': 'TOLD', 'MFS_SLACK_CONNECTOR_CONFIG': str(connector)}
            (root / 'config/settings.json').write_text(json.dumps(original))
            (root / 'workspace/.agents/skills/custom').mkdir(parents=True)
            (root / 'workspace/.agents/skills/custom/SKILL.md').write_text('custom skill')
            (root / 'state').mkdir()
            (root / 'state/slack-user-settings.json').write_text('{"model": "custom"}')
            (root / 'state/mfs.json').write_text('{"pid": 123}')
            context = tag_instances.ensure_default(root)
            lifecycle = Mock()
            self.assertTrue(tag_layout.migrate(context, lifecycle))
            saved = json.loads((context.home / 'config/settings.json').read_text())
            self.assertEqual(str(context.home / 'integrations/mfs/slack.toml'), saved['MFS_SLACK_CONNECTOR_CONFIG'])
            self.assertEqual(original, json.loads((root / 'config/settings.json').read_text()))
            self.assertEqual('custom skill', (context.workspace / '.agents/skills/custom/SKILL.md').read_text())
            self.assertTrue((context.home / 'state/slack-user-settings.json').is_file())
            self.assertFalse((context.home / 'state/mfs.json').exists())
            self.assertTrue((root / 'state/mfs.json').exists())
            lifecycle.stop_process.assert_any_call(root, 'slack')
            # Subsequent edits must not be overwritten by preserved legacy files.
            (context.home / 'config/settings.json').write_text('{"SLACK_TEAM_ID": "TNEW"}')
            self.assertFalse(tag_layout.migrate(context, lifecycle))
            self.assertEqual('TNEW', json.loads((context.home / 'config/settings.json').read_text())['SLACK_TEAM_ID'])

    def test_named_workspace_relocation_copies_custom_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory)
            root = user / 'Library/Application Support/Tag'
            with patch('pathlib.Path.home', return_value=user), patch('sys.platform', 'darwin'):
                context = tag_instances.create(root, 'work')
                old = context.home / 'workspace'
                (old / '.codex').mkdir(parents=True)
                (old / '.codex/config.toml').write_text('model = "custom"\n')
                (old / 'project.txt').write_text('work')
                (old / 'Cargo.lock').write_text('dependency lock')
                self.assertTrue(tag_layout.migrate(context, Mock()))
                self.assertEqual('model = "custom"\n', (context.workspace / '.codex/config.toml').read_text())
                self.assertEqual('work', (context.workspace / 'project.txt').read_text())
                self.assertTrue((old / 'project.txt').exists())
                self.assertEqual('dependency lock', (context.workspace / 'Cargo.lock').read_text())

    def test_historical_workspace_placeholder_does_not_block_relocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'Tag'
            context = tag_instances.ensure_default(root)
            historical = '# TAG-only Codex MCP servers go here: [mcp_servers.NAME]\n'
            current = (
                '# TAG-only Codex defaults and MCP servers go here.\n'
                '# model = "gpt-example"\n'
                '# model_reasoning_effort = "high"\n'
                '# service_tier = "default"\n'
                '# [mcp_servers.NAME]\n'
            )
            (context.workspace / '.codex/config.toml').write_text(historical)
            source = root / 'workspace/.codex/config.toml'
            source.parent.mkdir(parents=True)
            source.write_text(current)

            self.assertTrue(tag_layout.migrate(context, Mock()))
            self.assertEqual(current, (context.workspace / '.codex/config.toml').read_text())

    def test_custom_workspace_codex_config_still_blocks_relocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'Tag'
            context = tag_instances.ensure_default(root)
            custom = 'model = "custom"\n'
            destination = context.workspace / '.codex/config.toml'
            destination.write_text(custom)
            source = root / 'workspace/.codex/config.toml'
            source.parent.mkdir(parents=True)
            source.write_text('# TAG-only Codex MCP servers go here: [mcp_servers.NAME]\n')

            with self.assertRaisesRegex(RuntimeError, 'conflict'):
                tag_layout.migrate(context, Mock())
            self.assertEqual(custom, destination.read_text())

    def test_conflicting_files_are_preserved_and_retry_can_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = tag_instances.ensure_default(root)
            (root / 'config').mkdir()
            (root / 'config/settings.json').write_text('{"SLACK_TEAM_ID":"TOLD"}')
            target = context.home / 'config/settings.json'
            target.write_text('{"SLACK_TEAM_ID":"TNEW"}')
            marker = context.home / 'state/layout-migrations.json'
            with self.assertRaisesRegex(RuntimeError, 'conflict'):
                tag_layout.migrate(context, Mock())
            self.assertFalse(marker.exists())
            self.assertEqual('TNEW', json.loads(target.read_text())['SLACK_TEAM_ID'])
            target.rename(target.with_suffix('.saved'))
            self.assertTrue(tag_layout.migrate(context, Mock()))
            self.assertTrue(marker.exists())

    def test_interrupted_copy_is_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = tag_instances.ensure_default(root)
            (root / 'config').mkdir()
            (root / 'config/settings.json').write_text('{"SLACK_TEAM_ID":"TOLD"}')
            copy = tag_layout._copy
            def interrupt(source, destination, mappings=()):
                copy(source, destination, mappings)
                if source.name == 'settings.json':
                    raise OSError('interrupted after copy')
            with patch.object(tag_layout, '_copy', side_effect=interrupt), self.assertRaises(OSError):
                tag_layout.migrate(context, Mock())
            self.assertFalse((context.home / 'state/layout-migrations.json').exists())
            self.assertTrue(tag_layout.migrate(context, Mock()))

    def test_cannot_copy_through_destination_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target, outside = root / 'source', root / 'target', root / 'outside'
            source.mkdir(); outside.mkdir()
            (source / 'secret').write_text('private')
            target.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'symlink'):
                tag_layout._copy(source, target)
            self.assertFalse((outside / 'secret').exists())


class SelfContainedHomeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.user = Path(temporary.name)
        self.root = self.user / 'Library/Application Support/Tag'
        self.home_patch = patch('pathlib.Path.home', return_value=self.user)
        self.platform_patch = patch('sys.platform', 'darwin')
        self.home_patch.start()
        self.platform_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.addCleanup(self.platform_patch.stop)

    def legacy(self, name='default'):
        home = self.root / 'instances' / name
        (home / 'config').mkdir(parents=True)
        (home / 'state').mkdir()
        (home / 'instance.json').write_text(json.dumps({'schema_version': 1, 'id': name}))
        connector = home / 'integrations/mfs/slack.toml'
        connector.parent.mkdir(parents=True)
        credential = home / 'config/slack-history-token'
        credential.write_text('test-credential')
        connector.write_text('token = ' + json.dumps('file:' + str(credential)) + '\n')
        (connector.parent / 'Cargo.lock').write_text('dependency lock')
        (home / 'config/settings.json').write_text(json.dumps({
            'SLACK_TEAM_ID': 'TOLD', 'MFS_SLACK_CONNECTOR_CONFIG': str(connector),
            'MFS_SLACK_CONNECTOR_URI': 'slack://unchanged/root',
            'OPENTAG_CUSTOM_SETTING': '/some/unrelated/path',
        }))
        (home / 'state/slack-active-sessions.json').write_text('{"thread": "session"}')
        return tag_instances.resolve(self.root, name)

    def test_native_default_and_named_data_move_with_credentials_and_history(self):
        for name in ('default', 'work'):
            with self.subTest(name=name):
                context = self.legacy(name)
                workspace = self.user / 'Tag' / name
                workspace.mkdir(parents=True)
                (workspace / 'notes.md').write_text('my files')
                lifecycle = Mock()
                self.assertTrue(tag_layout.migrate(context, lifecycle))
                migrated = tag_instances.resolve(self.root, name)
                self.assertEqual(migrated.home, workspace / '.tag')
                self.assertEqual(migrated.workspace, workspace)
                values = json.loads((migrated.home / 'config/settings.json').read_text())
                self.assertEqual(values['MFS_SLACK_CONNECTOR_URI'], 'slack://unchanged/root')
                self.assertEqual((migrated.home / 'integrations/mfs/Cargo.lock').read_text(), 'dependency lock')
                self.assertEqual(values['OPENTAG_CUSTOM_SETTING'], '/some/unrelated/path')
                self.assertEqual(values['MFS_SLACK_CONNECTOR_CONFIG'], str(migrated.home / 'integrations/mfs/slack.toml'))
                self.assertIn(str(migrated.home / 'config/slack-history-token'),
                              Path(values['MFS_SLACK_CONNECTOR_CONFIG']).read_text())
                self.assertEqual(json.loads((migrated.home / 'state/slack-active-sessions.json').read_text()), {'thread': 'session'})
                self.assertTrue((context.home / 'config/settings.json').exists())
                self.assertEqual((workspace / 'notes.md').read_text(), 'my files')
                lifecycle.stop_process.assert_any_call(context.home, 'slack')
                self.assertEqual((migrated.home / 'config/slack-history-token').stat().st_mode & 0o777, 0o600)
                self.assertFalse(tag_layout.migrate(migrated, lifecycle))
                records = [item for item in tag_instances.discover(self.root) if item['id'] == name]
                self.assertEqual(len(records), 1)
                self.assertTrue(records[0]['valid'])
                self.assertEqual(records[0]['home'], str(migrated.home))

    def test_interrupted_home_copy_keeps_old_authority_and_retries(self):
        context = self.legacy()
        target = self.user / 'Tag/default/.tag'
        copy = tag_layout._copy
        def interrupt(source, destination, mappings=()):
            copy(source, destination, mappings)
            if source.name == 'settings.json':
                raise OSError('interrupted')
        with patch.object(tag_layout, '_copy', side_effect=interrupt), self.assertRaises(OSError):
            tag_layout.migrate(context, Mock())
        self.assertFalse((target / 'state/home-migration.json').exists())
        self.assertEqual(tag_instances.resolve(self.root).home, context.home)
        self.assertTrue(tag_layout.migrate(context, Mock()))
        self.assertEqual(tag_instances.resolve(self.root).home, target)
        (target / 'config/settings.json').write_text('{"SLACK_TEAM_ID": "TNEW"}')
        self.assertFalse(tag_layout.migrate(tag_instances.resolve(self.root), Mock()))
        self.assertEqual(json.loads((target / 'config/settings.json').read_text())['SLACK_TEAM_ID'], 'TNEW')

    def test_conflict_never_commits_and_preserves_both_settings(self):
        context = self.legacy()
        target = self.user / 'Tag/default/.tag'
        (target / 'config').mkdir(parents=True)
        settings = target / 'config/settings.json'
        settings.write_text('{"SLACK_TEAM_ID": "TNEW"}')
        with self.assertRaisesRegex(RuntimeError, 'conflict'):
            tag_layout.migrate(context, Mock())
        self.assertFalse((target / 'state/home-migration.json').exists())
        self.assertEqual(tag_instances.resolve(self.root).home, context.home)
        self.assertEqual(json.loads(settings.read_text())['SLACK_TEAM_ID'], 'TNEW')
        settings.rename(settings.with_suffix('.saved'))
        self.assertTrue(tag_layout.migrate(context, Mock()))

    def test_legacy_root_and_workspace_migrate_all_the_way_in_one_start(self):
        (self.root / 'config').mkdir(parents=True)
        (self.root / 'config/settings.json').write_text('{"SLACK_TEAM_ID": "TOLD"}')
        (self.root / 'workspace').mkdir()
        (self.root / 'workspace/notes.md').write_text('original')
        context = tag_instances.ensure_default(self.root)
        self.assertTrue(tag_layout.migrate(context, Mock()))
        migrated = tag_instances.resolve(self.root)
        self.assertEqual(migrated.home, self.user / 'Tag/default/.tag')
        self.assertEqual((migrated.workspace / 'notes.md').read_text(), 'original')
        self.assertTrue((migrated.home / 'config/settings.json').is_file())

    def test_failed_stop_and_settings_lock_leave_old_home_authoritative(self):
        context = self.legacy()
        lifecycle = Mock()
        lifecycle.stop_process.side_effect = RuntimeError('could not stop')
        with self.assertRaisesRegex(RuntimeError, 'could not stop'):
            tag_layout.migrate(context, lifecycle)
        self.assertEqual(tag_instances.resolve(self.root).home, context.home)
        lock = context.home / 'config/settings.json.lock'
        lock.touch()
        with self.assertRaises(FileExistsError):
            tag_layout.migrate(context, Mock())
        self.assertTrue(lock.exists())
        self.assertEqual(tag_instances.resolve(self.root).home, context.home)
        lock.unlink()
        self.assertTrue(tag_layout.migrate(context, Mock()))

    def test_previously_migrated_workspace_does_not_restore_stale_files(self):
        context = self.legacy()
        (context.home / 'state/layout-migrations.json').write_text('{"version": 1}')
        old = context.home / 'workspace'
        old.mkdir()
        (old / 'notes.md').write_text('stale')
        workspace = self.user / 'Tag/default'
        workspace.mkdir(parents=True)
        (workspace / 'notes.md').write_text('current')
        self.assertTrue(tag_layout.migrate(context, Mock()))
        self.assertEqual((workspace / 'notes.md').read_text(), 'current')
        self.assertFalse((workspace / '.tag/workspace').exists())

    def test_start_resolves_migrated_home_before_readiness_and_loads_new_paths(self):
        import os
        import sys
        from scripts import tag_cli
        context = self.legacy()
        target = self.user / 'Tag/default/.tag'
        def dependencies():
            self.assertEqual(os.environ['TAG_INSTANCE_HOME'], str(target))
            self.assertEqual(os.environ['OPENTAG_WORKDIR'], str(target.parent))
            self.assertEqual(os.environ['OPENTAG_ENV_FILE'], str(target / 'config/settings.json'))
            self.assertEqual(tag_instances.resolve(self.root).home, target)
            values = tag_cli.read_config(Path(os.environ['OPENTAG_ENV_FILE']))
            self.assertEqual(values['MFS_SLACK_CONNECTOR_CONFIG'], str(target / 'integrations/mfs/slack.toml'))
            return ['test-dependency']
        with patch.dict(os.environ, {'TAG_HOME': str(self.root)}, clear=True), patch.object(
            sys, 'argv', ['tag', 'start']
        ), patch.object(tag_cli, 'missing_runtime_dependencies', side_effect=dependencies), patch.object(
            tag_cli, 'stop_process'
        ) as stop, self.assertRaisesRegex(RuntimeError, 'test-dependency'):
            tag_cli.main()
        stop.assert_any_call(context.home, 'slack')

    def test_rollback_to_incompatible_release_keeps_current_pointer(self):
        import os
        import sys
        from scripts import tag_cli
        from contextlib import redirect_stdout
        from io import StringIO
        tag_instances.ensure_default(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        current = self.root / 'current.json'
        current.write_text('{"release": "current", "instance_layout": 2}')
        (self.root / 'previous.json').write_text('{"release": "old"}')
        with patch.dict(os.environ, {'TAG_HOME': str(self.root)}, clear=True), patch.object(
            sys, 'argv', ['tag', 'rollback']
        ), patch.object(tag_cli, 'bridge_processes', return_value=[]), patch.object(
            tag_cli, 'process_for', return_value=None
        ), redirect_stdout(StringIO()), self.assertRaisesRegex(RuntimeError, 'cannot read'):
            tag_cli.main()
        self.assertEqual(json.loads(current.read_text())['release'], 'current')
