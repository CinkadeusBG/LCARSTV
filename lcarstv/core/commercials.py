"""Commercial pool management for LCARSTV."""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commercial_catalog import CommercialCatalog


class CommercialPool:
    """Manages a pool of commercial media files for random selection.
    
    Responsibilities:
    - Load and cache commercial files from a configured directory
    - Provide random selection without repeats within a single pick
    - Handle missing/empty directories gracefully (never throw)
    """
    
    def __init__(
        self,
        commercials_dir: Path | None,
        extensions: tuple[str, ...],
        debug: bool = False,
        catalog: "CommercialCatalog | None" = None,
    ):
        """Initialize the commercial pool.
        
        Args:
            commercials_dir: Directory containing commercial media files (or None to disable)
            extensions: Allowed file extensions (e.g., ('.mp4', '.mkv'))
            debug: Enable debug logging
            catalog: Optional CommercialCatalog for disk-based caching (recommended)
        """
        self.commercials_dir = commercials_dir
        self.extensions = tuple(ext.lower() for ext in extensions)
        self.debug = debug
        self.catalog = catalog
        self._files: tuple[Path, ...] | None = None
    
    def _load_files(self) -> tuple[Path, ...]:
        """Load commercial files from the configured directory.
        
        Uses catalog if available for fast disk-based caching, otherwise falls back
        to direct filesystem scan (legacy behavior).
        
        Returns:
            Tuple of commercial file paths (empty if directory missing/invalid)
        """
        if self._files is not None:
            return self._files
        
        if self.commercials_dir is None:
            if self.debug:
                print("[debug] commercials: no commercials_dir configured")
            self._files = ()
            return self._files
        
        # Use catalog if available (recommended path)
        if self.catalog is not None:
            try:
                self._files = self.catalog.get_or_scan(
                    commercials_dir=self.commercials_dir,
                    extensions=self.extensions,
                )
                
                if self.debug:
                    print(f"[debug] commercials: loaded {len(self._files)} file(s) from cache")
                
                return self._files
            
            except Exception as e:
                # Catalog failed; fall through to legacy scan
                if self.debug:
                    print(f"[debug] commercials: catalog failed, falling back to direct scan: {e}")
        
        # Legacy path: direct filesystem scan (no caching)
        try:
            if not self.commercials_dir.exists():
                if self.debug:
                    print(f"[debug] commercials: directory does not exist: {self.commercials_dir}")
                self._files = ()
                return self._files
            
            if not self.commercials_dir.is_dir():
                if self.debug:
                    print(f"[debug] commercials: path is not a directory: {self.commercials_dir}")
                self._files = ()
                return self._files
            
            # Scan for media files
            files: list[Path] = []
            for ext in self.extensions:
                # Use rglob to find files recursively
                files.extend(self.commercials_dir.rglob(f"*{ext}"))
            
            # Filter to only actual files (not directories) and sort for determinism
            files = [f for f in files if f.is_file()]
            files.sort(key=lambda p: str(p).lower())
            
            self._files = tuple(files)
            
            if self.debug:
                print(f"[debug] commercials: loaded {len(self._files)} file(s) from {self.commercials_dir}")
            
            return self._files
        
        except Exception as e:
            # Best-effort: never throw, just log and return empty
            if self.debug:
                print(f"[debug] commercials: failed to load files: {e}")
            self._files = ()
            return self._files
    
    def pick_random(self, count: int = 3, exclude: list[Path] | None = None) -> list[Path]:
        """Pick random commercials from the pool.
        
        Args:
            count: Number of commercials to pick (default: 3)
            exclude: Optional list of files to exclude from selection
        
        Returns:
            List of selected commercial paths (may be shorter than count if pool is small)
            Returns empty list if no commercials available
        
        Notes:
        - No repeats within a single pick
        - If pool has fewer files than requested, returns all available files (shuffled)
        - Never throws exceptions
        """
        try:
            files = self._load_files()
            
            if not files:
                if self.debug:
                    print("[debug] commercials: no files available for selection")
                return []
            
            # Build available pool (exclude any requested exclusions)
            exclude_set = set(exclude) if exclude else set()
            available = [f for f in files if f not in exclude_set]
            
            if not available:
                if self.debug:
                    print("[debug] commercials: no files remaining after exclusions")
                return []
            
            # Pick min(count, len(available)) unique files
            pick_count = min(int(count), len(available))
            selected = random.sample(available, pick_count)
            
            if self.debug:
                print(f"[debug] commercials: picked {len(selected)} file(s) from pool of {len(available)}")
            
            return selected
        
        except Exception as e:
            # Best-effort: never throw
            if self.debug:
                print(f"[debug] commercials: pick_random failed: {e}")
            return []
    
    def is_available(self) -> bool:
        """Check if commercial pool has any files available.
        
        Returns:
            True if at least one commercial file exists, False otherwise
        """
        files = self._load_files()
        return len(files) > 0
