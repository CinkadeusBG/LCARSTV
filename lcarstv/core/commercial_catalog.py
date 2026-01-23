"""Persistent cache for commercial directory scans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _count_commercial_files(commercials_dir: Path, extensions: tuple[str, ...]) -> int:
    """Fast count of commercial files in directory.
    
    This is used to detect if the commercial library has changed without doing a full scan.
    
    Args:
        commercials_dir: Directory containing commercial files
        extensions: Tuple of allowed file extensions
    
    Returns:
        Total count of commercial files
    """
    if not commercials_dir.exists() or not commercials_dir.is_dir():
        return 0
    
    allowed = {e.lower() for e in extensions}
    count = 0
    
    try:
        for p in commercials_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in allowed:
                count += 1
    except Exception:
        # Best-effort: don't crash on permission errors, etc.
        pass
    
    return count


@dataclass
class CommercialCatalog:
    """Persistent cache for commercial directory scans.
    
    Avoids expensive directory walks on every startup by caching scan results.
    Automatically rescans when the file count changes (commercials added/removed).
    """
    
    path: Path
    debug: bool = False
    
    _loaded: bool = False
    _cached_files: tuple[str, ...] | None = None
    _cached_count: int = 0
    _scanned_at: str = ""
    
    def _ensure_loaded(self) -> None:
        """Load catalog from disk if not already loaded."""
        if self._loaded:
            return
        self._loaded = True
        
        try:
            if not self.path.exists():
                return
            
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            
            if not isinstance(data, dict):
                return
            
            files = data.get("files", [])
            if isinstance(files, list):
                self._cached_files = tuple(files)
                self._cached_count = len(self._cached_files)
                self._scanned_at = data.get("scanned_at", "")
        
        except Exception as e:
            # Corrupt catalog should not crash the app
            if self.debug:
                print(f"[debug] commercial-catalog: failed to load {self.path}: {e}")
    
    def _save(self, files: tuple[Path, ...]) -> None:
        """Save catalog to disk.
        
        Args:
            files: Tuple of commercial file paths to save
        """
        now = datetime.utcnow().isoformat() + "Z"
        
        data = {
            "version": 1,
            "file_count": len(files),
            "files": [str(f) for f in files],
            "scanned_at": now,
        }
        
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
        
        # Update in-memory cache
        self._cached_files = tuple(str(f) for f in files)
        self._cached_count = len(files)
        self._scanned_at = now
    
    def get_or_scan(
        self,
        commercials_dir: Path,
        extensions: tuple[str, ...],
    ) -> tuple[Path, ...]:
        """Get cached commercial files or perform a new scan if needed.
        
        Smart rescan logic:
        1. Check if catalog exists
        2. If exists, compare current file count with cached count
        3. If counts match, use cache (instant!)
        4. If counts differ, rescan and update cache
        5. If no cache, scan and create cache
        
        Args:
            commercials_dir: Directory containing commercial files
            extensions: Tuple of allowed file extensions
        
        Returns:
            Tuple of commercial file paths (empty if directory missing/invalid)
        """
        self._ensure_loaded()
        
        if not commercials_dir.exists() or not commercials_dir.is_dir():
            if self.debug:
                print(f"[debug] commercial-catalog: directory invalid or missing: {commercials_dir}")
            return ()
        
        # Fast count current commercial files
        current_count = _count_commercial_files(commercials_dir, extensions)
        
        # Decide if we can use cache
        use_cache = False
        if self._cached_files is not None and self._cached_count > 0:
            # Check if file count matches
            if self._cached_count == current_count:
                use_cache = True
                if self.debug:
                    print(
                        f"[debug] commercial-catalog: using cache "
                        f"({self._cached_count} files match current count)"
                    )
            else:
                if self.debug:
                    print(
                        f"[debug] commercial-catalog: cache invalid "
                        f"(cached: {self._cached_count}, current: {current_count}) - rescanning"
                    )
        else:
            if self.debug:
                print(f"[debug] commercial-catalog: no cache found - scanning")
        
        if use_cache and self._cached_files is not None:
            # Use cached results - convert strings back to Paths
            files = tuple(Path(f) for f in self._cached_files)
            return files
        
        # Need to scan
        if self.debug:
            print(f"[debug] commercial-catalog: scanning {commercials_dir}...")
        
        try:
            # Scan for media files
            files_list: list[Path] = []
            for ext in extensions:
                # Use rglob to find files recursively
                files_list.extend(commercials_dir.rglob(f"*{ext}"))
            
            # Filter to only actual files (not directories) and sort for determinism
            files_list = [f for f in files_list if f.is_file()]
            files_list.sort(key=lambda p: str(p).lower())
            
            files = tuple(files_list)
            
            # Cache the results
            self._save(files)
            
            if self.debug:
                print(
                    f"[debug] commercial-catalog: scanned {len(files)} files, "
                    f"cache updated"
                )
            
            return files
        
        except Exception as e:
            # Best-effort: never throw, just log and return empty
            if self.debug:
                print(f"[debug] commercial-catalog: scan failed: {e}")
            return ()
