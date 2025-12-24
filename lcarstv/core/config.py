from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChannelConfig:
    call_sign: str
    media_dirs: tuple[Path, ...]


@dataclass(frozen=True)
class ChannelsConfig:
    channels: tuple[ChannelConfig, ...]

    def ordered_call_signs(self) -> tuple[str, ...]:
        return tuple(ch.call_sign for ch in self.channels)

    def by_call_sign(self) -> dict[str, ChannelConfig]:
        return {c.call_sign: c for c in self.channels}


@dataclass(frozen=True)
class Settings:
    extensions: tuple[str, ...]
    default_duration_sec: float
    debug: bool


def load_channels_config(path: Path) -> ChannelsConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    chans: list[ChannelConfig] = []
    for ch in data.get("channels", []):
        call_sign = str(ch["call_sign"]).strip().upper()
        media_dirs = tuple(Path(p) for p in ch.get("media_dirs", []))
        chans.append(ChannelConfig(call_sign=call_sign, media_dirs=media_dirs))
    if not chans:
        raise ValueError("channels.json has no channels")
    return ChannelsConfig(channels=tuple(chans))


def load_settings(path: Path) -> Settings:
    data = json.loads(path.read_text(encoding="utf-8"))
    extensions = tuple(str(x).lower() for x in data.get("extensions", []))
    if not extensions:
        raise ValueError("settings.json requires non-empty extensions")
    default_duration_sec = float(data.get("default_duration_sec", 1800))
    debug = bool(data.get("debug", False))
    return Settings(
        extensions=extensions,
        default_duration_sec=default_duration_sec,
        debug=debug,
    )

