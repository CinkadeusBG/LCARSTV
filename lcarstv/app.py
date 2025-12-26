from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from lcarstv.core.clock import now_utc
from lcarstv.core.config import load_channels, load_settings_profile
from lcarstv.core.station import Station
from lcarstv.input.keyboard import KeyboardInput
from lcarstv.player import MpvPlayer


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lcarstv")
    default_profile = "windows" if os.name == "nt" else "pi"
    parser.add_argument(
        "--profile",
        choices=("windows", "pi"),
        default=default_profile,
        help=f"Select runtime/config profile (default: {default_profile})",
    )
    parser.add_argument(
        "--channels",
        type=str,
        default=None,
        help="Override channels config path (takes precedence over profile).",
    )
    parser.add_argument(
        "--settings",
        type=str,
        default=None,
        help="Override settings config path (takes precedence over profile).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without mpv; log current airing file and computed position.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    repo_root = Path(__file__).resolve().parents[1]

    settings_path = Path(args.settings).expanduser() if args.settings else None
    channels_path = Path(args.channels).expanduser() if args.channels else None

    settings = load_settings_profile(repo_root=repo_root, profile=args.profile, path_override=settings_path)
    channels_cfg = load_channels(repo_root=repo_root, profile=args.profile, path_override=channels_path)

    station = Station.from_configs(
        channels_cfg=channels_cfg,
        settings=settings,
        repo_root=repo_root,
        now=now_utc(),
    )

    inp = KeyboardInput()
    print("LCARSTV dry-run" if args.dry_run else "LCARSTV playback")
    print("Controls: PageUp/Up=Channel Up, PageDown/Down=Channel Down, Q=Quit")
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
                    if active_chan is None:
                        continue
                    expected_pb = active_chan.scheduled_playback(now)
                    expected_file = str(expected_pb.file_path)
                    expected_pos = float(expected_pb.file_offset_sec)

                    current_media_norm = _norm_path(current_media)
                    expected_norm = _norm_path(expected_file)
                    if current_media_norm is None or expected_norm is None:
                        continue

                    # Ignore triggers while mpv is on static/previous channel.
                    # We consider playback "in-channel" if the currently loaded file is
                    # any file in the *current block*.
                    block = active_chan.get_current_block()
                    block_files_norm = {_norm_path(str(p)) for p in block.files}
                    in_block = current_media_norm in block_files_norm
                    if not in_block:
                        continue

                    # If we are within the playback guard window (static + post-seek grace),
                    # do not run *any* trigger evaluation.
                    if player.playback_guard_active():
                        continue

                    # EOF/IDLE/NEAR_END detection from mpv.
                    mpv_trigger = player.poll_end_of_episode(end_epsilon_sec=settings.end_epsilon_sec)

                    # Schedule rollover (authoritative / deterministic) based on block duration.
                    schedule_trigger = False
                    dur = player.current_duration_sec()  # still used for debug logging only
                    elapsed = active_chan.state.elapsed_sec(now)
                    schedule_trigger = elapsed >= float(block.total_duration_sec)

                    # Within-block schedule file switch.
                    scheduled_file_mismatch = current_media_norm != expected_norm

                    # Prefer schedule as authoritative. mpv EOF/IDLE can happen early due to scrub.
                    mpv_reason: str | None = None
                    mpv_time_pos: float | None = None
                    mpv_dur: float | None = None
                    if mpv_trigger is not None:
                        mpv_reason, mpv_time_pos, mpv_dur = mpv_trigger

                    # Case A) Schedule says it ended -> do a real advance (persisted).
                    if schedule_trigger and current_media is not None:
                        # Ensure it advances only once for the current media.
                        if last_auto_advanced_from != current_media:
                            old_file = current_media

                            # Use mpv reason for labeling when available, else SCHEDULE.
                            advance_reason = mpv_reason if mpv_reason in ("EOF", "IDLE") else "SCHEDULE"
                            info = station.advance_active(now, reason=advance_reason)

                            if settings.debug:
                                tp_s = "?"
                                dur_s = "?"
                                chan = station.channels.get(station.active_call_sign)
                                pb = chan.scheduled_playback(now) if chan is not None else None
                                pos = pb.file_offset_sec if pb is not None else None
                                tp_s = f"{pos:.2f}" if isinstance(pos, (int, float)) else "?"
                                dur_s = f"{dur:.2f}" if isinstance(dur, (int, float)) else "?"
                                print(
                                    f"[debug] auto-advance reason={advance_reason} time-pos={tp_s}s dur={dur_s}s {Path(old_file).name} -> {Path(info.current_file).name}"
                                )

                            last_auto_advanced_from = old_file
                            player.play(info.current_file, info.position_sec, call_sign=info.call_sign)

                            # Suppress re-triggers while mpv transitions.
                            suppress_until_time = time.time() + 0.5
                            awaiting_mpv_path = _norm_path(info.current_file)

                    # Case B) Schedule says we should be on a different file within the current block.
                    # Switch files without advancing the block.
                    elif scheduled_file_mismatch:
                        if settings.debug:
                            print(
                                f"[debug] within-block switch reason=SCHEDULE call_sign={station.active_call_sign} block_id={active_chan.state.current_block_id} {Path(current_media).name} -> {Path(expected_file).name} offset={expected_pos:.2f}"
                            )

                        player.play(expected_file, expected_pos, call_sign=station.active_call_sign)

                        # Suppress retriggers while mpv transitions.
                        suppress_until_time = time.time() + 0.5
                        awaiting_mpv_path = _norm_path(expected_file)

                    # Case B) mpv says EOF/IDLE but schedule says it hasn't ended -> corrective re-load/seek.
                    elif mpv_reason in ("EOF", "IDLE"):
                        # IMPORTANT: do NOT advance or persist schedule state here.
                        # expected_file/pos computed above

                        if settings.debug:
                            print(
                                f"[debug] correct reason={mpv_reason} call_sign={station.active_call_sign} block_id={active_chan.state.current_block_id} current_file={expected_file} position_sec={expected_pos:.2f}"
                            )

                        player.play(expected_file, expected_pos, call_sign=station.active_call_sign)

                        # Suppress retriggers while mpv transitions back.
                        suppress_until_time = time.time() + 0.5
                        awaiting_mpv_path = _norm_path(expected_file)

            # low CPU polling loop; no threads.
            time.sleep(0.05)
    finally:
        inp.close()
        if player is not None:
            player.close()
