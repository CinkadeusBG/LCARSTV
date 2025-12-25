from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import random

from .config import ChannelsConfig, Settings
from .channel import ChannelRuntime
from .duration_cache import DurationCache
from .models import ChannelState, TuneInfo
from .scanner import scan_media_dirs
from .selector import SmartRandomSelector
from .state_store import PersistedChannel, StateStore


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
        store = StateStore(path=repo_root / "data" / "state.json", debug=settings.debug)
        durations = DurationCache(path=repo_root / "data" / "durations.json", debug=settings.debug)
        persisted = store.load()
        selector = SmartRandomSelector(store=store, state=persisted, debug=settings.debug)
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

            cooldown = int(ch.cooldown) if ch.cooldown is not None else int(settings.default_cooldown)

            # Ensure scheduler has a bag for this channel/library.
            selector.ensure_initialized(ch.call_sign, files)

            # Restore persisted live state if valid.
            persisted_ch = selector.state.channels.get(ch.call_sign) or PersistedChannel()
            eligible = {str(p) for p in files}
            restored_file = persisted_ch.current_file if persisted_ch.current_file in eligible else None
            restored_started = persisted_ch.started_at

            if restored_file and restored_started:
                current_file = restored_file
                started_at = restored_started
                if settings.debug:
                    print(f"[debug] {ch.call_sign} restore: {Path(current_file).name} started_at={started_at.isoformat()}")
            else:
                # Invalid/missing persisted state: choose a new file and start it in-progress.
                current_path = selector.pick_next(
                    call_sign=ch.call_sign,
                    files=files,
                    cooldown=cooldown,
                    current_file=None,
                )
                current_file = str(current_path)

                dur = max(1.0, float(settings.default_duration_sec))
                offset = random.random() * dur
                started_at = now - timedelta(seconds=offset)

                if settings.debug:
                    print(
                        f"[debug] {ch.call_sign} init: {Path(current_file).name} started_at={started_at.isoformat()} offset={offset:.2f}s"
                    )

                # Persist initial live state.
                pch = selector.state.channels.get(ch.call_sign)
                if pch is not None:
                    pch.current_file = current_file
                    pch.started_at = started_at
                    store.save(selector.state)

            state = ChannelState(call_sign=ch.call_sign, current_file=current_file, started_at=started_at)
            channels[ch.call_sign] = ChannelRuntime(
                call_sign=ch.call_sign,
                files=files,
                settings=settings,
                cooldown=cooldown,
                selector=selector,
                store=store,
                state=state,
                durations=durations,
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
        chan.sync_to_now(now, reason="TUNE_SYNC", debug=self.settings.debug)
        pos = chan.state.position_sec(now)

        if self.settings.debug:
            print(
                f"[debug] tune call_sign={call_sign} file={Path(chan.state.current_file).name} started_at={chan.state.started_at.isoformat()} pos={pos:.2f}s"
            )
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

    def advance_active(self, now: datetime, *, reason: str = "AUTO_ADVANCE") -> TuneInfo:
        """Immediately advance the active channel to the next file.

        - Deterministic: advances by real rollovers, never sets started_at=now.
        - Advances multiple items if the channel is behind (catch-up).
        - Persists current_file/started_at and scheduler state.
        """

        call_sign = self.active_call_sign
        if call_sign not in self.channels:
            raise KeyError(call_sign)

        chan = self.channels[call_sign]
        chan.sync_to_now(now, reason=reason, debug=self.settings.debug)
        pos = chan.state.position_sec(now)
        return TuneInfo(
            call_sign=call_sign,
            current_file=chan.state.current_file,
            started_at=chan.state.started_at,
            position_sec=pos,
        )

    def force_advance_active(self, now: datetime, *, reason: str) -> TuneInfo:
        """Force at least one rollover for the active channel.

        Used for mpv EOF/IDLE/NEAR_END triggers where the player says the file has ended.
        Deterministic rule: started_at advances by duration(current_file) (cached ffprobe).

        Notes:
        - If the channel is already behind, this may advance multiple items.
        - If the channel is not behind (e.g. NEAR_END slightly early), this will
          still advance one item.
        """

        call_sign = self.active_call_sign
        if call_sign not in self.channels:
            raise KeyError(call_sign)

        chan = self.channels[call_sign]
        dur = chan.durations.get_duration_sec(
            chan.state.current_file, default_duration_sec=float(self.settings.default_duration_sec)
        )
        forced_now = max(now, chan.state.started_at + timedelta(seconds=float(dur) + 0.001))
        chan.sync_to_now(forced_now, reason=reason, debug=self.settings.debug)

        pos = chan.state.position_sec(now)
        return TuneInfo(
            call_sign=call_sign,
            current_file=chan.state.current_file,
            started_at=chan.state.started_at,
            position_sec=pos,
        )
