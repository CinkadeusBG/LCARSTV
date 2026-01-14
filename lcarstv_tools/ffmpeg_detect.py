"""FFmpeg/FFprobe wrappers and stderr parsing for commercial break detection."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .types import Segment


def check_ffmpeg_available() -> tuple[bool, str]:
    """Check if ffmpeg and ffprobe are available in PATH.
    
    Returns:
        (success, error_message)
    """
    for cmd in ["ffmpeg", "ffprobe"]:
        try:
            subprocess.run(
                [cmd, "-version"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except FileNotFoundError:
            return False, f"{cmd} not found in PATH. Please install FFmpeg."
        except Exception as e:
            return False, f"Error checking {cmd}: {e}"
    return True, ""


def get_duration(file_path: Path) -> float | None:
    """Get duration of media file in seconds using ffprobe.
    
    Returns:
        Duration in seconds, or None if unable to determine.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        
        duration_str = result.stdout.strip()
        if not duration_str:
            return None
            
        return float(duration_str)
    except (ValueError, subprocess.TimeoutExpired):
        return None
    except Exception:
        return None


def detect_black_segments(
    file_path: Path,
    black_min_duration: float,
    black_threshold: float,
) -> list[Segment]:
    """Detect black segments using ffmpeg blackdetect filter.
    
    Args:
        file_path: Path to media file
        black_min_duration: Minimum duration in seconds (d parameter)
        black_threshold: Black picture ratio threshold (pic_th parameter)
    
    Returns:
        List of black segments
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i", str(file_path),
                "-vf", f"blackdetect=d={black_min_duration}:pic_th={black_threshold}",
                "-an",
                "-f", "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,  # 10 minutes max
        )
        
        return parse_black_segments(result.stderr)
    except subprocess.TimeoutExpired:
        return []
    except Exception:
        return []


def parse_black_segments(stderr: str) -> list[Segment]:
    """Parse blackdetect output from ffmpeg stderr.
    
    Example line:
        [blackdetect @ 0x...] black_start:123.456 black_end:125.789 black_duration:2.333
    
    Returns:
        List of Segment objects
    """
    segments: list[Segment] = []
    # Pattern to match black_start and black_end
    pattern = r"black_start:(\d+\.?\d*)\s+black_end:(\d+\.?\d*)"
    
    for match in re.finditer(pattern, stderr):
        try:
            start = float(match.group(1))
            end = float(match.group(2))
            if end > start:
                segments.append(Segment(start=start, end=end))
        except (ValueError, IndexError):
            continue
    
    return segments


def detect_silence_segments(
    file_path: Path,
    silence_min_duration: float,
    silence_noise_db: float,
) -> list[Segment]:
    """Detect silence segments using ffmpeg silencedetect filter.
    
    Args:
        file_path: Path to media file
        silence_min_duration: Minimum silence duration in seconds
        silence_noise_db: Noise threshold in dB
    
    Returns:
        List of silence segments
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i", str(file_path),
                "-af", f"silencedetect=noise={silence_noise_db}dB:d={silence_min_duration}",
                "-vn",
                "-f", "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,  # 10 minutes max
        )
        
        return parse_silence_segments(result.stderr)
    except subprocess.TimeoutExpired:
        return []
    except Exception:
        return []


def parse_silence_segments(stderr: str) -> list[Segment]:
    """Parse silencedetect output from ffmpeg stderr.
    
    Example lines:
        [silencedetect @ 0x...] silence_start: 123.456
        [silencedetect @ 0x...] silence_end: 125.789 | silence_duration: 2.333
    
    Uses a state machine to pair starts with their corresponding ends sequentially.
    This avoids misalignment issues when parsing by index.
    
    Returns:
        List of Segment objects
    """
    segments: list[Segment] = []
    
    # Robust regex patterns that handle negatives and decimals
    start_pattern = r"silence_start:\s*(-?\d+(?:\.\d+)?)"
    end_pattern = r"silence_end:\s*(-?\d+(?:\.\d+)?)"
    
    # State machine: track current silence start
    current_start: float | None = None
    
    # Process stderr line by line to maintain order
    for line in stderr.split('\n'):
        # Check for silence_start
        start_match = re.search(start_pattern, line)
        if start_match:
            try:
                current_start = float(start_match.group(1))
            except ValueError:
                pass
            continue
        
        # Check for silence_end (must follow a start)
        end_match = re.search(end_pattern, line)
        if end_match and current_start is not None:
            try:
                end = float(end_match.group(1))
                # Only create segment if end > start (valid segment)
                if end > current_start:
                    segments.append(Segment(start=current_start, end=end))
                # Reset state after pairing
                current_start = None
            except ValueError:
                pass
    
    return segments
