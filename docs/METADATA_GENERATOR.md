# Commercial Break Metadata Generator

A standalone tool to detect commercial break windows in video files and generate metadata JSON files.

## Overview

This tool scans `.mp4` files (or other video formats), detects fade-to-black + silence segments, and outputs JSON metadata files with commercial break timestamps. The metadata files are written next to each video file with the same basename.

**Important:** This tool does **not** modify any existing LCARSTV playback logic. It is a standalone helper utility.

## Prerequisites

- Python 3.11 or higher
- **FFmpeg** and **FFprobe** must be installed and available in your system PATH
  - Download from: https://ffmpeg.org/download.html
  - On Windows, ensure `ffmpeg.exe` and `ffprobe.exe` are in your PATH

To verify FFmpeg is installed:
```powershell
ffmpeg -version
ffprobe -version
```

## Usage

### Basic Usage

Generate metadata for all `.mp4` files in a directory:

```powershell
python -m lcarstv_tools.generate_metadata --path "Z:\media\WTNG"
```

### Recursive Mode

Scan subdirectories recursively:

```powershell
python -m lcarstv_tools.generate_metadata --path "Z:\media" --recursive
```

### Overwrite Existing Files

By default, the tool skips files that already have `.json` metadata. To regenerate:

```powershell
python -m lcarstv_tools.generate_metadata --path "Z:\media\WTNG" --overwrite
```

### Dry Run

Preview what would be generated without writing files:

```powershell
python -m lcarstv_tools.generate_metadata --path "Z:\media\WTNG" --dry-run
```

### Different File Extensions

Process files with a different extension (e.g., `.mkv`):

```powershell
python -m lcarstv_tools.generate_metadata --path "Z:\media" --ext mkv
```

## Command-Line Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--path` | Required | - | Directory containing media files |
| `--recursive` | Flag | False | Recurse into subfolders |
| `--overwrite` | Flag | False | Overwrite existing `.json` files |
| `--ext` | String | `mp4` | File extension to process |
| `--exclude-edge-seconds` | Float | 90.0 | Ignore detections within first/last N seconds |
| `--black-min-duration` | Float | **1.5** | Minimum black duration in seconds |
| `--black-threshold` | Float | **0.98** | Black detection threshold (picture_black_ratio) |
| `--silence-min-duration` | Float | 0.4 | Minimum silence duration in seconds (only used with --require-silence) |
| `--silence-noise-db` | Float | -38.0 | Silence noise threshold in dB (only used with --require-silence) |
| `--merge-gap-seconds` | Float | 0.4 | Merge breaks separated by ≤ this gap |
| `--min-break-duration` | Float | **1.0** | Minimum break duration to keep after filtering |
| `--require-silence` | Flag | **False** | Require overlap with silence detection (more conservative) |
| `--dry-run` | Flag | False | Print what would be written without writing |

## Detection Strategy

### Default Mode: Blackdetect-Only

**By default**, the tool uses **blackdetect as the primary driver** for commercial break detection:

1. **Black Detection** - Uses FFmpeg's `blackdetect` filter to find fade-to-black segments
2. **Break Creation** - Black segments are directly converted to break windows
3. **Edge Filtering** - Removes detections near the start/end of the file (configurable)
4. **Merging** - Combines nearby break windows that are close together
5. **Duration Filtering** - Removes breaks shorter than minimum duration (prevents micro-blips)

This approach works well for classic TV content like **Star Trek: The Next Generation** where commercial breaks have fade-to-black with music stings playing over them.

### Optional Mode: Require Silence

With the `--require-silence` flag, the tool uses a **stricter two-pass approach**:

1. **Black Detection** - Uses FFmpeg's `blackdetect` filter
2. **Silence Detection** - Uses FFmpeg's `silencedetect` filter
3. **Overlap Pairing** - Only creates break windows where black and silence segments **actually overlap**
4. **Filtering** - Edge filtering, merging, and duration filtering as above

#### Overlap Logic

Two segments are considered overlapping if:
```
overlap_start = max(black.start, silence.start)
overlap_end = min(black.end, silence.end)
overlap_end > overlap_start  # Strict requirement
```

Adjacent segments that merely "touch" (e.g., one ends at 12.0s and the other starts at 12.0s) are **not** considered overlapping.

## Output Format

For each media file `movie.mp4`, a file `movie.json` is created in the same directory:

```json
{
  "version": 1,
  "breaks": [
    { "start": 123.456, "end": 145.678 },
    { "start": 456.789, "end": 478.901 }
  ]
}
```

- Timestamps are in seconds (float with 3 decimal places)
- Breaks are sorted in ascending order
- If no breaks are detected, the `breaks` array will be empty

## Tuning Parameters

If you're getting too many false positives or missing actual commercial breaks, try adjusting:

### Reduce False Positives
- Increase `--black-min-duration` (e.g., 1.0)
- Increase `--silence-min-duration` (e.g., 0.6)
- Decrease `--black-threshold` (e.g., 0.05 for stricter black detection)
- Increase `--exclude-edge-seconds` (e.g., 120 to ignore first/last 2 minutes)

### Catch More Breaks
- Decrease `--black-min-duration` (e.g., 0.5)
- Decrease `--silence-min-duration` (e.g., 0.3)
- Increase `--black-threshold` (e.g., 0.15 for more lenient black detection)
- Increase `--silence-noise-db` (e.g., -35 for louder silence threshold)

### Merge More Aggressively
- Increase `--merge-gap-seconds` (e.g., 1.0 to merge breaks separated by up to 1 second)

## Example Workflow

1. **Test on a single directory with dry-run:**
   ```powershell
   python -m lcarstv_tools.generate_metadata --path "Z:\media\test" --dry-run
   ```

2. **Review the output and adjust parameters if needed**

3. **Generate metadata for real:**
   ```powershell
   python -m lcarstv_tools.generate_metadata --path "Z:\media\test"
   ```

4. **Process entire library recursively:**
   ```powershell
   python -m lcarstv_tools.generate_metadata --path "Z:\media" --recursive
   ```

5. **Update existing metadata with new parameters:**
   ```powershell
   python -m lcarstv_tools.generate_metadata --path "Z:\media" --recursive --overwrite
   ```

## When to Use --require-silence

The `--require-silence` flag enables a more conservative detection mode that requires both black screens AND silence.

**Use blackdetect-only (default) for:**
- Classic TV shows with fade-to-black + music stings (e.g., Star Trek TNG, 80s/90s sitcoms)
- Content where commercial breaks have consistent fade patterns
- When you want maximum detection coverage

**Use --require-silence for:**
- Content with frequent scene fades (may cause false positives in default mode)
- When you want fewer, more conservative break detections
- Content where silence always accompanies commercial breaks

**Example with --require-silence:**
```powershell
python -m lcarstv_tools.generate_metadata --path "Z:\media" --require-silence
```

## Performance Notes

- Processing time depends on file size and duration
- **Default mode** (blackdetect-only): One FFmpeg pass per file (~10-15 seconds per hour of video)
- **With --require-silence**: Two FFmpeg passes per file (~20-30 seconds per hour of video)
- The tool processes files sequentially
- Typical speed on modern hardware

## Troubleshooting

### "ffmpeg not found in PATH"
Ensure FFmpeg is installed and added to your system PATH. After installation, restart your terminal/PowerShell.

### "Could not determine duration"
The file may be corrupted or in an unsupported format. Try playing it in a media player to verify.

### No breaks detected
- Try adjusting detection thresholds (see "Tuning Parameters")
- The file may not have obvious black + silence patterns
- Use `--dry-run` to preview detection output

### Too many false positives
- Increase minimum duration thresholds
- Decrease black threshold for stricter detection
- Increase edge exclusion seconds

## Notes

- Metadata files (`.json`) are **not** tracked in source control (add to `.gitignore` if needed)
- Media files are never modified; only metadata JSON files are created
- The tool assumes media files are static and will not be moved or renamed
- Existing LCARSTV playback logic is not affected by this tool
