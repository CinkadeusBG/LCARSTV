"""Unit tests for metadata generation tool (no ffmpeg execution)."""

from __future__ import annotations

import pytest

from lcarstv_tools import ffmpeg_detect
from lcarstv_tools.generate_metadata import (
    create_break_windows,
    filter_edge_regions,
    filter_min_duration,
    merge_nearby_windows,
)
from lcarstv_tools.types import BreakWindow, Segment


class TestBlackSegmentParsing:
    """Tests for parsing blackdetect output."""

    def test_parse_single_black_segment(self) -> None:
        """Test parsing a single black segment."""
        stderr = (
            "[blackdetect @ 0x7f8b4c000000] black_start:123.456 black_end:125.789 "
            "black_duration:2.333\n"
        )
        segments = ffmpeg_detect.parse_black_segments(stderr)
        
        assert len(segments) == 1
        assert segments[0].start == pytest.approx(123.456)
        assert segments[0].end == pytest.approx(125.789)

    def test_parse_multiple_black_segments(self) -> None:
        """Test parsing multiple black segments."""
        stderr = (
            "[blackdetect @ 0x7f8b4c000000] black_start:10.5 black_end:12.0 black_duration:1.5\n"
            "[blackdetect @ 0x7f8b4c000000] black_start:100.0 black_end:102.5 black_duration:2.5\n"
            "[blackdetect @ 0x7f8b4c000000] black_start:200.25 black_end:201.75 black_duration:1.5\n"
        )
        segments = ffmpeg_detect.parse_black_segments(stderr)
        
        assert len(segments) == 3
        assert segments[0].start == pytest.approx(10.5)
        assert segments[0].end == pytest.approx(12.0)
        assert segments[1].start == pytest.approx(100.0)
        assert segments[1].end == pytest.approx(102.5)
        assert segments[2].start == pytest.approx(200.25)
        assert segments[2].end == pytest.approx(201.75)

    def test_parse_no_black_segments(self) -> None:
        """Test parsing when no black segments detected."""
        stderr = "[blackdetect @ 0x7f8b4c000000] no black detected\n"
        segments = ffmpeg_detect.parse_black_segments(stderr)
        
        assert len(segments) == 0

    def test_parse_with_extra_output(self) -> None:
        """Test parsing with extra ffmpeg output."""
        stderr = (
            "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'test.mp4':\n"
            "  Duration: 00:10:00.00, start: 0.000000, bitrate: 1000 kb/s\n"
            "[blackdetect @ 0x7f8b4c000000] black_start:50.0 black_end:52.0 black_duration:2.0\n"
            "frame= 1000 fps=100 q=-0.0 Lsize=N/A time=00:00:10.00\n"
        )
        segments = ffmpeg_detect.parse_black_segments(stderr)
        
        assert len(segments) == 1
        assert segments[0].start == pytest.approx(50.0)


class TestSilenceSegmentParsing:
    """Tests for parsing silencedetect output."""

    def test_parse_single_silence_segment(self) -> None:
        """Test parsing a single silence segment."""
        stderr = (
            "[silencedetect @ 0x7f8b4c000000] silence_start: 123.456\n"
            "[silencedetect @ 0x7f8b4c000000] silence_end: 125.789 | silence_duration: 2.333\n"
        )
        segments = ffmpeg_detect.parse_silence_segments(stderr)
        
        assert len(segments) == 1
        assert segments[0].start == pytest.approx(123.456)
        assert segments[0].end == pytest.approx(125.789)

    def test_parse_multiple_silence_segments(self) -> None:
        """Test parsing multiple silence segments."""
        stderr = (
            "[silencedetect @ 0x7f8b4c000000] silence_start: 10.5\n"
            "[silencedetect @ 0x7f8b4c000000] silence_end: 12.0 | silence_duration: 1.5\n"
            "[silencedetect @ 0x7f8b4c000000] silence_start: 100.0\n"
            "[silencedetect @ 0x7f8b4c000000] silence_end: 102.5 | silence_duration: 2.5\n"
        )
        segments = ffmpeg_detect.parse_silence_segments(stderr)
        
        assert len(segments) == 2
        assert segments[0].start == pytest.approx(10.5)
        assert segments[0].end == pytest.approx(12.0)
        assert segments[1].start == pytest.approx(100.0)
        assert segments[1].end == pytest.approx(102.5)

    def test_parse_no_silence_segments(self) -> None:
        """Test parsing when no silence segments detected."""
        stderr = "[silencedetect @ 0x7f8b4c000000] no silence detected\n"
        segments = ffmpeg_detect.parse_silence_segments(stderr)
        
        assert len(segments) == 0


class TestCreateBreakWindows:
    """Tests for creating break windows from black and silence segments."""

    def test_blackdetect_only_mode(self) -> None:
        """Test blackdetect-only mode (default)."""
        black = [
            Segment(start=10.0, end=12.0),
            Segment(start=50.0, end=52.0),
        ]
        silence: list[Segment] = []
        
        windows = create_break_windows(black, silence, require_silence=False)
        
        assert len(windows) == 2
        assert windows[0].start == pytest.approx(10.0)
        assert windows[0].end == pytest.approx(12.0)
        assert windows[1].start == pytest.approx(50.0)
        assert windows[1].end == pytest.approx(52.0)

    def test_require_silence_perfect_overlap(self) -> None:
        """Test require-silence mode with perfectly overlapping segments."""
        black = [Segment(start=10.0, end=12.0)]
        silence = [Segment(start=10.0, end=12.0)]
        
        windows = create_break_windows(black, silence, require_silence=True)
        
        assert len(windows) == 1
        assert windows[0].start == pytest.approx(10.0)
        assert windows[0].end == pytest.approx(12.0)

    def test_require_silence_partial_overlap(self) -> None:
        """Test require-silence mode with partial overlap."""
        black = [Segment(start=10.0, end=12.0)]
        silence = [Segment(start=11.0, end=13.0)]
        
        windows = create_break_windows(black, silence, require_silence=True)
        
        assert len(windows) == 1
        assert windows[0].start == pytest.approx(11.0)
        assert windows[0].end == pytest.approx(12.0)

    def test_require_silence_no_overlap(self) -> None:
        """Test require-silence mode with no overlap."""
        black = [Segment(start=10.0, end=12.0)]
        silence = [Segment(start=15.0, end=17.0)]
        
        windows = create_break_windows(black, silence, require_silence=True)
        
        assert len(windows) == 0

    def test_require_silence_adjacent_no_overlap(self) -> None:
        """Test that adjacent segments (touching) do not create overlap."""
        black = [Segment(start=10.0, end=12.0)]
        silence = [Segment(start=12.0, end=14.0)]
        
        windows = create_break_windows(black, silence, require_silence=True)
        
        # overlap_end = min(12.0, 14.0) = 12.0
        # overlap_start = max(10.0, 12.0) = 12.0
        # overlap_end > overlap_start? 12.0 > 12.0? No
        assert len(windows) == 0

    def test_require_silence_multiple_overlaps(self) -> None:
        """Test require-silence mode with multiple overlaps."""
        black = [
            Segment(start=10.0, end=12.0),
            Segment(start=50.0, end=52.0),
        ]
        silence = [
            Segment(start=11.0, end=13.0),
            Segment(start=51.0, end=53.0),
        ]
        
        windows = create_break_windows(black, silence, require_silence=True)
        
        assert len(windows) == 2
        assert windows[0].start == pytest.approx(11.0)
        assert windows[0].end == pytest.approx(12.0)
        assert windows[1].start == pytest.approx(51.0)
        assert windows[1].end == pytest.approx(52.0)

    def test_blackdetect_only_with_music_over_black(self) -> None:
        """Test blackdetect-only mode catches breaks with music (TNG scenario)."""
        # This simulates Star Trek TNG where there's black but music playing
        black = [Segment(start=600.0, end=602.0)]
        silence: list[Segment] = []  # No silence because of music sting
        
        # With require_silence=False, should still detect the break
        windows = create_break_windows(black, silence, require_silence=False)
        
        assert len(windows) == 1
        assert windows[0].start == pytest.approx(600.0)
        assert windows[0].end == pytest.approx(602.0)
        
        # With require_silence=True, should NOT detect
        windows_strict = create_break_windows(black, silence, require_silence=True)
        assert len(windows_strict) == 0


class TestFilterMinDuration:
    """Tests for minimum duration filtering."""

    def test_filter_short_breaks(self) -> None:
        """Test filtering breaks shorter than minimum duration."""
        windows = [
            BreakWindow(start=10.0, end=10.5),  # 0.5s - too short
            BreakWindow(start=20.0, end=21.0),  # 1.0s - exactly min
            BreakWindow(start=30.0, end=32.0),  # 2.0s - long enough
        ]
        
        filtered = filter_min_duration(windows, min_duration=1.0)
        
        assert len(filtered) == 2
        assert filtered[0].start == pytest.approx(20.0)
        assert filtered[1].start == pytest.approx(30.0)

    def test_filter_zero_length_breaks(self) -> None:
        """Test filtering zero-length breaks."""
        windows = [
            BreakWindow(start=10.0, end=12.0),  # Valid
            BreakWindow(start=20.0, end=20.001),  # Nearly zero
        ]
        
        filtered = filter_min_duration(windows, min_duration=1.0)
        
        assert len(filtered) == 1
        assert filtered[0].start == pytest.approx(10.0)

    def test_filter_empty_list(self) -> None:
        """Test filtering empty list."""
        windows: list[BreakWindow] = []
        
        filtered = filter_min_duration(windows, min_duration=1.0)
        
        assert len(filtered) == 0

    def test_all_breaks_too_short(self) -> None:
        """Test when all breaks are too short."""
        windows = [
            BreakWindow(start=10.0, end=10.5),  # 0.5s
            BreakWindow(start=20.0, end=20.3),  # 0.3s
        ]
        
        filtered = filter_min_duration(windows, min_duration=1.0)
        
        assert len(filtered) == 0


class TestFilterEdgeRegions:
    """Tests for filtering edge regions."""

    def test_filter_start_edge(self) -> None:
        """Test filtering segments in the start edge region."""
        windows = [
            BreakWindow(start=10.0, end=12.0),  # Within start edge
            BreakWindow(start=100.0, end=102.0),  # Valid
        ]
        
        filtered = filter_edge_regions(windows, exclude_seconds=90.0, duration=600.0)
        
        assert len(filtered) == 1
        assert filtered[0].start == pytest.approx(100.0)

    def test_filter_end_edge(self) -> None:
        """Test filtering segments in the end edge region."""
        windows = [
            BreakWindow(start=100.0, end=102.0),  # Valid
            BreakWindow(start=550.0, end=552.0),  # Within end edge
        ]
        
        filtered = filter_edge_regions(windows, exclude_seconds=90.0, duration=600.0)
        
        assert len(filtered) == 1
        assert filtered[0].start == pytest.approx(100.0)

    def test_filter_both_edges(self) -> None:
        """Test filtering segments in both edge regions."""
        windows = [
            BreakWindow(start=50.0, end=52.0),  # Start edge
            BreakWindow(start=300.0, end=302.0),  # Valid
            BreakWindow(start=550.0, end=552.0),  # End edge
        ]
        
        filtered = filter_edge_regions(windows, exclude_seconds=90.0, duration=600.0)
        
        assert len(filtered) == 1
        assert filtered[0].start == pytest.approx(300.0)

    def test_no_filtering_needed(self) -> None:
        """Test when all segments are valid."""
        windows = [
            BreakWindow(start=100.0, end=102.0),
            BreakWindow(start=300.0, end=302.0),
        ]
        
        filtered = filter_edge_regions(windows, exclude_seconds=90.0, duration=600.0)
        
        assert len(filtered) == 2


class TestMergeNearbyWindows:
    """Tests for merging nearby windows."""

    def test_merge_overlapping_windows(self) -> None:
        """Test merging overlapping windows."""
        windows = [
            BreakWindow(start=10.0, end=12.0),
            BreakWindow(start=11.0, end=13.0),
        ]
        
        merged = merge_nearby_windows(windows, merge_gap_seconds=0.5)
        
        assert len(merged) == 1
        assert merged[0].start == pytest.approx(10.0)
        assert merged[0].end == pytest.approx(13.0)

    def test_merge_adjacent_windows(self) -> None:
        """Test merging adjacent windows within gap threshold."""
        windows = [
            BreakWindow(start=10.0, end=12.0),
            BreakWindow(start=12.3, end=14.0),  # Gap of 0.3 seconds
        ]
        
        merged = merge_nearby_windows(windows, merge_gap_seconds=0.5)
        
        assert len(merged) == 1
        assert merged[0].start == pytest.approx(10.0)
        assert merged[0].end == pytest.approx(14.0)

    def test_no_merge_large_gap(self) -> None:
        """Test not merging windows with large gap."""
        windows = [
            BreakWindow(start=10.0, end=12.0),
            BreakWindow(start=15.0, end=17.0),  # Gap of 3.0 seconds
        ]
        
        merged = merge_nearby_windows(windows, merge_gap_seconds=0.5)
        
        assert len(merged) == 2
        assert merged[0].start == pytest.approx(10.0)
        assert merged[0].end == pytest.approx(12.0)
        assert merged[1].start == pytest.approx(15.0)
        assert merged[1].end == pytest.approx(17.0)

    def test_merge_multiple_chains(self) -> None:
        """Test merging multiple chains of windows."""
        windows = [
            BreakWindow(start=10.0, end=12.0),
            BreakWindow(start=12.2, end=14.0),  # Merge with first
            BreakWindow(start=14.3, end=16.0),  # Merge with chain
            BreakWindow(start=50.0, end=52.0),  # Separate chain
            BreakWindow(start=52.1, end=54.0),  # Merge with second chain
        ]
        
        merged = merge_nearby_windows(windows, merge_gap_seconds=0.5)
        
        assert len(merged) == 2
        assert merged[0].start == pytest.approx(10.0)
        assert merged[0].end == pytest.approx(16.0)
        assert merged[1].start == pytest.approx(50.0)
        assert merged[1].end == pytest.approx(54.0)

    def test_merge_empty_list(self) -> None:
        """Test merging empty list."""
        windows: list[BreakWindow] = []
        
        merged = merge_nearby_windows(windows, merge_gap_seconds=0.5)
        
        assert len(merged) == 0

    def test_merge_respects_sorting(self) -> None:
        """Test that merge handles unsorted windows correctly."""
        windows = [
            BreakWindow(start=50.0, end=52.0),
            BreakWindow(start=10.0, end=12.0),
            BreakWindow(start=12.3, end=14.0),
        ]
        
        merged = merge_nearby_windows(windows, merge_gap_seconds=0.5)
        
        assert len(merged) == 2
        # Should be sorted after merge
        assert merged[0].start == pytest.approx(10.0)
        assert merged[0].end == pytest.approx(14.0)
        assert merged[1].start == pytest.approx(50.0)
        assert merged[1].end == pytest.approx(52.0)


class TestSegmentDataclass:
    """Tests for Segment dataclass."""

    def test_valid_segment(self) -> None:
        """Test creating a valid segment."""
        seg = Segment(start=10.0, end=12.0)
        assert seg.start == 10.0
        assert seg.end == 12.0

    def test_invalid_segment_raises(self) -> None:
        """Test that invalid segment raises ValueError."""
        with pytest.raises(ValueError, match="must be >= start"):
            Segment(start=12.0, end=10.0)


class TestBreakWindowDataclass:
    """Tests for BreakWindow dataclass."""

    def test_valid_window(self) -> None:
        """Test creating a valid break window."""
        window = BreakWindow(start=10.0, end=12.0)
        assert window.start == 10.0
        assert window.end == 12.0

    def test_invalid_window_raises(self) -> None:
        """Test that invalid window raises ValueError."""
        with pytest.raises(ValueError, match="must be > start"):
            BreakWindow(start=12.0, end=12.0)

    def test_to_dict(self) -> None:
        """Test converting window to dictionary."""
        window = BreakWindow(start=123.456789, end=145.678901)
        result = window.to_dict()
        
        assert result == {"start": 123.457, "end": 145.679}
