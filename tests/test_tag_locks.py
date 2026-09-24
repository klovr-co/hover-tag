from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.tag_locks import LifecycleLock


class LifecycleLockTests(unittest.TestCase):
    def test_concurrent_owner_is_rejected_and_release_allows_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'start.lock'
            with LifecycleLock(path):
                with self.assertRaisesRegex(RuntimeError, 'in progress'):
                    LifecycleLock(path).acquire()
            with LifecycleLock(path):
                self.assertTrue(path.is_dir())
            self.assertFalse(path.exists())

    def test_process_crash_releases_lock_and_next_start_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'start.lock'
            code = ('import os,sys; from pathlib import Path; '
                    'from scripts.tag_locks import LifecycleLock; '
                    'lock=LifecycleLock(Path(sys.argv[1])).acquire(); os._exit(0)')
            subprocess.run([sys.executable, '-c', code, str(path)], check=True)
            self.assertTrue(path.is_dir())
            with LifecycleLock(path):
                pass
            self.assertFalse(path.exists())

    def test_empty_legacy_lock_is_recovered_only_without_legacy_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'start.lock'
            path.mkdir()
            with patch('psutil.process_iter', return_value=[]), LifecycleLock(path):
                self.assertTrue(path.is_dir())

    def test_legacy_lock_with_live_cli_is_not_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'start.lock'
            path.mkdir()
            process = Mock(pid=999999999, info={'cmdline': ['python', '/old/scripts/tag_cli.py', 'start']})
            with patch('psutil.process_iter', return_value=[process]):
                with self.assertRaisesRegex(RuntimeError, 'legacy lock'):
                    LifecycleLock(path).acquire()
            self.assertTrue(path.is_dir())
