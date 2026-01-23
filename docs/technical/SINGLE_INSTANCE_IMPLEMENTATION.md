# Single-Instance Lock Implementation

## Overview

LCARSTV now includes single-instance locking on Linux/Pi to prevent multiple instances from running simultaneously, which would cause mpv IPC socket contention and other resource conflicts.

## Implementation Details

### Files Created/Modified

1. **lcarstv/core/single_instance.py** (NEW)
   - `SingleInstanceLock` class using fcntl.flock on Unix systems
   - No-op behavior on Windows to preserve existing functionality
   - Context manager support for clean resource management
   - Writes PID to lockfile for debugging

2. **lcarstv/app.py** (MODIFIED)
   - Lock acquisition immediately after argument parsing
   - Lock release in the finally block
   - Clear error message when instance is already running

3. **tests/test_single_instance.py** (NEW)
   - 7 comprehensive test cases
   - Platform-aware: skips Unix-specific tests on Windows
   - Tests locking behavior, context manager, PID writing, and edge cases

### How It Works

#### On Linux/Pi:
```python
lock = SingleInstanceLock(enabled=(os.name != "nt"))
if not lock.acquire():
    print("[lcarstv] Another instance is already running. Exiting.")
    return 1
```

- Uses `fcntl.flock(fd, LOCK_EX | LOCK_NB)` for non-blocking exclusive file lock
- Lockfile: `/tmp/lcarstv.lock`
- If lock acquisition fails (BlockingIOError), another instance is running
- Lock is automatically released by kernel if process crashes

#### On Windows:
- Lock is automatically disabled (no-op)
- Always returns `True` from `acquire()`
- No behavior change from previous versions

### Testing

Run the test suite:
```bash
python -m unittest discover -s tests -v
```

**Expected Results:**
- On Windows: 7 tests run, 6 skipped (Unix-specific)
- On Linux/Pi: All 13 tests run

### Demo Script

Use `test_single_instance_demo.py` to verify the locking behavior:

```bash
# Terminal 1
python test_single_instance_demo.py

# Terminal 2 (while terminal 1 is still running)
python test_single_instance_demo.py
# Should see: "[BLOCKED] Another instance is already running. Exiting."
```

## Behavior

### Before This Change
- Users could accidentally start LCARSTV multiple times
- Multiple mpv instances would conflict over IPC socket
- Race conditions and undefined behavior

### After This Change

**Linux/Pi:**
- First instance: Starts normally
- Second instance: Prints error message and exits with code 1
- Clean, deterministic behavior

**Windows:**
- No change (lock disabled)
- Multiple instances still allowed (if needed for development/testing)

## Technical Details

### Lock Characteristics
- **Mechanism:** fcntl file lock (advisory, but sufficient)
- **Scope:** System-wide (all users see the same lockfile)
- **Cleanup:** Automatic on process exit (even on crash/kill)
- **PID tracking:** Lockfile contains running instance's PID

### Edge Cases Handled
- ✅ Multiple acquire/release cycles
- ✅ Context manager (__enter__/__exit__)
- ✅ Multiple release() calls (idempotent)
- ✅ Lock cleanup on exception/interrupt
- ✅ Different lockfile paths don't conflict
- ✅ Windows compatibility (no-op)

## Future Enhancements

If needed, the implementation can be extended to:
- Allow lock path override via CLI argument
- Add `--force` flag to bypass lock (for debugging)
- Implement Windows-specific locking (e.g., named mutex)
- Add systemd integration for automatic restart handling

## Acceptance Criteria ✅

- [x] Starting LCARSTV twice on Pi/Linux results in second instance exiting cleanly
- [x] Clear error message displayed to user
- [x] No regression on Windows
- [x] `python -m unittest discover -s tests -v` passes
- [x] Code is minimal and readable
- [x] Coexists with mpv IPC socket race fix
