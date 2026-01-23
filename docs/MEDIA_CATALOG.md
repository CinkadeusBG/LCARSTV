# Media Catalog Cache

## Overview

The media catalog cache is a performance optimization that dramatically reduces startup time by caching the results of media directory scans. Instead of walking the entire directory tree on every startup, the system:

1. **First startup**: Scans media directories and caches the file list
2. **Subsequent startups**: Uses the cached file list (near-instant!)
3. **Auto-rescan**: Detects when media has been added/removed and rescans automatically

## How It Works

### Smart Rescan Logic

The catalog uses a **file count comparison** to detect changes:

1. Quickly count media files in each channel's directories (fast operation)
2. Compare with the cached file count
3. If counts match → use cache (instant)
4. If counts differ → rescan and update cache

This approach provides:
- ✅ Instant startup when no media changes
- ✅ Automatic detection when you add/remove media
- ✅ No manual intervention required
- ✅ Per-channel granularity (only rescans changed channels)

### Cache Structure

The catalog is stored in `data/media_catalog.json`:

```json
{
  "version": 1,
  "catalogs": {
    "WTNG": {
      "media_dirs": ["media/WTNG"],
      "files": [
        "z:/media/wtng/episode1.mp4",
        "z:/media/wtng/episode2.mp4",
        ...
      ],
      "file_count": 150,
      "scanned_at": "2026-01-22T18:29:00Z"
    }
  }
}
```

### Error Recovery

If a cached file is found to be missing (e.g., media moved/deleted):

1. The system detects the missing file during playback
2. Automatically invalidates the cache for that channel
3. Displays a warning message
4. Requests a restart to rescan

This ensures the cache never becomes permanently stale.

## Performance Impact

### Before (without catalog cache)
- **First startup**: 5-10 seconds (directory scan)
- **Second startup**: 5-10 seconds (directory scan again)
- **Every startup**: Full directory walk

### After (with catalog cache)
- **First startup**: 5-10 seconds (initial scan + cache creation)
- **Second startup**: ~0.5 seconds (95% faster!)
- **Startup with changes**: 5-10 seconds (automatic rescan)
- **Startup without changes**: ~0.5 seconds (cache hit)

## File Counting vs Full Scan

The system uses two different operations:

1. **File counting** (fast): Uses `rglob()` to count media files
   - Skips `.json` metadata files automatically
   - Only counts files, doesn't read paths
   - Very fast even with thousands of files

2. **Full scan** (slower): Uses `scan_media_dirs()` to get full file list
   - Reads all file paths
   - Sorts deterministically
   - Returns complete file list
   - Only runs when needed

## Cache Location

- **Path**: `data/media_catalog.json`
- **Persistence**: Saved to disk (survives restarts)
- **Git**: Excluded from version control (in `.gitignore`)
- **Safe to delete**: If deleted, will rescan on next startup

## Invalidation Scenarios

The cache is automatically invalidated (triggering rescan) when:

1. **File count changed**: Added or removed media files
2. **Media dirs changed**: Channel configuration modified
3. **Missing files detected**: Cached file doesn't exist
4. **Manual deletion**: User deletes `data/media_catalog.json`

## Debug Output

With debug mode enabled (`debug: true` in settings):

```
[debug] media-catalog: WTNG using cache (150 files match current count)
```

Or when rescanning:

```
[debug] media-catalog: WTNG cache invalid (cached: 150, current: 155) - rescanning
[debug] media-catalog: WTNG scanning 1 directory...
[debug] media-catalog: WTNG scanned 155 files, cache updated
```

## Integration

The catalog integrates seamlessly with existing systems:

- **DurationCache**: Still used for file durations (separate cache)
- **StateStore**: Channel state still persisted separately
- **Scanner**: Catalog wraps the scanner, doesn't replace it
- **ChannelRuntime**: Receives catalog reference for error recovery

## Technical Details

### MediaCatalog Class

Located in `lcarstv/core/media_catalog.py`:

- `get_or_scan()`: Main entry point, returns cached or fresh scan
- `invalidate_channel()`: Removes cache entry for a channel
- `_count_media_files()`: Fast file counting utility
- `_ensure_loaded()`: Lazy-loads catalog from disk
- `_save()`: Atomically saves catalog to disk

### ChannelRuntime Integration

Each `ChannelRuntime` receives a reference to the catalog:

```python
channels[ch.call_sign] = ChannelRuntime(
    ...
    catalog=catalog,  # Reference for error recovery
)
```

This allows channels to invalidate their cache entry if missing files are detected during playback.

## Maintenance

### No maintenance required!

The system is fully automatic:
- Caches on first scan
- Auto-rescans when media changes
- Self-recovers from stale cache
- No user intervention needed

### Optional: Force Rescan

To force a complete rescan:

1. Delete `data/media_catalog.json`
2. Restart the application

This is rarely needed but can be useful if you suspect cache issues.

## Compatibility

- **Backward compatible**: Works with existing installations
- **No migration needed**: Creates cache on first run
- **Safe rollback**: Delete cache file to revert to always-scan behavior
- **Cross-platform**: Works on Windows, Linux, macOS

## Example Workflow

### Adding New Episodes

1. **Add files** to your media directory
   ```
   media/WTNG/
     ├── episode150.mp4  (existing)
     ├── episode151.mp4  (new!)
     ├── episode152.mp4  (new!)
   ```

2. **Restart the application**
   - System counts files: 152 (was 150)
   - Cache invalidated automatically
   - Rescans and updates: 5-10 seconds

3. **Future startups**
   - Uses new cached count (152 files)
   - Instant startup: ~0.5 seconds

### Moving Media

If you move media to a different location:

1. Update `channels.json` with new `media_dirs`
2. Restart application
3. System detects config change
4. Rescans automatically

## Limitations

- **File count only**: Doesn't detect file renames (count stays same)
- **Directory operations**: Still needs to count files (fast but not free)
- **No deep validation**: Trusts cache if count matches

These limitations are acceptable trade-offs for the massive startup performance gain.

## Related Systems

- **DurationCache** (`durations.json`): Caches FFprobe results
- **StateStore** (`state.json`): Persists channel/scheduler state
- **Scanner** (`scanner.py`): Performs actual directory scans

All three work together to optimize startup and runtime performance.
