from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .duration_cache import DurationCache


def norm_abs_path(p: str | Path) -> str:
    """Normalize an absolute path for stable IDs/keys.

    - Ensure absolute (via resolve when possible)
    - Lowercase
    - Convert backslashes to forward slashes (Windows-safe)
    """

    pp = Path(p)
    try:
        pp = pp.resolve()
    except Exception:
        # Best-effort; keep as-is.
        pass
    s = str(pp)
    return s.replace("\\", "/").lower()


def implicit_block_id_for_file(file_path: str | Path) -> str:
    """Stable implicit block id for a single media file."""

    return f"file:{norm_abs_path(file_path)}"


@dataclass(frozen=True)
class Block:
    """A scheduled unit consisting of 1+ files played back-to-back."""

    id: str
    files: tuple[Path, ...]
    durations_sec: tuple[float, ...]
    total_duration_sec: float

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Block.id must be non-empty")
        if not self.files:
            raise ValueError(f"Block {self.id!r} must contain at least one file")
        if len(self.files) != len(self.durations_sec):
            raise ValueError(f"Block {self.id!r} files/durations length mismatch")
        if self.total_duration_sec <= 0:
            raise ValueError(f"Block {self.id!r} total_duration_sec must be > 0")


@dataclass(frozen=True)
class BlockPlayback:
    """Derived playback for a given (block, started_at, now)."""

    block_id: str
    file_path: Path
    file_index: int
    file_offset_sec: float


def compute_block_playback(*, block: Block, started_at: datetime, now: datetime) -> BlockPlayback:
    """Pure function: map (block,start,now) -> (active file, offset).

    Contract:
    - elapsed is clamped to >= 0
    - If elapsed lands exactly on a boundary, we move to the next file (offset 0).
    """

    elapsed = max(0.0, (now - started_at).total_seconds())
    rem = float(elapsed)
    for i, dur in enumerate(block.durations_sec):
        d = max(0.0, float(dur))
        if rem < d:
            return BlockPlayback(block_id=block.id, file_path=block.files[i], file_index=i, file_offset_sec=rem)
        rem -= d

    # If we're exactly at/after the block end, clamp to the last frame of the last file.
    last_i = len(block.files) - 1
    last_dur = float(block.durations_sec[last_i]) if last_i >= 0 else 1.0
    clamp = max(0.0, last_dur - 0.25)
    return BlockPlayback(block_id=block.id, file_path=block.files[last_i], file_index=last_i, file_offset_sec=clamp)


def resolve_block_file(*, repo_root: Path, media_dirs: tuple[Path, ...], raw_path: str) -> Path:
    """Resolve a configured block file path.

    raw_path may be absolute or relative to any of the channel's media_dirs.
    Resolution is deterministic: the first media_dir in config order that contains
    the relative path wins.

    Raises:
        FileNotFoundError if the resolved file does not exist.
    """

    p = Path(str(raw_path))
    if p.is_absolute():
        resolved = p
        if not resolved.exists():
            raise FileNotFoundError(f"Block file does not exist: {resolved}")
        return resolved

    # Relative: try each media dir deterministically.
    for d in media_dirs:
        full_dir = (repo_root / d).resolve() if not d.is_absolute() else d
        cand = (full_dir / p)
        if cand.exists():
            return cand

    # No match.
    media_dirs_s = ", ".join(str(((repo_root / d).resolve() if not d.is_absolute() else d)) for d in media_dirs)
    raise FileNotFoundError(
        f"Block file {raw_path!r} not found in any media_dir for channel (searched: {media_dirs_s})"
    )


def build_channel_blocks(
    *,
    call_sign: str,
    repo_root: Path,
    media_dirs: tuple[Path, ...],
    scanned_files: tuple[Path, ...],
    explicit_blocks: tuple[tuple[str, tuple[str, ...]], ...],
    durations: DurationCache,
    default_duration_sec: float,
) -> tuple[dict[str, Block], tuple[str, ...]]:
    """Build all eligible blocks for a channel.

    Eligible set = explicit blocks + implicit single-file blocks for any scanned file
    not included in any explicit block.

    Args:
        explicit_blocks: tuple of (block_id, files_raw)
    Returns:
        (blocks_by_id, eligible_block_ids)
    """

    cs = str(call_sign).strip().upper()
    blocks_by_id: dict[str, Block] = {}

    # --- explicit blocks ---
    used_file_keys: set[str] = set()
    for bid, raw_files in explicit_blocks:
        block_id = str(bid).strip()
        if not block_id:
            raise ValueError(f"{cs}: block id must be non-empty")
        if block_id in blocks_by_id:
            raise ValueError(f"{cs}: duplicate block id: {block_id!r}")
        if not raw_files:
            raise ValueError(f"{cs}: block {block_id!r} must list at least one file")

        resolved_files: list[Path] = []
        resolved_durs: list[float] = []
        for rf in raw_files:
            resolved = resolve_block_file(repo_root=repo_root, media_dirs=media_dirs, raw_path=str(rf))
            key = norm_abs_path(resolved)
            if key in used_file_keys:
                raise ValueError(f"{cs}: file is listed in multiple blocks: {resolved}")
            used_file_keys.add(key)
            resolved_files.append(resolved)
            # Explicit blocks are small and important; ensure accurate durations.
            resolved_durs.append(
                float(durations.get_duration_sec(resolved, default_duration_sec=float(default_duration_sec)))
            )

        total = float(sum(resolved_durs))
        blocks_by_id[block_id] = Block(
            id=block_id,
            files=tuple(resolved_files),
            durations_sec=tuple(resolved_durs),
            total_duration_sec=total,
        )

    # --- implicit single-file blocks ---
    scanned_set = {norm_abs_path(p): p for p in scanned_files}
    for key, p in scanned_set.items():
        if key in used_file_keys:
            continue
        bid = implicit_block_id_for_file(p)
        # Implicit blocks: prefer cached duration; do not probe every file at startup.
        dur = float(durations.peek_duration_sec(p, default_duration_sec=float(default_duration_sec)))
        blocks_by_id[bid] = Block(id=bid, files=(p,), durations_sec=(dur,), total_duration_sec=dur)

    if not blocks_by_id:
        # Should not happen (Station adds a placeholder file), but guard anyway.
        raise ValueError(f"{cs}: no eligible blocks")

    eligible_ids = tuple(sorted(blocks_by_id.keys(), key=lambda x: str(x).lower()))
    return blocks_by_id, eligible_ids


def display_block_id(block_id: str) -> str:
    """Human-friendly string for logs."""

    if block_id.startswith("file:"):
        try:
            return Path(block_id[5:]).name
        except Exception:
            return block_id
    return block_id


def load_episode_metadata(episode_path: Path) -> dict | None:
    """Load commercial break metadata for an episode from its sidecar JSON file.
    
    Looks for a .json file with the same basename as the episode file.
    Expected format:
    {
        "version": 1,
        "breaks": [
            {"start": <seconds>, "end": <seconds>},
            ...
        ]
    }
    
    Args:
        episode_path: Path to the episode media file
    
    Returns:
        Parsed metadata dict if file exists and is valid, None otherwise
    
    Notes:
    - Never throws exceptions (returns None on any error)
    - Missing metadata is NOT an error
    - Malformed metadata is silently ignored
    """
    try:
        # Look for sidecar JSON file
        json_path = episode_path.with_suffix(".json")
        
        if not json_path.exists():
            return None
        
        if not json_path.is_file():
            return None
        
        # Read and parse JSON
        content = json_path.read_text(encoding="utf-8")
        metadata = json.loads(content)
        
        # Basic validation: must be a dict with "breaks" key
        if not isinstance(metadata, dict):
            return None
        
        if "breaks" not in metadata:
            return None
        
        breaks = metadata.get("breaks")
        if not isinstance(breaks, list):
            return None
        
        # Validate each break window
        for brk in breaks:
            if not isinstance(brk, dict):
                return None
            if "start" not in brk or "end" not in brk:
                return None
            # Ensure start/end are numeric
            try:
                float(brk["start"])
                float(brk["end"])
            except (TypeError, ValueError):
                return None
        
        # Metadata looks valid
        return metadata
    
    except Exception:
        # Best-effort: any error means "no metadata"
        return None
