from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class SingleInstanceLockTests(unittest.TestCase):
    """Test the SingleInstanceLock class for preventing concurrent application instances."""

    def test_windows_always_succeeds(self) -> None:
        """On Windows (or when enabled=False), lock always succeeds (no-op behavior)."""
        from lcarstv.core.single_instance import SingleInstanceLock

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            lockpath = tmp.name

        try:
            # Create two locks with enabled=False (Windows behavior)
            lock1 = SingleInstanceLock(path=lockpath, enabled=False)
            lock2 = SingleInstanceLock(path=lockpath, enabled=False)

            # Both should succeed
            self.assertTrue(lock1.acquire())
            self.assertTrue(lock1.acquired)
            self.assertTrue(lock2.acquire())
            self.assertTrue(lock2.acquired)

            lock1.release()
            lock2.release()
        finally:
            try:
                os.unlink(lockpath)
            except Exception:
                pass

    @unittest.skipIf(os.name == "nt", "fcntl-based locking not available on Windows")
    def test_acquire_release_cycle(self) -> None:
        """Acquire and release lock, then acquire again - should succeed."""
        from lcarstv.core.single_instance import SingleInstanceLock

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            lockpath = tmp.name

        try:
            lock = SingleInstanceLock(path=lockpath, enabled=True)

            # First acquisition should succeed
            self.assertTrue(lock.acquire())
            self.assertTrue(lock.acquired)

            # Release the lock
            lock.release()
            self.assertFalse(lock.acquired)

            # Should be able to acquire again after release
            self.assertTrue(lock.acquire())
            self.assertTrue(lock.acquired)

            lock.release()
        finally:
            try:
                os.unlink(lockpath)
            except Exception:
                pass

    @unittest.skipIf(os.name == "nt", "fcntl-based locking not available on Windows")
    def test_two_instances_conflict(self) -> None:
        """Two lock instances with the same path should conflict."""
        from lcarstv.core.single_instance import SingleInstanceLock

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            lockpath = tmp.name

        try:
            lock1 = SingleInstanceLock(path=lockpath, enabled=True)
            lock2 = SingleInstanceLock(path=lockpath, enabled=True)

            # First lock should succeed
            self.assertTrue(lock1.acquire())
            self.assertTrue(lock1.acquired)

            # Second lock should fail (already held by lock1)
            self.assertFalse(lock2.acquire())
            self.assertFalse(lock2.acquired)

            # After releasing lock1, lock2 should succeed
            lock1.release()
            self.assertFalse(lock1.acquired)

            self.assertTrue(lock2.acquire())
            self.assertTrue(lock2.acquired)

            lock2.release()
        finally:
            try:
                os.unlink(lockpath)
            except Exception:
                pass

    @unittest.skipIf(os.name == "nt", "fcntl-based locking not available on Windows")
    def test_context_manager(self) -> None:
        """Test lock works correctly as a context manager."""
        from lcarstv.core.single_instance import SingleInstanceLock

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            lockpath = tmp.name

        try:
            # Use lock as a context manager
            with SingleInstanceLock(path=lockpath, enabled=True) as lock1:
                self.assertTrue(lock1.acquired)

                # Try to acquire with another instance while first is held
                lock2 = SingleInstanceLock(path=lockpath, enabled=True)
                self.assertFalse(lock2.acquire())

            # After exiting context, lock1 should be released
            self.assertFalse(lock1.acquired)

            # Now lock2 should be able to acquire
            self.assertTrue(lock2.acquire())
            self.assertTrue(lock2.acquired)
            lock2.release()
        finally:
            try:
                os.unlink(lockpath)
            except Exception:
                pass

    @unittest.skipIf(os.name == "nt", "fcntl-based locking not available on Windows")
    def test_lockfile_contains_pid(self) -> None:
        """After acquiring lock, lockfile should contain the PID."""
        from lcarstv.core.single_instance import SingleInstanceLock

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            lockpath = tmp.name

        try:
            lock = SingleInstanceLock(path=lockpath, enabled=True)
            self.assertTrue(lock.acquire())

            # Read lockfile contents
            with open(lockpath, "r") as f:
                content = f.read().strip()

            # Should contain the current process PID
            self.assertEqual(content, str(os.getpid()))

            lock.release()
        finally:
            try:
                os.unlink(lockpath)
            except Exception:
                pass

    @unittest.skipIf(os.name == "nt", "fcntl-based locking not available on Windows")
    def test_multiple_release_is_safe(self) -> None:
        """Calling release() multiple times should be safe (idempotent)."""
        from lcarstv.core.single_instance import SingleInstanceLock

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            lockpath = tmp.name

        try:
            lock = SingleInstanceLock(path=lockpath, enabled=True)
            self.assertTrue(lock.acquire())

            # Release multiple times - should not raise
            lock.release()
            lock.release()
            lock.release()

            self.assertFalse(lock.acquired)
        finally:
            try:
                os.unlink(lockpath)
            except Exception:
                pass

    @unittest.skipIf(os.name == "nt", "fcntl-based locking not available on Windows")
    def test_different_paths_no_conflict(self) -> None:
        """Locks with different paths should not conflict."""
        from lcarstv.core.single_instance import SingleInstanceLock

        with tempfile.NamedTemporaryFile(delete=False) as tmp1:
            lockpath1 = tmp1.name
        with tempfile.NamedTemporaryFile(delete=False) as tmp2:
            lockpath2 = tmp2.name

        try:
            lock1 = SingleInstanceLock(path=lockpath1, enabled=True)
            lock2 = SingleInstanceLock(path=lockpath2, enabled=True)

            # Both should succeed since they use different lockfiles
            self.assertTrue(lock1.acquire())
            self.assertTrue(lock2.acquire())

            lock1.release()
            lock2.release()
        finally:
            try:
                os.unlink(lockpath1)
            except Exception:
                pass
            try:
                os.unlink(lockpath2)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
