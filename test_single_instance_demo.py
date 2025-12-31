#!/usr/bin/env python3
"""
Demonstration script to test single-instance lock behavior.

On Linux/Pi:
  - Run this script in one terminal
  - Try to run it again in another terminal -> should see "Already running" message

On Windows:
  - The lock is disabled, so multiple instances can run (preserves existing behavior)
"""

from __future__ import annotations

import os
import sys
import time

# Add parent directory to path so we can import lcarstv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lcarstv.core.single_instance import SingleInstanceLock


def main() -> int:
    print(f"Testing single-instance lock on {os.name} (PID: {os.getpid()})")
    
    # Create lock (enabled only on non-Windows platforms)
    lock = SingleInstanceLock(enabled=(os.name != "nt"))
    
    if os.name == "nt":
        print("NOTE: On Windows, single-instance lock is disabled (no-op)")
    
    print("Attempting to acquire lock...")
    
    if not lock.acquire():
        print("[BLOCKED] Another instance is already running. Exiting.")
        return 1
    
    print("[SUCCESS] Lock acquired! This instance is now running.")
    print(f"Lockfile: {lock.path}")
    
    if lock.enabled:
        # On Unix, show the lockfile contents
        try:
            with open(lock.path, "r") as f:
                content = f.read().strip()
            print(f"Lockfile contains PID: {content}")
        except Exception as e:
            print(f"Could not read lockfile: {e}")
    
    print("\nThis instance will run for 10 seconds.")
    print("Try running this script again in another terminal to test the lock.")
    print("Press Ctrl+C to exit early.\n")
    
    try:
        for i in range(10, 0, -1):
            print(f"Running... {i} seconds remaining")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Interrupted by user]")
    finally:
        print("Releasing lock...")
        lock.release()
        print("Lock released. Exiting.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
