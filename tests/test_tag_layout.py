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
