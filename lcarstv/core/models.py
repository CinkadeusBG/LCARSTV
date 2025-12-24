from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ChannelState:
    call_sign: str
    current_file: str
    started_at: datetime

    def position_sec(self, now: datetime) -> float:
        return max(0.0, (now - self.started_at).total_seconds())


@dataclass
class TuneInfo:
    call_sign: str
    current_file: str
    started_at: datetime
    position_sec: float

