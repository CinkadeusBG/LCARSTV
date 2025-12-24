from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class DeterministicSelector:
    """Phase-1 placeholder.

    Smart-random (shuffle-bag + cooldown + persistence) is a later phase.
    For now, we just advance sequentially in the scanned list.
    """

    def first(self, files: tuple[Path, ...]) -> Path:
        return files[0]

    def next_after(self, files: tuple[Path, ...], current: Path) -> Path:
        try:
            idx = files.index(current)
        except ValueError:
            return files[0]
        return files[(idx + 1) % len(files)]

