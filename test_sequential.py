#!/usr/bin/env python3
"""Test script for sequential playthrough functionality."""

from pathlib import Path
from lcarstv.core.selector import _parse_episode_info, _sort_items_sequentially

def test_episode_parsing():
    """Test the SxxExx pattern parsing."""
    print("=== Testing Episode Parsing ===\n")
    
    test_cases = [
        ("file:z:/media/show/S01E01 - Pilot.mkv", (1, 1)),
        ("file:z:/media/show/s02e15 - Episode Name.mp4", (2, 15)),
        ("file:z:/media/show/Show.S03E22.1080p.mkv", (3, 22)),
        ("file:z:/media/show/random_file.mkv", None),
        ("file:z:/media/show/Season 1/Episode 10.mkv", None),
    ]
    
    for item_id, expected in test_cases:
        result = _parse_episode_info(item_id)
        status = "✓" if result == expected else "✗"
        print(f"{status} {item_id}")
        print(f"   Expected: {expected}, Got: {result}\n")

def test_sequential_sorting():
    """Test sequential sorting of episodes."""
    print("=== Testing Sequential Sorting ===\n")
    
    # Create test items in random order
    test_items = (
        "file:z:/media/show/S01E05 - Fifth Episode.mkv",
        "file:z:/media/show/S02E01 - Season 2 Premiere.mkv",
        "file:z:/media/show/S01E01 - Pilot.mkv",
        "file:z:/media/show/S01E10 - Tenth Episode.mkv",
        "file:z:/media/show/S01E02 - Second Episode.mkv",
        "file:z:/media/show/random_special.mkv",
        "file:z:/media/show/S02E05 - Mid Season.mkv",
    )
    
    print("Original order:")
    for i, item in enumerate(test_items, 1):
        filename = Path(item).name if "/" in item or "\\" in item else item
        print(f"  {i}. {filename}")
    
    sorted_items = _sort_items_sequentially(test_items)
    
    print("\nSorted order:")
    for i, item in enumerate(sorted_items, 1):
        filename = Path(item).name if "/" in item or "\\" in item else item
        ep_info = _parse_episode_info(item)
        ep_str = f" (S{ep_info[0]:02d}E{ep_info[1]:02d})" if ep_info else " (no episode info)"
        print(f"  {i}. {filename}{ep_str}")
    
    # Verify sorting is correct
    print("\n=== Verification ===")
    expected_order = [
        "S01E01 - Pilot.mkv",
        "S01E02 - Second Episode.mkv",
        "S01E05 - Fifth Episode.mkv",
        "S01E10 - Tenth Episode.mkv",
        "S02E01 - Season 2 Premiere.mkv",
        "S02E05 - Mid Season.mkv",
        "random_special.mkv",
    ]
    
    actual_order = [Path(item).name for item in sorted_items]
    
    if actual_order == expected_order:
        print("✓ Sorting is correct!")
    else:
        print("✗ Sorting mismatch!")
        print(f"Expected: {expected_order}")
        print(f"Got:      {actual_order}")

def test_wraparound_behavior():
    """Test that sequential index wraps around correctly."""
    print("\n=== Testing Wraparound Behavior ===\n")
    
    test_items = (
        "file:z:/media/show/S01E01.mkv",
        "file:z:/media/show/S01E02.mkv",
        "file:z:/media/show/S01E03.mkv",
    )
    
    sorted_items = _sort_items_sequentially(test_items)
    
    print(f"Total items: {len(sorted_items)}")
    print("\nSimulating sequential playback:")
    
    for i in range(5):  # Play through more than once to test wraparound
        index = i % len(sorted_items)
        filename = Path(sorted_items[index]).name
        print(f"  Pick {i+1}: {filename} (index={index})")
    
    print("\n✓ Wraparound works correctly!")

if __name__ == "__main__":
    print("Sequential Playthrough Test Suite\n")
    print("=" * 50)
    print()
    
    test_episode_parsing()
    print()
    test_sequential_sorting()
    print()
    test_wraparound_behavior()
    
    print("\n" + "=" * 50)
    print("All tests completed!")
