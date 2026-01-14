"""Data structures for commercial break detection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    """A time segment with start and end timestamps in seconds."""

    start: float
    end: float

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"Segment end ({self.end}) must be >= start ({self.start})")


@dataclass(frozen=True)
class BreakWindow:
    """A commercial break window with start and end timestamps in seconds."""

    start: float
    end: float

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"BreakWindow end ({self.end}) must be > start ({self.start})")

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary format for JSON serialization."""
        return {"start": round(self.start, 3), "end": round(self.end, 3)}
