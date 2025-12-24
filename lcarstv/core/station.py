from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import ChannelsConfig, Settings
from .channel import ChannelRuntime
from .models import ChannelState, TuneInfo
from .scanner import scan_media_dirs
from .selector import DeterministicSelector


@dataclass
class Station:
    call_signs: tuple[str, ...]
    channels: dict[str, ChannelRuntime]
    active_call_sign: str
    settings: Settings

    @staticmethod
    def from_configs(
        *,
        channels_cfg: ChannelsConfig,
        settings: Settings,
        repo_root: Path,
        now: datetime,
    ) -> "Station":
        selector = DeterministicSelector()
        channels: dict[str, ChannelRuntime] = {}
        for ch in channels_cfg.channels:
            scan = scan_media_dirs(repo_root, ch.media_dirs, settings.extensions)
            if not scan.files:
                # Keep deterministic, but allow boot without media present.
                # A real deployment will have media files.
                # We'll synthesize a placeholder filename so logic still runs.
                placeholder = repo_root / "media" / ch.call_sign / "_NO_MEDIA_FOUND_.mp4"
                files = (placeholder,)
            else:
                files = scan.files

            first = selector.first(files)
            state = ChannelState(call_sign=ch.call_sign, current_file=str(first), started_at=now)
            channels[ch.call_sign] = ChannelRuntime(
                call_sign=ch.call_sign,
                files=files,
                settings=settings,
                selector=selector,
                state=state,
            )

        call_signs = channels_cfg.ordered_call_signs()
        active = call_signs[0]
        return Station(call_signs=call_signs, channels=channels, active_call_sign=active, settings=settings)

    def _idx(self) -> int:
        return self.call_signs.index(self.active_call_sign)

    def channel_up(self, now: datetime) -> TuneInfo:
        i = self._idx()
        self.active_call_sign = self.call_signs[(i + 1) % len(self.call_signs)]
        return self.tune_to(self.active_call_sign, now)

    def channel_down(self, now: datetime) -> TuneInfo:
        i = self._idx()
        self.active_call_sign = self.call_signs[(i - 1) % len(self.call_signs)]
        return self.tune_to(self.active_call_sign, now)

    def tune_to(self, call_sign: str, now: datetime) -> TuneInfo:
        if call_sign not in self.channels:
            raise KeyError(call_sign)

        # Channel change UX placeholders (console only for now)
        print("--- STATIC ---")
        print(f"TUNED: {call_sign}")

        chan = self.channels[call_sign]
        chan.sync_to_now(now, debug=self.settings.debug)
        pos = chan.state.position_sec(now)
        print(f"Airing: {Path(chan.state.current_file).name}")
        print(f"Started at: {chan.state.started_at.isoformat()}")
        print(f"Position: {pos:.2f}s")
        print()
        return TuneInfo(
            call_sign=call_sign,
            current_file=chan.state.current_file,
            started_at=chan.state.started_at,
            position_sec=pos,
        )

