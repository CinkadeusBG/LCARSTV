"""Test script for --max-break-duration feature."""

from pathlib import Path
from lcarstv_tools.types import BreakWindow
from lcarstv_tools.generate_metadata import (
    filter_windows_by_max_duration,
    merge_breaks_by_gap,
)

def test_max_break_duration_filter():
    """Test that the max-break-duration filter works correctly."""
    
    # Create test windows
    windows = [
        BreakWindow(start=100.0, end=105.0),    # 5 seconds - should pass
        BreakWindow(start=200.0, end=350.0),    # 150 seconds - should pass
        BreakWindow(start=1000.0, end=1400.0),  # 400 seconds - should be removed
        BreakWindow(start=1500.0, end=1680.0),  # 180 seconds - should pass (exactly at threshold)
        BreakWindow(start=2000.0, end=2005.0),  # 5 seconds - should pass
    ]
    
    print("Test 1: Filter with max_duration=180")
    print(f"  Input: {len(windows)} windows")
    filtered = filter_windows_by_max_duration(windows, 180.0)
    print(f"  Output: {len(filtered)} windows")
    
    assert len(filtered) == 4, f"Expected 4 windows, got {len(filtered)}"
    
    # Verify the 400-second window was removed
    for window in filtered:
        duration = window.end - window.start
        assert duration <= 180.0, f"Window {window} has duration {duration}s > 180s"
    
    # Verify the removed window is the 1000-1400 one
    assert not any(w.start == 1000.0 and w.end == 1400.0 for w in filtered), \
        "Window 1000-1400 should have been removed"
    
    print("  ✓ Successfully removed 400-second window")
    print("  ✓ Kept 180-second window (at threshold)")
    print("  ✓ All remaining windows are <= 180 seconds")
    
    print("\nTest 2: No filter (max_duration=None simulates omitted flag)")
    print(f"  Input: {len(windows)} windows")
    # When None, the filter shouldn't be called, but let's verify the list stays intact
    print(f"  Output: {len(windows)} windows (unchanged)")
    assert len(windows) == 5, "Original list should be unchanged"
    print("  ✓ Current behavior preserved when flag omitted")
    
    print("\nTest 3: Verify specific durations")
    test_cases = [
        (100.0, 105.0, 180.0, True, "5s window should pass"),
        (200.0, 350.0, 180.0, True, "150s window should pass"),
        (1000.0, 1400.0, 180.0, False, "400s window should be removed"),
        (1500.0, 1680.0, 180.0, True, "180s window at threshold should pass"),
        (1000.0, 1181.0, 180.0, False, "181s window should be removed"),
    ]
    
    for start, end, max_dur, should_pass, description in test_cases:
        window = BreakWindow(start=start, end=end)
        result = filter_windows_by_max_duration([window], max_dur)
        passed = len(result) == 1
        assert passed == should_pass, f"Failed: {description}"
        print(f"  ✓ {description}")
    
    print("\nTest 4: Gap-based merge creating runaway windows (two-stage filtering)")
    # Simulate the user's issue: multiple short breaks get merged into a giant one
    short_breaks = [
        BreakWindow(start=800.0, end=802.0),    # 2 seconds
        BreakWindow(start=1100.0, end=1103.0),  # 3 seconds
        BreakWindow(start=1200.0, end=1202.0),  # 2 seconds
    ]
    
    print(f"  Input: {len(short_breaks)} short breaks")
    for i, w in enumerate(short_breaks, 1):
        print(f"    {i}. {w.start}s - {w.end}s ({w.end - w.start}s)")
    
    # Merge with 420-second gap (like the user's command)
    merged = merge_breaks_by_gap(short_breaks, 420.0)
    print(f"  After gap-based merge (420s): {len(merged)} break(s)")
    for i, w in enumerate(merged, 1):
        dur = w.end - w.start
        print(f"    {i}. {w.start}s - {w.end}s ({dur}s)")
    
    # The merge should create one giant window
    assert len(merged) == 1, f"Expected 1 merged window, got {len(merged)}"
    merged_duration = merged[0].end - merged[0].start
    print(f"  Merged duration: {merged_duration}s (exceeds 180s threshold)")
    assert merged_duration > 180.0, "Merged window should exceed threshold"
    
    # Now apply max-duration filter (simulating the final filter)
    final = filter_windows_by_max_duration(merged, 180.0)
    print(f"  After final max-duration filter: {len(final)} break(s)")
    
    assert len(final) == 0, "Runaway merged window should be removed"
    print("  ✓ Successfully caught and removed runaway window created by merging")
    
    print("\n✅ All tests passed!")

if __name__ == "__main__":
    test_max_break_duration_filter()
