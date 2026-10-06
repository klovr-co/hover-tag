from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.tag_locks import LifecycleLock
from scripts import tag_locks


class LifecycleLockTests(unittest.TestCase):
    def test_windows_locks_empty_guard_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'start.lock'
            guard = path.with_name(path.name + '.guard')
            lock = LifecycleLock(path)
            msvcrt = Mock(LK_NBLCK=2, LK_UNLCK=0)

            def locking(fd, mode, size):
                self.assertEqual(fd, lock.handle.fileno())
                self.assertEqual(lock.handle.tell(), 0)
                self.assertEqual(size, 1)
                if mode == msvcrt.LK_NBLCK:
                    # Windows permits locking beyond EOF. Writing first races
                    # with another owner that has already locked this byte.
                    self.assertEqual(guard.read_bytes(), b'')

            msvcrt.locking.side_effect = locking
            with patch.object(tag_locks, 'os', wraps=os, O_RDWR=os.O_RDWR, O_CREAT=os.O_CREAT) as platform_os, \
                    patch.dict(sys.modules, {'msvcrt': msvcrt}):
                platform_os.name = 'nt'
                with lock:
                    self.assertTrue(path.is_dir())
                    self.assertEqual(guard.read_bytes(), b'tag-lifecycle-lock-v1')
            self.assertEqual(msvcrt.locking.call_count, 2)
            self.assertIsNone(lock.handle)
            self.assertFalse(path.exists())

    def test_windows_contention_on_empty_guard_leaves_it_untouched_and_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'start.lock'
            guard = path.with_name(path.name + '.guard')
            lock = LifecycleLock(path)
            msvcrt = Mock(LK_NBLCK=2, LK_UNLCK=0)
            msvcrt.locking.side_effect = PermissionError('byte already locked')
            with patch.object(tag_locks, 'os', wraps=os, O_RDWR=os.O_RDWR, O_CREAT=os.O_CREAT) as platform_os, \
                    patch.dict(sys.modules, {'msvcrt': msvcrt}):
                platform_os.name = 'nt'
                with self.assertRaisesRegex(RuntimeError, 'in progress'):
                    lock.acquire()
                self.assertIsNone(lock.handle)
                self.assertEqual(guard.read_bytes(), b'')
                self.assertFalse(path.exists())
                self.assertEqual(msvcrt.locking.call_count, 1)
                msvcrt.locking.side_effect = None
                with lock:
                    self.assertTrue(path.is_dir())

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


class AcquireAllTests(unittest.TestCase):
    def test_waits_for_a_busy_lock_then_takes_every_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / 'a.lock', Path(directory) / 'b.lock']
            other = LifecycleLock(paths[1]).acquire()
            waits = []

            def sleep(_):
                # The other operation finishes while we wait.
                if other.handle is not None:
                    other.release()

            held, waited = tag_locks.acquire_all(paths, wait=5, waiting=lambda: waits.append(1), sleep=sleep)
            self.assertTrue(waited)
            self.assertEqual(waits, [1])
            self.assertEqual([lock.path for lock in held], paths)
            for lock in held:
                lock.release()

    def test_gives_up_after_the_wait_and_holds_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / 'a.lock', Path(directory) / 'b.lock']
            other = LifecycleLock(paths[1]).acquire()
            now = [0.0]

            def sleep(seconds):
                now[0] += seconds

            with self.assertRaisesRegex(tag_locks.LockBusy, 'Another lifecycle operation'):
                tag_locks.acquire_all(paths, wait=2, sleep=sleep, clock=lambda: now[0])
            # The first lock was let go, so nothing is left half-held.
            LifecycleLock(paths[0]).acquire().release()
            other.release()

    def test_does_not_wait_without_a_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a.lock'
            other = LifecycleLock(path).acquire()
            with self.assertRaises(tag_locks.LockBusy):
                tag_locks.acquire_all([path], sleep=lambda _: self.fail('slept'))
            other.release()
