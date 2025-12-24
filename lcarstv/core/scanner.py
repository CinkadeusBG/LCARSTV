from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScanResult:
    files: tuple[Path, ...]


def scan_media_dirs(repo_root: Path, media_dirs: tuple[Path, ...], extensions: tuple[str, ...]) -> ScanResult:
    """Scan media dirs recursively and return a deterministic, sorted list.

    This is ingestion only; we never present browsing UI.
    """

    allowed = {e.lower() for e in extensions}
    out: list[Path] = []
    for d in media_dirs:
        full = (repo_root / d).resolve() if not d.is_absolute() else d
        if not full.exists():
            continue
        if full.is_file():
            # If the user mistakenly points at a file, accept it.
            if full.suffix.lower() in allowed:
                out.append(full)
            continue
        for p in full.rglob("*"):
            if p.is_file() and p.suffix.lower() in allowed:
                out.append(p)

    # deterministic ordering; avoid OS-dependent glob ordering
    out_sorted = sorted({p for p in out}, key=lambda x: str(x).lower())
    return ScanResult(files=tuple(out_sorted))

