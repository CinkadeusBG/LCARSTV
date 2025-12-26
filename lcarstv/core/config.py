from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def resolve_profile_config_path(*, repo_root: Path, base_name: str, profile: str | None) -> tuple[Path, str]:
    """Return the config path to use for a given base file name and profile.

    Resolution order:
    1) config/{base_name}.{profile}.json if profile is provided and file exists
    2) config/{base_name}.json

    Returns (path, reason) where reason is "profile" or "fallback".
    """

    config_dir = repo_root / "config"
    if profile:
        prof = str(profile).strip().lower()
        prof_path = config_dir / f"{base_name}.{prof}.json"
        if prof_path.exists():
            return prof_path, "profile"
    return config_dir / f"{base_name}.json", "fallback"


def load_channels(*, repo_root: Path, profile: str | None = None, path_override: Path | None = None) -> ChannelsConfig:
    """Load channels config honoring per-profile files and optional overrides."""

    if path_override is not None:
        print(f"[config] profile={profile or '-'} channels={path_override} (override)")
        return load_channels_config(path_override)
    path, reason = resolve_profile_config_path(repo_root=repo_root, base_name="channels", profile=profile)
    print(f"[config] profile={profile or '-'} channels={path} ({reason})")
    return load_channels_config(path)


def load_settings_profile(*, repo_root: Path, profile: str | None = None, path_override: Path | None = None) -> Settings:
    """Load settings config honoring per-profile files and optional overrides."""

    if path_override is not None:
        print(f"[config] profile={profile or '-'} settings={path_override} (override)")
        return load_settings(path_override)
    path, reason = resolve_profile_config_path(repo_root=repo_root, base_name="settings", profile=profile)
    print(f"[config] profile={profile or '-'} settings={path} ({reason})")
    return load_settings(path)


@dataclass(frozen=True)
class BlockConfig:
    id: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class ChannelConfig:
    call_sign: str
    media_dirs: tuple[Path, ...]
    cooldown: int | None
    blocks: tuple[BlockConfig, ...] = ()


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
    default_cooldown: int
    debug: bool
    ipc_trace: bool
    static_burst_path: Path | None
    end_epsilon_sec: float


def load_channels_config(path: Path) -> ChannelsConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    chans: list[ChannelConfig] = []
    for ch in data.get("channels", []):
        call_sign = str(ch["call_sign"]).strip().upper()
        media_dirs = tuple(Path(p) for p in ch.get("media_dirs", []))
        cooldown_raw = ch.get("cooldown")
        cooldown = int(cooldown_raw) if cooldown_raw is not None else None

        blocks_cfg: list[BlockConfig] = []
        for b in ch.get("blocks", []) or []:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("id", "")).strip()
            files_raw = b.get("files", []) or []
            files = tuple(str(x) for x in files_raw)
            if not bid:
                raise ValueError(f"{call_sign}: block requires non-empty id")
            if not files:
                raise ValueError(f"{call_sign}: block {bid!r} requires non-empty files")
            blocks_cfg.append(BlockConfig(id=bid, files=files))

        chans.append(
            ChannelConfig(call_sign=call_sign, media_dirs=media_dirs, cooldown=cooldown, blocks=tuple(blocks_cfg))
        )
    if not chans:
        raise ValueError("channels.json has no channels")
    return ChannelsConfig(channels=tuple(chans))


def load_settings(path: Path) -> Settings:
    data = json.loads(path.read_text(encoding="utf-8"))
    extensions = tuple(str(x).lower() for x in data.get("extensions", []))
    if not extensions:
        raise ValueError("settings.json requires non-empty extensions")
    default_duration_sec = float(data.get("default_duration_sec", 1800))
    default_cooldown = int(data.get("default_cooldown", 10))
    debug = bool(data.get("debug", False))
    ipc_trace = bool(data.get("ipc_trace", False))
    static_burst_raw = data.get("static_burst_path")
    static_burst_path = Path(static_burst_raw) if static_burst_raw else None
    end_epsilon_sec = float(data.get("end_epsilon_sec", 0.25))
    return Settings(
        extensions=extensions,
        default_duration_sec=default_duration_sec,
        default_cooldown=default_cooldown,
        debug=debug,
        ipc_trace=ipc_trace,
        static_burst_path=static_burst_path,
        end_epsilon_sec=end_epsilon_sec,
    )
