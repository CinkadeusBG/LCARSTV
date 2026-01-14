"""CLI tool to generate commercial break metadata for media files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ffmpeg_detect
from .types import BreakWindow, Segment


def seconds_to_timecode(seconds: float) -> str:
    """Convert seconds to HH:MM:SS.mmm format for VLC compatibility.
    
    Args:
        seconds: Time in seconds
    
    Returns:
        Formatted timecode string
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def create_break_windows(
    black_segments: list[Segment],
    silence_segments: list[Segment],
    require_silence: bool,
) -> list[BreakWindow]:
    """Create break windows from black segments, optionally requiring silence overlap.
    
    Args:
        black_segments: List of detected black segments
        silence_segments: List of detected silence segments
        require_silence: If True, require overlap with silence segments
    
    Returns:
        List of break windows
    """
    windows: list[BreakWindow] = []
    
    if not require_silence:
        # Blackdetect-only mode: convert black segments directly to break windows
        for black in black_segments:
            if black.end > black.start:
                windows.append(BreakWindow(start=black.start, end=black.end))
    else:
        # Require overlap with silence segments
        for black in black_segments:
            for silence in silence_segments:
                overlap_start = max(black.start, silence.start)
                overlap_end = min(black.end, silence.end)
                
                # Only accept actual overlaps
                if overlap_end > overlap_start:
                    windows.append(BreakWindow(start=overlap_start, end=overlap_end))
    
    return windows


def filter_edge_regions(
    windows: list[BreakWindow],
    exclude_seconds: float,
    duration: float,
) -> list[BreakWindow]:
    """Filter out windows that touch the first or last N seconds of the file.
    
    Args:
        windows: List of break windows
        exclude_seconds: Seconds to exclude from start and end
        duration: Total duration of media file
    
    Returns:
        Filtered list of break windows
    """
    filtered: list[BreakWindow] = []
    
    for window in windows:
        # Skip if starts before exclude zone ends
        if window.start < exclude_seconds:
            continue
        # Skip if ends after exclude zone starts
        if window.end > duration - exclude_seconds:
            continue
        
        filtered.append(window)
    
    return filtered


def merge_nearby_windows(
    windows: list[BreakWindow],
    merge_gap_seconds: float,
) -> list[BreakWindow]:
    """Merge break windows that overlap or are separated by <= merge_gap_seconds.
    
    Args:
        windows: List of break windows (will be sorted)
        merge_gap_seconds: Maximum gap between windows to merge
    
    Returns:
        Merged list of break windows
    """
    if not windows:
        return []
    
    # Sort by start time
    sorted_windows = sorted(windows, key=lambda w: w.start)
    
    merged: list[BreakWindow] = [sorted_windows[0]]
    
    for window in sorted_windows[1:]:
        last = merged[-1]
        gap = window.start - last.end
        
        if gap <= merge_gap_seconds:
            # Merge: extend the last window to include this one
            merged[-1] = BreakWindow(start=last.start, end=max(last.end, window.end))
        else:
            # Keep as separate window
            merged.append(window)
    
    return merged


def filter_windows_by_max_duration(
    windows: list[BreakWindow],
    max_duration: float,
) -> list[BreakWindow]:
    """Filter out break windows longer than maximum duration.
    
    Prevents runaway commercial windows caused by incorrect pairing.
    
    Args:
        windows: List of break windows
        max_duration: Maximum duration in seconds
    
    Returns:
        Filtered list with only windows <= max_duration
    """
    filtered: list[BreakWindow] = []
    
    for window in windows:
        duration = window.end - window.start
        if duration <= max_duration:
            filtered.append(window)
    
    return filtered


def filter_min_duration(
    windows: list[BreakWindow],
    min_duration: float,
    min_duration_after: float | None = None,
    after_seconds: float | None = None,
) -> list[BreakWindow]:
    """Filter out break windows shorter than minimum duration.
    
    Supports dual thresholds: different minimum durations before/after a time threshold.
    This prevents micro-blips, zero-length windows, and rounding artifacts.
    
    Args:
        windows: List of break windows
        min_duration: Minimum duration in seconds (for all breaks, or early breaks if dual-threshold)
        min_duration_after: Optional minimum duration for breaks starting after threshold
        after_seconds: Optional time threshold in seconds
    
    Returns:
        Filtered list of break windows
    """
    filtered: list[BreakWindow] = []
    
    # Determine if dual-threshold mode is active
    use_dual_threshold = min_duration_after is not None and after_seconds is not None
    
    for window in windows:
        duration = window.end - window.start
        
        if use_dual_threshold:
            # Dual-threshold: apply different minimums based on start time
            if window.start < after_seconds:
                # Early break: use standard threshold
                if duration >= min_duration:
                    filtered.append(window)
            else:
                # Later break: use lenient threshold
                if duration >= min_duration_after:
                    filtered.append(window)
        else:
            # Single threshold mode (current behavior)
            if duration >= min_duration:
                filtered.append(window)
    
    return filtered


def merge_breaks_by_gap(
    breaks: list[BreakWindow],
    min_gap_seconds: float,
) -> list[BreakWindow]:
    """Merge breaks that are too close together.
    
    If the gap between the end of one break and the start of the next is less than
    min_gap_seconds, merge them into a single break. This is useful for collapsing
    noisy clusters of detections into logical breaks.
    
    Args:
        breaks: List of break windows
        min_gap_seconds: Minimum gap required between breaks (seconds)
    
    Returns:
        Merged list of break windows
    """
    if not breaks:
        return []
    
    # Sort by start time
    sorted_breaks = sorted(breaks, key=lambda b: b.start)
    
    merged: list[BreakWindow] = [sorted_breaks[0]]
    
    for current in sorted_breaks[1:]:
        previous = merged[-1]
        gap = current.start - previous.end
        
        if gap < min_gap_seconds:
            # Merge: extend previous break to include current
            merged[-1] = BreakWindow(start=previous.start, end=max(previous.end, current.end))
        else:
            # Gap is sufficient, keep as separate break
            merged.append(current)
    
    return merged


def process_file(
    file_path: Path,
    black_min_duration: float,
    black_threshold: float,
    silence_min_duration: float,
    silence_noise_db: float,
    exclude_edge_seconds: float,
    merge_gap_seconds: float,
    min_break_duration: float,
    min_break_duration_after: float | None,
    after_seconds: float | None,
    skip_first_breaks: int,
    min_gap_between_breaks: float | None,
    max_break_duration: float | None,
    require_silence: bool,
    debug: bool = False,
) -> tuple[list[BreakWindow] | None, str | None]:
    """Process a single media file to detect commercial breaks.
    
    Returns:
        (break_windows, error_message)
        break_windows is None if processing failed
        error_message is None if processing succeeded
    """
    # Get duration
    duration = ffmpeg_detect.get_duration(file_path)
    if duration is None:
        return None, "Could not determine duration"
    
    # Detect black segments (always required)
    black_segments = ffmpeg_detect.detect_black_segments(
        file_path,
        black_min_duration,
        black_threshold,
    )
    
    if debug:
        print(f"  [DEBUG] Black segments detected: {len(black_segments)}")
    
    # Detect silence segments (only if needed)
    silence_segments: list[Segment] = []
    if require_silence:
        silence_segments = ffmpeg_detect.detect_silence_segments(
            file_path,
            silence_min_duration,
            silence_noise_db,
        )
        if debug:
            print(f"  [DEBUG] Silence segments detected: {len(silence_segments)}")
    
    # Create break windows
    windows = create_break_windows(black_segments, silence_segments, require_silence)
    
    if debug:
        print(f"  [DEBUG] Windows after pairing: {len(windows)}")
    
    # Filter max duration (must come before merging to catch runaway windows)
    if max_break_duration is not None:
        before_count = len(windows)
        windows = filter_windows_by_max_duration(windows, max_break_duration)
        removed = before_count - len(windows)
        if debug:
            print(f"  [DEBUG] Windows after max-duration filter: {len(windows)} (removed {removed})")
    
    # Filter edge regions
    windows = filter_edge_regions(windows, exclude_edge_seconds, duration)
    
    if debug:
        print(f"  [DEBUG] Windows after edge filter: {len(windows)}")
    
    # Merge nearby windows
    windows = merge_nearby_windows(windows, merge_gap_seconds)
    
    if debug:
        print(f"  [DEBUG] Windows after merge: {len(windows)}")
    
    # Filter minimum duration (with optional dual threshold)
    windows = filter_min_duration(windows, min_break_duration, min_break_duration_after, after_seconds)
    
    if debug:
        print(f"  [DEBUG] Windows after min-duration filter: {len(windows)}")
    
    # Skip first N breaks if requested
    if skip_first_breaks > 0 and len(windows) > skip_first_breaks:
        if debug:
            print(f"  [DEBUG] Skipping first {skip_first_breaks} break(s)")
        windows = windows[skip_first_breaks:]
    
    # Merge breaks that are too close together (optional final step)
    if min_gap_between_breaks is not None and min_gap_between_breaks > 0:
        if debug:
            print(f"  [DEBUG] Windows before gap-based merge: {len(windows)}")
        windows = merge_breaks_by_gap(windows, min_gap_between_breaks)
        if debug:
            print(f"  [DEBUG] Windows after gap-based merge: {len(windows)}")
    
    # Apply max duration filter again after gap-based merge (catches runaway merged windows)
    if max_break_duration is not None:
        before_count = len(windows)
        windows = filter_windows_by_max_duration(windows, max_break_duration)
        removed = before_count - len(windows)
        if debug and removed > 0:
            print(f"  [DEBUG] Windows after final max-duration filter: {len(windows)} (removed {removed})")
    
    if debug:
        if windows:
            print(f"  [DEBUG] Final breaks:")
            for i, w in enumerate(windows, 1):
                start_time = seconds_to_timecode(w.start)
                end_time = seconds_to_timecode(w.end)
                dur = w.end - w.start
                print(f"  [DEBUG]   {i}. {w.start:.3f}s ({start_time}) - {w.end:.3f}s ({end_time}) [duration: {dur:.3f}s]")
    
    return windows, None


def write_metadata_json(
    file_path: Path,
    breaks: list[BreakWindow],
    dry_run: bool = False,
) -> None:
    """Write metadata JSON file next to the media file.
    
    Args:
        file_path: Path to media file
        breaks: List of break windows
        dry_run: If True, print instead of writing
    """
    # Create JSON structure
    metadata = {
        "version": 1,
        "breaks": [window.to_dict() for window in breaks],
    }
    
    json_path = file_path.with_suffix(".json")
    json_content = json.dumps(metadata, indent=2)
    
    if dry_run:
        print(f"[DRY RUN] Would write {json_path}:")
        print(json_content)
        print()
    else:
        json_path.write_text(json_content, encoding="utf-8")


def find_media_files(
    directory: Path,
    extension: str,
    recursive: bool,
) -> list[Path]:
    """Find all media files in directory.
    
    Args:
        directory: Directory to search
        extension: File extension (without dot)
        recursive: Whether to search recursively
    
    Returns:
        Sorted list of media file paths
    """
    ext_with_dot = f".{extension}"
    
    if recursive:
        files = list(directory.rglob(f"*{ext_with_dot}"))
    else:
        files = list(directory.glob(f"*{ext_with_dot}"))
    
    # Filter to only files (not directories)
    files = [f for f in files if f.is_file()]
    
    # Sort for deterministic ordering
    files.sort(key=lambda p: str(p).lower())
    
    return files


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate commercial break metadata for media files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--path",
        type=Path,
        required=True,
        help="Directory containing media files",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recurse into subfolders",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .json files (default: skip if exists)",
    )
    parser.add_argument(
        "--ext",
        type=str,
        default="mp4",
        help="File extension to process (default: mp4)",
    )
    parser.add_argument(
        "--exclude-edge-seconds",
        type=float,
        default=90.0,
        help="Ignore detections within first/last N seconds (default: 90)",
    )
    parser.add_argument(
        "--black-min-duration",
        type=float,
        default=1.5,
        help="Minimum black duration in seconds (default: 1.5)",
    )
    parser.add_argument(
        "--black-threshold",
        type=float,
        default=0.98,
        help="Black detection threshold (picture_black_ratio, default: 0.98)",
    )
    parser.add_argument(
        "--silence-min-duration",
        type=float,
        default=0.4,
        help="Minimum silence duration in seconds (default: 0.4)",
    )
    parser.add_argument(
        "--silence-noise-db",
        type=float,
        default=-38.0,
        help="Silence noise threshold in dB (default: -38)",
    )
    parser.add_argument(
        "--merge-gap-seconds",
        type=float,
        default=0.4,
        help="Merge adjacent breaks separated by <= this gap (default: 0.4)",
    )
    parser.add_argument(
        "--min-break-duration",
        type=float,
        default=1.0,
        help="Minimum break duration to keep after filtering (default: 1.0)",
    )
    parser.add_argument(
        "--min-break-duration-after",
        type=float,
        default=None,
        help="Minimum break duration for breaks starting after threshold (e.g., 0.35)",
    )
    parser.add_argument(
        "--after-seconds",
        type=float,
        default=None,
        help="Time threshold in seconds for dual-duration mode (e.g., 300 for 5 minutes)",
    )
    parser.add_argument(
        "--skip-first-breaks",
        type=int,
        default=0,
        help="Skip first N breaks (useful for cold opens, default: 0)",
    )
    parser.add_argument(
        "--min-gap-between-breaks",
        type=float,
        default=None,
        help="Merge breaks separated by less than this gap in seconds (default: none)",
    )
    parser.add_argument(
        "--max-break-duration",
        type=float,
        default=None,
        help="Maximum break duration in seconds (discards longer windows, default: none)",
    )
    parser.add_argument(
        "--require-silence",
        action="store_true",
        help="Require overlap with silence detection (default: false, blackdetect-only)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output showing detection pipeline details",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files; print what would be written",
    )
    
    args = parser.parse_args()
    
    # Auto-enable debug when using --require-silence
    debug_mode = args.debug or args.require_silence
    
    # Validate path
    if not args.path.exists():
        print(f"Error: Path does not exist: {args.path}", file=sys.stderr)
        return 1
    
    if not args.path.is_dir():
        print(f"Error: Path is not a directory: {args.path}", file=sys.stderr)
        return 1
    
    # Check ffmpeg availability
    available, error_msg = ffmpeg_detect.check_ffmpeg_available()
    if not available:
        print(f"Error: {error_msg}", file=sys.stderr)
        return 1
    
    # Find media files
    print(f"Scanning for .{args.ext} files in: {args.path}")
    if args.recursive:
        print("  (recursive mode)")
    
    media_files = find_media_files(args.path, args.ext, args.recursive)
    
    if not media_files:
        print(f"No .{args.ext} files found.")
        return 0
    
    print(f"Found {len(media_files)} file(s)\n")
    
    # Process each file
    processed = 0
    skipped = 0
    failed = 0
    
    for file_path in media_files:
        json_path = file_path.with_suffix(".json")
        
        # Skip if JSON exists and not overwriting
        if json_path.exists() and not args.overwrite and not args.dry_run:
            print(f"SKIP: {file_path.name} (JSON already exists)")
            skipped += 1
            continue
        
        print(f"Processing: {file_path.name}...", end=" " if not debug_mode else "\n", flush=True)
        
        # Process file
        breaks, error = process_file(
            file_path,
            args.black_min_duration,
            args.black_threshold,
            args.silence_min_duration,
            args.silence_noise_db,
            args.exclude_edge_seconds,
            args.merge_gap_seconds,
            args.min_break_duration,
            args.min_break_duration_after,
            args.after_seconds,
            args.skip_first_breaks,
            args.min_gap_between_breaks,
            args.max_break_duration,
            args.require_silence,
            debug_mode,
        )
        
        if error is not None:
            print(f"FAILED ({error})")
            failed += 1
            continue
        
        # Write metadata
        assert breaks is not None
        print(f"{len(breaks)} break(s) detected")
        
        write_metadata_json(file_path, breaks, args.dry_run)
        processed += 1
    
    # Summary
    print(f"\nSummary:")
    print(f"  Processed: {processed}")
    print(f"  Skipped:   {skipped}")
    print(f"  Failed:    {failed}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
