from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings
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

    def sync_to_now(self, now: datetime, *, debug: bool = False) -> None:
        """Advance current_file/started_at until the current airing content contains `now`.

        Uses default_duration_sec for this initial dry-run slice.
        """

        duration = float(self.settings.default_duration_sec)
        if duration <= 0:
            raise ValueError("default_duration_sec must be > 0")

        # Catch up through as many episodes as would have ended.
        while True:
            elapsed = (now - self.state.started_at).total_seconds()
            if debug:
                print(
                    f"[debug] {self.call_sign} sync: now={now.isoformat()} started_at={self.state.started_at.isoformat()} elapsed={elapsed:.2f}s dur={duration:.2f}s"
                )
            if elapsed < duration:
                return

            self.state.started_at = self.state.started_at + timedelta(seconds=duration)

            current_path = Path(self.state.current_file)
            next_path = self.selector.pick_next(
                call_sign=self.call_sign,
                files=self.files,
                cooldown=self.cooldown,
                current_file=current_path,
            )
            if debug:
                print(
                    f"[debug] {self.call_sign} advance: {current_path.name} -> {next_path.name} new_started_at={self.state.started_at.isoformat()}"
                )
            self.state.current_file = str(next_path)

            # Persist live state after each rollover.
            st = self.selector.state
            ch = st.channels.get(self.call_sign)
            if ch is not None:
                ch.current_file = self.state.current_file
                ch.started_at = self.state.started_at
                self.store.save(st)
