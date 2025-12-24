from __future__ import annotations

import argparse
import time
from pathlib import Path

from lcarstv.core.clock import now_utc
from lcarstv.core.config import load_channels_config, load_settings
from lcarstv.core.station import Station
from lcarstv.input.keyboard import KeyboardInput
from lcarstv.player import MpvPlayer


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lcarstv")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without mpv; log current airing file and computed position.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    settings = load_settings(repo_root / "config" / "settings.json")
    channels_cfg = load_channels_config(repo_root / "config" / "channels.json")

    station = Station.from_configs(
        channels_cfg=channels_cfg,
        settings=settings,
        repo_root=repo_root,
        now=now_utc(),
    )

    inp = KeyboardInput()
    print("LCARSTV dry-run" if args.dry_run else "LCARSTV playback")
    print("Controls: PageUp=Channel Up, PageDown=Channel Down, Q=Quit")
    print()

    player: MpvPlayer | None = None
    if not args.dry_run:
        player = MpvPlayer(
            debug=settings.debug,
            static_burst_path=str(settings.static_burst_path) if settings.static_burst_path else None,
            static_burst_duration_sec=0.4,
        )

    try:
        # Initial tune
        info = station.tune_to(station.active_call_sign, now_utc())
        if player is not None:
            player.play_with_static_burst(info.current_file, info.position_sec, call_sign=info.call_sign)

        while True:
            evt = inp.poll()
            if evt is not None:
                if evt.kind == "quit":
                    print("Exiting.")
                    return 0
                if evt.kind == "channel_up":
                    info = station.channel_up(now_utc())
                    if player is not None:
                        player.play_with_static_burst(info.current_file, info.position_sec, call_sign=info.call_sign)
                if evt.kind == "channel_down":
                    info = station.channel_down(now_utc())
                    if player is not None:
                        player.play_with_static_burst(info.current_file, info.position_sec, call_sign=info.call_sign)

            # low CPU polling loop; no threads.
            time.sleep(0.05)
    finally:
        if player is not None:
            player.close()
