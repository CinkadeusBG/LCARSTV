"""Player adapters.

LCARSTV supports multiple playback backends.

- mpv (default): JSON IPC controlled process.
- vlc: per-play `cvlc`/`vlc` process (preferred for composite output on Pi).
"""

from __future__ import annotations

import os
from typing import Protocol

from lcarstv.core.config import Settings

from .mpv_player import MpvPlayer
from .vlc_player import VlcPlayer


class Player(Protocol):
    # NOTE: This is the minimal surface area used by lcarstv/app.py.
    debug: bool

    @property
    def current_media_path(self) -> str | None: ...

    def start(self) -> None: ...

    def play(self, file_path: str, start_sec: float, *, call_sign: str | None = None) -> None: ...

    def play_with_static_burst(self, file_path: str, start_sec: float, *, call_sign: str | None = None) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...

    def playback_guard_active(self) -> bool: ...

    def poll_end_of_episode(
        self, *, end_epsilon_sec: float = 0.25
    ) -> tuple[str, float | None, float | None] | None: ...

    def current_duration_sec(self) -> float | None: ...

    def current_mpv_path(self) -> str | None: ...


def create_player(settings: Settings) -> Player:
    backend = str(settings.player_backend or "mpv").strip().lower()
    if backend == "vlc":
        # Allow VLC on Windows *if installed*.
        cand = VlcPlayer(
            debug=settings.debug,
            static_burst_path=str(settings.static_burst_path) if settings.static_burst_path else None,
            static_burst_duration_sec=0.5,
        )
        if not cand.has_vlc():
            if settings.debug:
                print("[player] warn: vlc backend requested but vlc/cvlc not found; falling back to mpv")
        else:
            return cand

    # Default.
    return MpvPlayer(
        debug=settings.debug,
        ipc_trace=settings.ipc_trace,
        static_burst_path=str(settings.static_burst_path) if settings.static_burst_path else None,
        static_burst_duration_sec=0.4,
    )


__all__ = ["MpvPlayer", "VlcPlayer", "Player", "create_player"]
