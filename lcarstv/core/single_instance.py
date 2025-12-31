from __future__ import annotations

import os
from typing import Any


class SingleInstanceLock:
    """
    File-based single-instance lock using fcntl.flock on Unix systems.
    
    On Windows (os.name == "nt"), this is a no-op and always succeeds to preserve
    existing behavior. On Linux/Pi, uses an exclusive file lock to ensure only one
    instance of the application can run at a time.
    
    Usage:
        lock = SingleInstanceLock(enabled=(os.name != "nt"))
        if not lock.acquire():
            print("Already running")
            sys.exit(1)
        try:
            # ... application code ...
        finally:
            lock.release()
    
    Or as a context manager:
        with SingleInstanceLock(enabled=(os.name != "nt")) as lock:
            if not lock.acquired:
                print("Already running")
                sys.exit(1)
            # ... application code ...
    """

    def __init__(self, path: str = "/tmp/lcarstv.lock", enabled: bool = True) -> None:
        """
        Initialize the single-instance lock.
        
        Args:
            path: Path to the lockfile. Default: /tmp/lcarstv.lock
            enabled: If False, lock is a no-op (always succeeds). On Windows,
                     this is automatically set to False regardless of input.
        """
        self.path = path
        # On Windows, force enabled=False to preserve existing behavior
        self.enabled = enabled and (os.name != "nt")
        self._fd: int | None = None
        self.acquired = False

    def acquire(self) -> bool:
        """
        Attempt to acquire the lock.
        
        Returns:
            True if the lock was acquired (or if disabled).
            False if another instance already holds the lock.
        """
        if not self.enabled:
            self.acquired = True
            return True

        # Import fcntl only on Unix systems where it's available
        try:
            import fcntl
        except ImportError:
            # fcntl not available (shouldn't happen given enabled check, but be safe)
            self.acquired = True
            return True

        try:
            # Open lockfile for writing (create if doesn't exist)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o644)
            
            # Try to acquire an exclusive, non-blocking lock
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Lock is already held by another process
                os.close(fd)
                self.acquired = False
                return False
            
            # Lock acquired! Write our PID to the file
            self._fd = fd
            pid_str = f"{os.getpid()}\n"
            os.ftruncate(fd, 0)  # Clear any previous content
            os.write(fd, pid_str.encode("utf-8"))
            os.fsync(fd)  # Ensure it's written to disk
            
            self.acquired = True
            return True
            
        except Exception:
            # If anything goes wrong, clean up and report failure
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None
            self.acquired = False
            return False

    def release(self) -> None:
        """
        Release the lock if it was acquired.
        
        This is idempotent - safe to call multiple times.
        """
        if not self.enabled or self._fd is None:
            return

        try:
            import fcntl
            # Explicitly unlock (though closing fd would do this automatically)
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except Exception:
            pass  # Best effort
        
        try:
            os.close(self._fd)
        except Exception:
            pass  # Best effort
        
        self._fd = None
        self.acquired = False

    def __enter__(self) -> SingleInstanceLock:
        """Context manager entry: acquire the lock."""
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Context manager exit: release the lock."""
        self.release()
