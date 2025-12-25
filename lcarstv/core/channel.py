from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings
from .duration_cache import DurationCache
from .models import ChannelState
from .selector import SmartRandomSelector
from .state_store import StateStore


@dataclass
class ChannelRuntime:
    call_sign: str
    files: tuple[Path, ...]
    settings: Settings
    cooldown: int
    selector: SmartRandomSelector
    store: StateStore
    state: ChannelState
    durations: DurationCache

    def _persist_live_state(self) -> None:
        st = self.selector.state
        ch = st.channels.get(self.call_sign)
        if ch is not None:
            ch.current_file = self.state.current_file
            ch.started_at = self.state.started_at
            self.store.save(st)

    def sync_to_now(self, now: datetime, *, reason: str = "SYNC", debug: bool = False) -> int:
        """Advance (rollover) until the current airing content contains `now`.

        Deterministic invariant:
        - started_at only ever moves forward by *durations of aired files*.
        - current_file only advances when (now - started_at) >= duration(current_file).

        Returns:
            Number of rollovers applied.
        """

        if self.settings.default_duration_sec <= 0:
            raise ValueError("default_duration_sec must be > 0")

        rollovers = 0
        while True:
            current_file = self.state.current_file
            dur = self.durations.get_duration_sec(
                current_file, default_duration_sec=float(self.settings.default_duration_sec)
            )
            elapsed = (now - self.state.started_at).total_seconds()

            if debug:
                print(
                    f"[debug] {self.call_sign} sync: now={now.isoformat()} started_at={self.state.started_at.isoformat()} elapsed={elapsed:.2f}s dur={dur:.2f}s file={Path(current_file).name}"
                )

            if elapsed < dur:
                return rollovers

            old_file = self.state.current_file
            old_started = self.state.started_at

            # Advance time by the just-finished file duration.
            self.state.started_at = self.state.started_at + timedelta(seconds=float(dur))

            # Advance to next file.
            current_path = Path(old_file)
            next_path = self.selector.pick_next(
                call_sign=self.call_sign,
                files=self.files,
                cooldown=self.cooldown,
                current_file=current_path,
            )
            self.state.current_file = str(next_path)

            self._persist_live_state()
            rollovers += 1

            if debug:
                print(
                    f"[debug] advance reason={reason} call_sign={self.call_sign} {Path(old_file).name} -> {Path(self.state.current_file).name} {old_started.isoformat()} -> {self.state.started_at.isoformat()}"
                )
