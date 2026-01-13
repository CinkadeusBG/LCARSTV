#!/usr/bin/env python3
"""Test script for aggregate channel (WMIX) functionality."""

from pathlib import Path
from lcarstv.core.config import load_channels, load_settings_profile

def test_aggregate_config():
    """Test that aggregate channel config loads correctly."""
    print("=" * 60)
    print("Testing Aggregate Channel Configuration")
    print("=" * 60)
    
    repo_root = Path(".")
    
    # Load channels config
    print("\n1. Loading channels configuration...")
    cfg = load_channels(repo_root=repo_root, profile=None)
    print(f"   ✓ Loaded {len(cfg.channels)} channels")
    
    # Check for aggregate channels
    print("\n2. Checking for aggregate channels...")
    agg_channels = [ch for ch in cfg.channels if ch.aggregate_from_channels is not None]
    print(f"   ✓ Found {len(agg_channels)} aggregate channel(s)")
    
    # Check WMIX specifically
    print("\n3. Validating WMIX aggregate channel...")
    wmix = [ch for ch in cfg.channels if ch.call_sign == "WMIX"]
    if not wmix:
        print("   ✗ WMIX channel not found!")
        return False
    
    wmix = wmix[0]
    print(f"   ✓ WMIX found: {wmix.call_sign}")
    print(f"   ✓ Sources: {list(wmix.aggregate_from_channels or [])}")
    print(f"   ✓ Cooldown: {wmix.cooldown}")
    
    # Validate media_dirs is empty
    if wmix.media_dirs:
        print(f"   ✗ WMIX should not have media_dirs, but has: {wmix.media_dirs}")
        return False
    print("   ✓ No media_dirs (correct for aggregate channel)")
    
    # Validate sources exist
    print("\n4. Validating source channels exist...")
    channel_map = {ch.call_sign: ch for ch in cfg.channels}
    for source_cs in wmix.aggregate_from_channels or []:
        if source_cs in channel_map:
            source_ch = channel_map[source_cs]
            print(f"   ✓ {source_cs}: Found")
            print(f"      - Sequential: {source_ch.sequential_playthrough}")
            print(f"      - Cooldown: {source_ch.cooldown}")
        else:
            print(f"   ✗ Source {source_cs} not found!")
            return False
    
    print("\n5. Channel order validation...")
    expected_order = ["KTOS", "WTNG", "KDSN", "KVOY", "WMOV", "WMIX"]
    actual_order = [ch.call_sign for ch in cfg.channels]
    print(f"   Expected: {expected_order}")
    print(f"   Actual:   {actual_order}")
    if actual_order == expected_order:
        print("   ✓ Channel order is correct")
    else:
        print("   ⚠ Channel order differs (may be intentional)")
    
    print("\n" + "=" * 60)
    print("✓ All aggregate channel configuration tests passed!")
    print("=" * 60)
    return True

if __name__ == "__main__":
    try:
        success = test_aggregate_config()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
