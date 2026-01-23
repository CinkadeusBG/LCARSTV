from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .scanner import ScanResult, scan_media_dirs


def _count_media_files(repo_root: Path, media_dirs: tuple[Path, ...], extensions: tuple[str, ...]) -> int:
    """Fast count of media files in directories (excludes .json files).
    
    This is used to detect if the media library has changed without doing a full scan.
    
    Args:
        repo_root: Repository root path
        media_dirs: Tuple of media directory paths
        extensions: Tuple of allowed file extensions
    
    Returns:
        Total count of media files
    """
    allowed = {e.lower() for e in extensions}
    count = 0
    
    for d in media_dirs:
        full = (repo_root / d).resolve() if not d.is_absolute() else d
        if not full.exists():
            continue
        if full.is_file():
            # If the user mistakenly points at a file, count it.
            if full.suffix.lower() in allowed:
                count += 1
            continue
        for p in full.rglob("*"):
            if p.is_file() and p.suffix.lower() in allowed:
                count += 1
    
    return count


@dataclass
class CatalogEntry:
    """Cached media scan results for a single channel."""
    call_sign: str
    media_dirs: tuple[str, ...]  # Store as strings for JSON serialization
    files: tuple[str, ...]  # Store as strings for JSON serialization
    file_count: int
    scanned_at: str  # ISO format timestamp


@dataclass
class MediaCatalog:
    """Persistent cache for media directory scans.
    
    Avoids expensive directory walks on every startup by caching scan results.
    Automatically rescans when the file count changes (media added/removed).
    """
    
    path: Path
    debug: bool = False
    
    _loaded: bool = False
    _catalogs: dict[str, CatalogEntry] | None = None
    
    def _ensure_loaded(self) -> None:
        """Load catalog from disk if not already loaded."""
        if self._loaded:
            return
        self._loaded = True
        self._catalogs = {}
        
        try:
            if not self.path.exists():
                return
            
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            
            if not isinstance(data, dict):
                return
            
            catalogs_data = data.get("catalogs")
            if not isinstance(catalogs_data, dict):
                return
            
            # Parse each catalog entry
            for call_sign, entry_data in catalogs_data.items():
                if not isinstance(entry_data, dict):
                    continue
                
                try:
                    self._catalogs[call_sign] = CatalogEntry(
                        call_sign=call_sign,
                        media_dirs=tuple(entry_data.get("media_dirs", [])),
                        files=tuple(entry_data.get("files", [])),
                        file_count=int(entry_data.get("file_count", 0)),
                        scanned_at=entry_data.get("scanned_at", ""),
                    )
                except (TypeError, ValueError):
                    # Skip malformed entries
                    continue
        
        except Exception as e:
            # Corrupt catalog should not crash the app
            if self.debug:
                print(f"[debug] media-catalog: failed to load {self.path}: {e}")
            self._catalogs = {}
    
    def _save(self) -> None:
        """Save catalog to disk."""
        self._ensure_loaded()
        assert self._catalogs is not None
        
        # Convert to JSON-serializable format
        catalogs_data = {}
        for call_sign, entry in self._catalogs.items():
            catalogs_data[call_sign] = {
                "media_dirs": list(entry.media_dirs),
                "files": list(entry.files),
                "file_count": entry.file_count,
                "scanned_at": entry.scanned_at,
            }
        
        data = {
            "version": 1,
            "catalogs": catalogs_data,
        }
        
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
    
    def get_or_scan(
        self,
        call_sign: str,
        repo_root: Path,
        media_dirs: tuple[Path, ...],
        extensions: tuple[str, ...],
    ) -> ScanResult:
        """Get cached scan results or perform a new scan if needed.
        
        Smart rescan logic:
        1. Check if catalog exists for this channel
        2. If exists, compare current file count with cached count
        3. If counts match, use cache (instant!)
        4. If counts differ, rescan and update cache
        5. If no cache, scan and create cache
        
        Args:
            call_sign: Channel call sign
            repo_root: Repository root path
            media_dirs: Tuple of media directory paths
            extensions: Tuple of allowed file extensions
        
        Returns:
            ScanResult with files tuple
        """
        self._ensure_loaded()
        assert self._catalogs is not None
        
        # Convert media_dirs to strings for comparison
        media_dirs_str = tuple(str(d) for d in media_dirs)
        
        # Check if we have a cached entry
        cached = self._catalogs.get(call_sign)
        
        # Fast count current media files
        current_count = _count_media_files(repo_root, media_dirs, extensions)
        
        # Decide if we can use cache
        use_cache = False
        if cached is not None:
            # Check if media_dirs configuration changed
            if cached.media_dirs == media_dirs_str:
                # Check if file count matches
                if cached.file_count == current_count:
                    use_cache = True
                    if self.debug:
                        print(
                            f"[debug] media-catalog: {call_sign} using cache "
                            f"({cached.file_count} files match current count)"
                        )
                else:
                    if self.debug:
                        print(
                            f"[debug] media-catalog: {call_sign} cache invalid "
                            f"(cached: {cached.file_count}, current: {current_count}) - rescanning"
                        )
            else:
                if self.debug:
                    print(
                        f"[debug] media-catalog: {call_sign} media_dirs changed - rescanning"
                    )
        else:
            if self.debug:
                print(f"[debug] media-catalog: {call_sign} no cache found - scanning")
        
        if use_cache and cached is not None:
            # Use cached results - convert strings back to Paths
            files = tuple(Path(f) for f in cached.files)
            return ScanResult(files=files)
        
        # Need to scan
        if self.debug:
            print(f"[debug] media-catalog: {call_sign} scanning {len(media_dirs)} director{'y' if len(media_dirs) == 1 else 'ies'}...")
        
        scan_result = scan_media_dirs(repo_root, media_dirs, extensions)
        
        # Cache the results
        now = datetime.utcnow().isoformat() + "Z"
        self._catalogs[call_sign] = CatalogEntry(
            call_sign=call_sign,
            media_dirs=media_dirs_str,
            files=tuple(str(f) for f in scan_result.files),
            file_count=len(scan_result.files),
            scanned_at=now,
        )
        
        self._save()
        
        if self.debug:
            print(
                f"[debug] media-catalog: {call_sign} scanned {len(scan_result.files)} files, "
                f"cache updated"
            )
        
        return scan_result
    
    def invalidate_channel(self, call_sign: str) -> None:
        """Invalidate (remove) cached entry for a channel.
        
        Used for error recovery when a cached file is found to be missing.
        
        Args:
            call_sign: Channel call sign to invalidate
        """
        self._ensure_loaded()
        assert self._catalogs is not None
        
        if call_sign in self._catalogs:
            del self._catalogs[call_sign]
            self._save()
            
            if self.debug:
                print(f"[debug] media-catalog: {call_sign} cache invalidated")
