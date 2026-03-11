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


class GetChapterListTests(unittest.TestCase):
    """Test MpvPlayer.get_chapter_list() without a real mpv process."""

    def _make_player_with_ipc(self, ipc_response: dict):
        """Return a MpvPlayer whose IPC client returns the given response."""
        from lcarstv.player.mpv_player import MpvPlayer

        player = MpvPlayer.__new__(MpvPlayer)
        player.debug = False
        player.ipc_trace = False
        player._ipc = _FakeIpc(ipc_response)
        return player

    def test_returns_empty_when_no_ipc(self) -> None:
        """No IPC client → returns []."""
        from lcarstv.player.mpv_player import MpvPlayer

        player = MpvPlayer.__new__(MpvPlayer)
        player.debug = False
        player._ipc = None
        self.assertEqual(player.get_chapter_list(), [])

    def test_returns_empty_when_ipc_error(self) -> None:
        """IPC returns error → returns []."""
        player = self._make_player_with_ipc({"error": "property unavailable", "data": None})
        self.assertEqual(player.get_chapter_list(), [])

    def test_returns_empty_when_data_is_none(self) -> None:
        """chapter-list property is None (no chapters) → returns []."""
        player = self._make_player_with_ipc({"error": "success", "data": None})
        self.assertEqual(player.get_chapter_list(), [])

    def test_returns_empty_when_only_one_chapter(self) -> None:
        """Only chapter 0 (t=0) present → returns [] because chapter 0 is skipped."""
        player = self._make_player_with_ipc({
            "error": "success",
            "data": [{"title": "Act 1", "time": 0.0}],
        })
        self.assertEqual(player.get_chapter_list(), [])

    def test_returns_chapter_timestamps_skipping_chapter_zero(self) -> None:
        """3-act file: chapter 0 at t=0, chapter 1 at t=720.5, chapter 2 at t=1440.2."""
        player = self._make_player_with_ipc({
            "error": "success",
            "data": [
                {"title": "Act 1", "time": 0.0},
                {"title": "Act 2", "time": 720.5},
                {"title": "Act 3", "time": 1440.2},
            ],
        })
        result = player.get_chapter_list()
        self.assertEqual(result, [720.5, 1440.2])

    def test_result_is_sorted(self) -> None:
        """Chapter times returned in ascending order even if mpv returns them out of order."""
        player = self._make_player_with_ipc({
            "error": "success",
            "data": [
                {"title": "Act 1", "time": 0.0},
                {"title": "Act 3", "time": 1440.2},
                {"title": "Act 2", "time": 720.5},
            ],
        })
        result = player.get_chapter_list()
        self.assertEqual(result, [720.5, 1440.2])

    def test_skips_non_dict_entries(self) -> None:
        """Malformed entries (non-dict) in chapter-list are skipped gracefully."""
        player = self._make_player_with_ipc({
            "error": "success",
            "data": [
                {"title": "Act 1", "time": 0.0},
                "garbage",
                None,
                {"title": "Act 2", "time": 720.5},
            ],
        })
        result = player.get_chapter_list()
        self.assertEqual(result, [720.5])

    def test_skips_entries_without_time_key(self) -> None:
        """Entries missing the 'time' key are skipped."""
        player = self._make_player_with_ipc({
            "error": "success",
            "data": [
                {"title": "Act 1", "time": 0.0},
                {"title": "Act 2"},           # no 'time' key
                {"title": "Act 3", "time": 720.5},
            ],
        })
        result = player.get_chapter_list()
        self.assertEqual(result, [720.5])

    def test_ipc_exception_returns_empty(self) -> None:
        """IPC command raises an exception → returns [] without propagating."""
        from lcarstv.player.mpv_player import MpvPlayer

        player = MpvPlayer.__new__(MpvPlayer)
        player.debug = False
        player._ipc = _RaisingIpc()
        self.assertEqual(player.get_chapter_list(), [])

    def test_no_error_key_treated_as_success(self) -> None:
        """Response with no 'error' key (treated as success) still works."""
        player = self._make_player_with_ipc({
            "data": [
                {"title": "Act 1", "time": 0.0},
                {"title": "Act 2", "time": 850.0},
            ],
        })
        result = player.get_chapter_list()
        self.assertEqual(result, [850.0])


class _FakeIpc:
    """Minimal IPC stub that returns a fixed response for any command."""

    def __init__(self, response: dict) -> None:
        self._response = response

    def command(self, *args, **kwargs) -> dict:
        return self._response


class _RaisingIpc:
    """IPC stub that always raises an exception."""

    def command(self, *args, **kwargs) -> dict:
        raise RuntimeError("simulated IPC failure")


if __name__ == "__main__":
    unittest.main()
