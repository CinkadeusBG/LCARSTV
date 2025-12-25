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
            ipc_trace=settings.ipc_trace,
            static_burst_path=str(settings.static_burst_path) if settings.static_burst_path else None,
            static_burst_duration_sec=0.4,
        )

    # Throttle auto-advance polling (keep low CPU / low IPC spam).
    last_auto_poll = 0.0
    last_auto_advanced_from: str | None = None

    def _norm_path(p: str | None) -> str | None:
        if not p:
            return None
        # mpv may return different slash styles / case on Windows.
        return str(p).replace("\\", "/").lower()

    # Suppress double-advances right after we load the next file.
    suppress_until_time: float = 0.0
    awaiting_mpv_path: str | None = None

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

            # Auto-advance: EOF or virtual schedule rollover.
            if player is not None:
                now = now_utc()
                t = time.time()
                if t - last_auto_poll >= 0.2:
                    last_auto_poll = t

                    # If we just advanced, suppress additional triggers until mpv
                    # reports the newly requested path, or until a short cooldown.
                    if awaiting_mpv_path is not None:
                        mpv_path = _norm_path(player.current_mpv_path())
                        if mpv_path is not None and mpv_path == awaiting_mpv_path:
                            awaiting_mpv_path = None
                        elif t < suppress_until_time:
                            # Still waiting; do not evaluate triggers.
                            continue
                        else:
                            # Cooldown elapsed; stop suppressing even if mpv path
                            # hasn't updated (best-effort safety net).
                            awaiting_mpv_path = None

                    # Debounce by the last played media path we requested.
                    current_media = player.current_media_path

                    # Only evaluate triggers for the currently active channel.
                    active_chan = station.channels.get(station.active_call_sign)
                    active_expected = _norm_path(active_chan.state.current_file) if active_chan is not None else None
                    current_media_norm = _norm_path(current_media)
                    if active_expected is None or current_media_norm is None or active_expected != current_media_norm:
                        # mpv is still transitioning or showing static / previous channel; ignore triggers.
                        continue

                    # If we are within the playback guard window (static + post-seek grace),
                    # do not run *any* trigger evaluation.
                    if player.playback_guard_active():
                        continue

                    # EOF/IDLE/NEAR_END detection from mpv.
                    mpv_trigger = player.poll_end_of_episode(end_epsilon_sec=settings.end_epsilon_sec)

                    # Schedule rollover (authoritative / deterministic) based on cached per-file duration.
                    schedule_trigger = False
                    dur = player.current_duration_sec()  # still used for debug logging only
                    if active_chan is not None:
                        expected_dur = active_chan.durations.get_duration_sec(
                            active_chan.state.current_file,
                            default_duration_sec=float(settings.default_duration_sec),
                        )
                        schedule_trigger = active_chan.state.position_sec(now) >= float(expected_dur)

                    trigger_kind: str | None = None

                    # Prefer mpv-based triggers (EOF/IDLE/NEAR_END); fall back to schedule.
                    mpv_reason: str | None = None
                    mpv_time_pos: float | None = None
                    mpv_dur: float | None = None
                    if mpv_trigger is not None:
                        mpv_reason, mpv_time_pos, mpv_dur = mpv_trigger
                        # NEAR_END is informational; actual advancement must be driven by schedule
                        # or definitive mpv transitions (EOF/IDLE).
                        if mpv_reason in ("EOF", "IDLE"):
                            trigger_kind = mpv_reason
                    elif schedule_trigger:
                        trigger_kind = "SCHEDULE"

                    if trigger_kind is not None and current_media is not None:
                        # Ensure it advances only once for the current media.
                        if last_auto_advanced_from != current_media:
                            old_file = current_media
                            # Compute best-effort timing details for logging.
                            tp_s = "?"
                            dur_s = "?"
                            if trigger_kind == "SCHEDULE":
                                chan = station.channels.get(station.active_call_sign)
                                pos = chan.state.position_sec(now) if chan is not None else None
                                tp_s = f"{pos:.2f}" if isinstance(pos, (int, float)) else "?"
                                dur_s = f"{dur:.2f}" if isinstance(dur, (int, float)) else "?"
                            else:
                                tp_s = f"{mpv_time_pos:.2f}" if isinstance(mpv_time_pos, (int, float)) else "?"
                                dur_s = f"{mpv_dur:.2f}" if isinstance(mpv_dur, (int, float)) else "?"

                            # Advance only the currently active channel.
                            # IMPORTANT: never sets started_at=now.
                            if trigger_kind == "SCHEDULE":
                                info = station.advance_active(now, reason="SCHEDULE")
                            else:
                                # EOF/IDLE: force at least one rollover.
                                info = station.force_advance_active(now, reason=f"MPV_{trigger_kind}")

                            if settings.debug:
                                print(
                                    f"[debug] auto-advance trigger={trigger_kind} time-pos={tp_s}s dur={dur_s}s {Path(old_file).name} -> {Path(info.current_file).name}"
                                )

                            last_auto_advanced_from = old_file
                            player.play(info.current_file, info.position_sec, call_sign=info.call_sign)

                            # Suppress re-triggers while mpv transitions.
                            suppress_until_time = time.time() + 0.5
                            awaiting_mpv_path = _norm_path(info.current_file)

            # low CPU polling loop; no threads.
            time.sleep(0.05)
    finally:
        if player is not None:
            player.close()
