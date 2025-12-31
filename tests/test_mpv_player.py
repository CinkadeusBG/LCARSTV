from __future__ import annotations

import unittest
from pathlib import Path


class FakeClock:
    """Mock time source for testing time-based logic."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += float(dt)


class WaitForPathExistsTests(unittest.TestCase):
    """Test the _wait_for_path_exists helper function."""

    def test_path_exists_immediately(self) -> None:
        """Path exists on first check - returns immediately."""
        from lcarstv.player.mpv_player import _wait_for_path_exists

        clk = FakeClock()
        path = Path("/tmp/test.sock")

        # Fake exists_fn that always returns True
        def exists_fn(p: Path) -> bool:
            return True

        result = _wait_for_path_exists(
            path, timeout_sec=2.0, poll_interval_sec=0.02, exists_fn=exists_fn, time_fn=clk.now
        )

        self.assertTrue(result)
        # Should not have advanced time (returned immediately)
        self.assertEqual(clk.t, 0.0)

    def test_path_appears_before_timeout(self) -> None:
        """Path appears after a few checks - returns True."""
        from lcarstv.player.mpv_player import _wait_for_path_exists

        clk = FakeClock()
        path = Path("/tmp/test.sock")

        check_count = 0

        def exists_fn(p: Path) -> bool:
            nonlocal check_count
            check_count += 1
            # Path appears on the 5th check
            return check_count >= 5

        # Mock sleep to advance time
        def mock_sleep(duration: float) -> None:
            clk.advance(duration)

        # Temporarily replace time.sleep
        import time

        original_sleep = time.sleep
        time.sleep = mock_sleep

        try:
            result = _wait_for_path_exists(
                path, timeout_sec=2.0, poll_interval_sec=0.02, exists_fn=exists_fn, time_fn=clk.now
            )

            self.assertTrue(result)
            # Should have checked 5 times
            self.assertEqual(check_count, 5)
            # Time should have advanced by 4 * poll_interval
            self.assertAlmostEqual(clk.t, 4 * 0.02, places=5)
        finally:
            time.sleep = original_sleep

    def test_path_never_appears_timeout(self) -> None:
        """Path never appears - returns False after timeout."""
        from lcarstv.player.mpv_player import _wait_for_path_exists

        clk = FakeClock()
        path = Path("/tmp/test.sock")

        check_count = 0

        def exists_fn(p: Path) -> bool:
            nonlocal check_count
            check_count += 1
            return False

        # Mock sleep to advance time
        def mock_sleep(duration: float) -> None:
            clk.advance(duration)

        # Temporarily replace time.sleep
        import time

        original_sleep = time.sleep
        time.sleep = mock_sleep

        try:
            result = _wait_for_path_exists(
                path, timeout_sec=0.1, poll_interval_sec=0.02, exists_fn=exists_fn, time_fn=clk.now
            )

            self.assertFalse(result)
            # Should have checked multiple times (0.1 / 0.02 = 5 times, plus final check)
            self.assertGreater(check_count, 0)
        finally:
            time.sleep = original_sleep

    def test_path_appears_exactly_at_deadline(self) -> None:
        """Path appears exactly at deadline - final check succeeds."""
        from lcarstv.player.mpv_player import _wait_for_path_exists

        clk = FakeClock()
        path = Path("/tmp/test.sock")

        check_count = 0

        def exists_fn(p: Path) -> bool:
            nonlocal check_count
            check_count += 1
            # Only exists on the final check after timeout
            return clk.t >= 2.0

        # Mock sleep to advance time
        def mock_sleep(duration: float) -> None:
            clk.advance(duration)

        # Temporarily replace time.sleep
        import time

        original_sleep = time.sleep
        time.sleep = mock_sleep

        try:
            result = _wait_for_path_exists(
                path, timeout_sec=2.0, poll_interval_sec=0.5, exists_fn=exists_fn, time_fn=clk.now
            )

            # Final check at deadline should succeed
            self.assertTrue(result)
        finally:
            time.sleep = original_sleep


if __name__ == "__main__":
    unittest.main()
