from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InputEvent:
    kind: str  # "channel_up" | "channel_down" | "quit" | "reset_all"
