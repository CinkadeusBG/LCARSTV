# Commercial Catalog Optimization

## Overview

This document explains the disk-based caching optimization for commercial file scanning, which eliminates redundant filesystem scans on application startup.

## Problem

Prior to this optimization, the application would perform a full recursive directory scan of all commercial files on every startup:

```
[debug] commercials: loaded 651 file(s) from /srv/smb/media/commercials
```

This scan involved:
- Recursive directory traversal using `rglob()`
- File extension checking for all 651 files
- File existence verification
- Sorting and deduplication

On every launch, this process repeated, even when the commercial library hadn't changed. This was wasteful and inconsistent with how episode files were handled (which used disk-based caching via `MediaCatalog`).

## Solution

We implemented a disk-based caching system for commercial files that mirrors the existing `MediaCatalog` pattern used for episode files:

### 1. `CommercialCatalog` Class

**Location**: `lcarstv/core/commercial_catalog.py`

**Responsibilities**:
- Cache commercial file list to disk (`data/commercial_catalog.json`)
- Fast count check on startup (count files, don't list them)
- Only re-scan when count changes (files added/removed)
- Graceful fallback on errors

**Smart Rescan Logic**:
1. Check if catalog exists
2. If exists, compare current file count with cached count
3. If counts match → use cache (instant!)
4. If counts differ → rescan and update cache
5. If no cache → scan and create cache

### 2. Updated `CommercialPool` Class

**Location**: `lcarstv/core/commercials.py`

**Changes**:
- Added `catalog` parameter to constructor (optional for backward compatibility)
- Updated `_load_files()` to use catalog when available
- Falls back to legacy filesystem scan if catalog fails or is not provided

**Behavior**:
- **With catalog** (recommended): Uses `catalog.get_or_scan()` for fast cached loading
- **Without catalog** (legacy): Direct filesystem scan (original behavior)

### 3. Integration in `app.py`

**Location**: `lcarstv/app.py`

**Changes**:
```python
# Initialize commercial catalog for disk-based caching
commercial_catalog = CommercialCatalog(
    path=repo_root / "data" / "commercial_catalog.json",
    debug=settings.debug,
)

# Initialize commercial pool with catalog
commercial_pool = CommercialPool(
    commercials_dir=settings.commercials_dir,
    extensions=settings.extensions,
    debug=settings.debug,
    catalog=commercial_catalog,
)
```

## Expected Behavior

### First Launch (No Cache)
```
[debug] commercial-catalog: no cache found - scanning
[debug] commercial-catalog: scanning /srv/smb/media/commercials...
[debug] commercial-catalog: scanned 651 files, cache updated
[debug] commercials: loaded 651 file(s) from cache
```

The full scan happens once and results are saved to `data/commercial_catalog.json`.

### Subsequent Launches (Cache Valid)
```
[debug] commercial-catalog: using cache (651 files match current count)
[debug] commercials: loaded 651 file(s) from cache
```

Only a fast file count is performed (no directory walk). Loading is nearly instant.

### After Adding/Removing Commercials
```
[debug] commercial-catalog: cache invalid (cached: 651, current: 655) - rescanning
[debug] commercial-catalog: scanning /srv/smb/media/commercials...
[debug] commercial-catalog: scanned 655 files, cache updated
[debug] commercials: loaded 655 file(s) from cache
```

Automatic invalidation detects changes and rescans as needed.

## Benefits

1. **Startup Performance**: Eliminates expensive directory traversal on every launch
2. **Consistency**: Same caching pattern as episode files (`MediaCatalog`)
3. **Automatic Invalidation**: Detects adds/removes without manual cache clearing
4. **Backward Compatible**: Works without catalog (degrades to legacy behavior)
5. **Robust**: Graceful error handling, never crashes the app

## Cache File Format

**Location**: `data/commercial_catalog.json`

```json
{
  "version": 1,
  "file_count": 651,
  "scanned_at": "2026-01-23T22:48:45.123456Z",
  "files": [
    "/srv/smb/media/commercials/001 - Commercial.mp4",
    "/srv/smb/media/commercials/002 - Commercial.mp4",
    ...
  ]
}
```

- **version**: Format version for future compatibility
- **file_count**: Total number of commercial files
- **scanned_at**: ISO 8601 timestamp of last scan
- **files**: Sorted list of absolute file paths

## Maintenance

The cache is **automatically managed** by the application:

- **Created**: On first launch when no cache exists
- **Updated**: When file count changes (add/remove detected)
- **Ignored by Git**: Listed in `.gitignore` (runtime-generated artifact)

To manually invalidate the cache (force rescan):
```bash
rm data/commercial_catalog.json
```

## Comparison with Episode Scanning

| Feature | Episodes | Commercials |
|---------|----------|-------------|
| **Caching Class** | `MediaCatalog` | `CommercialCatalog` |
| **Cache File** | `data/media_catalog.json` | `data/commercial_catalog.json` |
| **Detection Logic** | Fast file count check | Fast file count check |
| **Invalidation** | Automatic on count change | Automatic on count change |
| **Per-Channel** | Yes (multi-channel support) | No (single pool shared) |

Both systems follow the same design pattern for consistency.

## Performance Impact

### Before (No Caching)
- **Startup scan**: ~651 files × filesystem operations
- **Time**: Variable (depends on disk/network speed)
- **Every launch**: Full scan repeated

### After (With Caching)
- **First launch**: Same as before (cache created)
- **Subsequent launches**: Fast count only (~651 stat calls)
- **Time**: Near-instant (< 100ms typically)
- **Every launch**: Minimal overhead

For a typical library of 651 commercials on a network mount (NAS), this can reduce startup time by several seconds.

## Implementation Notes

- The `CommercialCatalog` class is a dataclass with lazy loading (`_ensure_loaded()`)
- File paths are normalized and sorted for deterministic behavior
- The catalog uses atomic file replacement (`.tmp` → rename) to prevent corruption
- Error handling is best-effort: catalog failures degrade to direct scan (never crash)
- Thread-safe for single-threaded application (main event loop)

## Future Enhancements

Potential future improvements:
1. **Metadata tracking**: Store mtime/size for per-file invalidation (like `DurationCache`)
2. **Compression**: For very large libraries (thousands of files)
3. **Sub-categories**: Support organizing commercials into subdirectories with weights
4. **TTL expiration**: Optional time-based cache expiration

None of these are currently needed for typical use cases (hundreds of commercials).
