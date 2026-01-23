from __future__ import annotations

import argparse
import os
import time
import queue
from pathlib import Path

from lcarstv.core.blocks import load_episode_metadata
from lcarstv.core.clock import now_utc
from lcarstv.core.commercial_catalog import CommercialCatalog
from lcarstv.core.commercials import CommercialPool
from lcarstv.core.config import load_channels, load_settings_profile
from lcarstv.core.station import Station
from lcarstv.input.keyboard import KeyboardInput
from lcarstv.input.keys import InputEvent
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

    # Set system volume on Linux/Pi startup
    if os.name != "nt":
        try:
            import subprocess
            subprocess.run(
                ["amixer", "set", "PCM", "100%", "unmute"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2
            )
        except Exception as e:
            # Non-critical: continue even if amixer fails
            print(f"[warning] Could not set volume via amixer: {e}")

    # Single-instance lock: prevent multiple instances on Linux/Pi to avoid mpv IPC socket conflicts.
    # On Windows, this is a no-op to preserve existing behavior.
    from lcarstv.core.single_instance import SingleInstanceLock
    lock = SingleInstanceLock(enabled=(os.name != "nt"))
    if not lock.acquire():
        print("[lcarstv] Another instance is already running. Exiting.")
        return 1

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

    # Eager probing: populate duration cache for all media files on startup
    # This ensures the cache is complete on first run, making subsequent startups instant
    print("Building duration cache for all media files...")
    total_files = 0
    probed_count = 0
    cached_count = 0
    
    # First pass: count total files across all channels
    for call_sign, channel in station.channels.items():
        for block_id, block in channel.blocks_by_id.items():
            total_files += len(block.files)
    
    print(f"Scanning {total_files} media file(s) across {len(station.channels)} channel(s)...")
    
    # Second pass: probe all files
    for call_sign, channel in station.channels.items():
        for block_id, block in channel.blocks_by_id.items():
            for file_path in block.files:
                # Check if already cached (peek without probing)
                cached_dur = channel.durations.peek_duration_sec(
                    file_path,
                    default_duration_sec=settings.default_duration_sec
                )
                
                # Now get the actual duration (will probe if not cached)
                actual_dur = channel.durations.get_duration_sec(
                    file_path,
                    default_duration_sec=settings.default_duration_sec
                )
                
                # Track whether we probed (peek returned default) or used cache
                if cached_dur == settings.default_duration_sec and actual_dur != settings.default_duration_sec:
                    probed_count += 1
                else:
                    cached_count += 1
                
                # Progress reporting every 50 files
                processed = probed_count + cached_count
                if processed % 50 == 0 or processed == total_files:
                    print(f"  Progress: {processed}/{total_files} files ({probed_count} probed, {cached_count} cached)")
    
    print(f"Duration cache complete: {total_files} total ({probed_count} newly probed, {cached_count} from cache)")
    print()

    inp = KeyboardInput()
    print("LCARSTV dry-run" if args.dry_run else "LCARSTV playback")
    print("Controls: PageUp/Up=Channel Up, PageDown/Down=Channel Down, R=Reset All, Q=Quit")
    print()

    # Optional GPIO buttons (Pi/Linux only; must be explicitly enabled via settings.gpio_enable).
    gpio = None
    gpio_q: queue.SimpleQueue[InputEvent] = queue.SimpleQueue()
    if os.name != "nt" and bool(getattr(settings, "gpio_enable", False)):
        up_pin = getattr(settings, "gpio_btn_up", None)
        down_pin = getattr(settings, "gpio_btn_down", None)
        quit_pin = getattr(settings, "gpio_btn_quit", None)
        pull_up = bool(getattr(settings, "gpio_pull_up", True))
        bounce_sec = float(getattr(settings, "gpio_bounce_sec", 0.05))

        def _valid_pin(p: object) -> bool:
            try:
                return int(p) > 0
            except Exception:
                return False

        if not (_valid_pin(up_pin) and _valid_pin(down_pin)):
            print(
                "[gpio] gpio_enable=true but gpio_btn_up/gpio_btn_down are missing or invalid; GPIO disabled."
            )
        else:
            try:
                from lcarstv.input.gpio_buttons import GpioButtons

                gpio = GpioButtons(
                    on_up=lambda: gpio_q.put(InputEvent(kind="channel_up")),
                    on_down=lambda: gpio_q.put(InputEvent(kind="channel_down")),
                    on_quit=(lambda: gpio_q.put(InputEvent(kind="quit"))) if quit_pin is not None else None,
                    btn_up_pin=int(up_pin),
                    btn_down_pin=int(down_pin),
                    btn_quit_pin=int(quit_pin) if quit_pin is not None else None,
                    pull_up=pull_up,
                    bounce_sec=bounce_sec,
                )
                print(
                    f"[gpio] enabled: up={int(up_pin)} down={int(down_pin)}"
                    + (f" quit={int(quit_pin)}" if quit_pin is not None else "")
                    + f" pull_up={pull_up} bounce_sec={bounce_sec}"
                )
            except Exception as e:
                print(f"[gpio] failed to initialize; continuing without GPIO. error={e}")
                gpio = None

    player: MpvPlayer | None = None
    if not args.dry_run:
        player = MpvPlayer(
            debug=settings.debug,
            ipc_trace=settings.ipc_trace,
            static_burst_path=str(settings.static_burst_path) if settings.static_burst_path else None,
            static_burst_duration_sec=0.4,
            call_sign_inset_right_px=settings.call_sign_inset_right_px,
            call_sign_inset_top_px=settings.call_sign_inset_top_px,
            call_sign_duration_sec=settings.call_sign_duration_sec,
        )
    
    # Initialize commercial catalog for disk-based caching
    commercial_catalog = CommercialCatalog(
        path=repo_root / "data" / "commercial_catalog.json",
        debug=settings.debug,
    )
    
    # Initialize commercial pool with catalog
    commercial_pool = CommercialPool(
        commercials_dir=settings.commercials_dir,
        extensions=settings.extensions,
        debug=settings.debug,
        catalog=commercial_catalog,
    )

    # Throttle auto-advance polling (keep low CPU / low IPC spam).
    # Increased from 0.2s -> 0.4s -> 1.0s to reduce IPC overhead and improve button responsiveness.
    last_auto_poll = 0.0
    auto_poll_interval = 1.0
    last_auto_advanced_from: str | None = None

    def _norm_path(p: str | None) -> str | None:
        if not p:
            return None
        # mpv may return different slash styles / case on Windows.
        return str(p).replace("\\", "/").lower()

    # Suppress double-advances right after we load the next file.
    suppress_until_time: float = 0.0
    awaiting_mpv_path: str | None = None
    
    # Commercial playback state tracking
    current_episode_path: str | None = None
    episode_metadata: dict | None = None
    handled_break_indices: set[int] = set()
    in_commercial_break: bool = False
    
    # Metadata cache: avoid re-reading JSON files every poll cycle
    # Key: normalized file path, Value: parsed metadata dict or None
    episode_metadata_cache: dict[str, dict | None] = {}
    
    # Break check optimization: only check breaks every N seconds (not every poll)
    last_break_check_time: float = 0.0
    break_check_interval: float = 2.0  # Check every 2 seconds instead of 1.0s
    
    def _play_commercials(count: int = 3) -> InputEvent | None:
        """Play a sequence of random commercials.
        
        Args:
            count: Number of commercials to play (default: 3)
        
        Returns:
            InputEvent if interrupted by user, None if completed normally
        """
        if player is None:
            return None
        
        commercials = commercial_pool.pick_random(count=count)
        if not commercials:
            if settings.debug:
                print("[debug] commercials: no commercials available to play")
            return None
        
        if settings.debug:
            print(f"[debug] commercials: playing {len(commercials)} commercial(s)")
        
        for i, comm_path in enumerate(commercials, 1):
            if settings.debug:
                print(f"[debug] commercials: [{i}/{len(commercials)}] {comm_path.name}")
            
            # Play commercial from start (no call-sign OSD, no static burst)
            player.play(str(comm_path), 0.0)
            
            # Wait for commercial to finish
            # Poll for EOF with a reasonable timeout
            max_wait_time = 300.0  # 5 minutes max per commercial
            start_wait = time.time()
            
            while True:
                # Check for user input during commercial playback (allow interruption)
                while True:
                    try:
                        gevt = gpio_q.get_nowait()
                        if gevt.kind in ("quit", "channel_up", "channel_down"):
                            # Silent for quit; log only channel changes when debug enabled
                            if settings.debug and gevt.kind != "quit":
                                print(f"[debug] commercials: interrupted by {gevt.kind}")
                            return gevt
                    except Exception:
                        break
                
                evt = inp.poll()
                if evt is not None and evt.kind in ("quit", "channel_up", "channel_down"):
                    # Silent for quit; log only channel changes when debug enabled
                    if settings.debug and evt.kind != "quit":
                        print(f"[debug] commercials: interrupted by {evt.kind}")
                    return evt
                
                if time.time() - start_wait > max_wait_time:
                    if settings.debug:
                        print(f"[debug] commercials: timeout waiting for commercial to finish")
                    break
                
                # Check if commercial ended
                trigger = player.poll_end_of_episode(end_epsilon_sec=0.25)
                if trigger is not None:
                    if settings.debug:
                        reason, _, _ = trigger
                        print(f"[debug] commercials: commercial ended ({reason})")
                    break
                
                time.sleep(0.05)
        
        return None
    
    def _check_and_handle_breaks() -> tuple[bool, InputEvent | None]:
        """Check if we need to interrupt playback for an in-episode commercial break.
        
        Optimized to:
        - Use metadata cache (avoid re-reading JSON every call)
        - Throttle checks to every 2 seconds
        - Early-exit when far from any break (30s lookahead window)
        
        Returns:
            Tuple of (break_was_handled, interrupted_event)
            - break_was_handled: True if a break was played (even if interrupted)
            - interrupted_event: InputEvent if user interrupted, None otherwise
        """
        nonlocal current_episode_path, episode_metadata, handled_break_indices, in_commercial_break
        nonlocal last_break_check_time, episode_metadata_cache
        
        if player is None:
            return (False, None)
        
        # Only check breaks if show_commercials is enabled for the active channel
        active_chan = station.channels.get(station.active_call_sign)
        if active_chan is None:
            return (False, None)
        
        # Get channel config to check show_commercials flag
        channel_cfg = channels_cfg.by_call_sign().get(station.active_call_sign)
        if channel_cfg is None or not channel_cfg.show_commercials:
            return (False, None)
        
        # Get currently playing file
        current_media = player.current_media_path
        if current_media is None:
            return (False, None)
        
        current_media_norm = _norm_path(current_media)
        
        # Check if we switched to a new episode
        if current_episode_path != current_media_norm:
            # New episode: load metadata from cache or disk and reset break tracking
            current_episode_path = current_media_norm
            
            # Check cache first
            if current_media_norm in episode_metadata_cache:
                episode_metadata = episode_metadata_cache[current_media_norm]
            else:
                # Not in cache: load from disk and cache it
                episode_metadata = load_episode_metadata(Path(current_media))
                episode_metadata_cache[current_media_norm] = episode_metadata
                
                # Limit cache size to prevent unbounded memory growth
                if len(episode_metadata_cache) > 100:
                    # Remove oldest entries (first 20)
                    keys_to_remove = list(episode_metadata_cache.keys())[:20]
                    for k in keys_to_remove:
                        episode_metadata_cache.pop(k, None)
            
            handled_break_indices = set()
            last_break_check_time = 0.0  # Force immediate check for new episode
            
            # Get time-pos for initial break marking
            time_pos = player._get_float_property("time-pos")
            
            # Mark breaks that are already past as handled (prevent retroactive triggers when tuning mid-episode)
            if episode_metadata is not None and time_pos is not None:
                breaks = episode_metadata.get("breaks", [])
                for i, brk in enumerate(breaks):
                    # If we're already past the end of this break, mark it as handled
                    if time_pos >= float(brk["end"]):
                        handled_break_indices.add(i)
                        if settings.debug:
                            print(f"[debug] commercials: marking break {i+1} as already-past (time_pos={time_pos:.2f}s >= end={float(brk['end']):.2f}s)")
            
            if settings.debug:
                if episode_metadata is not None:
                    num_breaks = len(episode_metadata.get("breaks", []))
                    num_handled = len(handled_break_indices)
                    print(f"[debug] commercials: loaded metadata for {Path(current_media).name} ({num_breaks} break(s), {num_handled} already-past)")
                else:
                    print(f"[debug] commercials: no metadata for {Path(current_media).name}")
        
        # No metadata means no breaks to handle
        if episode_metadata is None:
            return (False, None)
        
        breaks = episode_metadata.get("breaks", [])
        if not breaks:
            return (False, None)
        
        # Throttle: only check breaks every N seconds (not every main loop poll)
        current_time = time.time()
        if current_time - last_break_check_time < break_check_interval:
            return (False, None)
        
        last_break_check_time = current_time
        
        # Get current playback position
        time_pos = player._get_float_property("time-pos")
        if time_pos is None:
            return (False, None)
        
        # Early-exit optimization: check if we're far from ANY unhandled break
        # Only do detailed checking if we're within 30s of a break start
        lookahead_window = 30.0
        near_any_break = False
        for i, brk in enumerate(breaks):
            if i in handled_break_indices:
                continue
            start = float(brk["start"])
            if time_pos >= (start - lookahead_window):
                near_any_break = True
                break
        
        if not near_any_break:
            # Far from any breaks; skip expensive checking
            return (False, None)
        
        # We're near a break; check each break window
        for i, brk in enumerate(breaks):
            # Skip already-handled breaks
            if i in handled_break_indices:
                continue
            
            start = float(brk["start"])
            end = float(brk["end"])
            
            # Check if we've crossed into this break window
            if time_pos >= start:
                # Mark this break as handled
                handled_break_indices.add(i)
                
                if settings.debug:
                    print(f"[debug] commercials: triggering break {i+1}/{len(breaks)} at {time_pos:.2f}s (window: {start:.2f}-{end:.2f})")
                
                # Play commercials (may be interrupted by user input)
                in_commercial_break = True
                interrupted_event = _play_commercials(count=3)
                in_commercial_break = False
                
                # If interrupted, return the event for the main loop to handle
                if interrupted_event is not None:
                    return (True, interrupted_event)
                
                # Resume episode at break end time
                if settings.debug:
                    print(f"[debug] commercials: resuming episode at {end:.2f}s")
                
                player.play(current_media, end)
                
                # Set a guard to prevent immediate re-triggers
                player.set_playback_guard(seconds=1.0, reason="COMMERCIAL_BREAK")
                
                return (True, None)
        
        return (False, None)

    try:

        # Initial tune
        info = station.tune_to(station.active_call_sign, now_utc())
        if player is not None:
            player.play_with_static_burst(info.current_file, info.position_sec, call_sign=info.call_sign)

        def _handle_input_event(evt: InputEvent) -> int | None:
            nonlocal current_episode_path, episode_metadata, handled_break_indices
            
            if evt.kind == "quit":
                print("Exiting.")
                return 0
            if evt.kind == "channel_up":
                # Reset commercial state when changing channels
                current_episode_path = None
                episode_metadata = None
                handled_break_indices = set()
                info = station.channel_up(now_utc())
                if player is not None:
                    player.play_with_static_burst(info.current_file, info.position_sec, call_sign=info.call_sign)
            if evt.kind == "channel_down":
                # Reset commercial state when changing channels
                current_episode_path = None
                episode_metadata = None
                handled_break_indices = set()
                info = station.channel_down(now_utc())
                if player is not None:
                    player.play_with_static_burst(info.current_file, info.position_sec, call_sign=info.call_sign)
            if evt.kind == "reset_all":
                # Reset all channels to fresh state
                current_episode_path = None
                episode_metadata = None
                handled_break_indices = set()
                info = station.reset_all_channels(now_utc())
                if player is not None:
                    player.play_with_static_burst(info.current_file, info.position_sec, call_sign=info.call_sign)
            return None

        while True:
            # Drain queued GPIO events (edge callbacks) first.
            while True:
                try:
                    gevt = gpio_q.get_nowait()
                except Exception:
                    break
                rc = _handle_input_event(gevt)
                if rc is not None:
                    return rc

            evt = inp.poll()
            if evt is not None:
                rc = _handle_input_event(evt)
                if rc is not None:
                    return rc

            # Auto-advance: EOF or virtual schedule rollover.
            if player is not None:
                now = now_utc()
                t = time.time()
                if t - last_auto_poll >= auto_poll_interval:
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
                    
                    # Check for in-episode commercial breaks (only when not already in a break)
                    if not in_commercial_break:
                        break_handled, interrupted_event = _check_and_handle_breaks()
                        
                        # If user interrupted during the break, handle the event
                        if interrupted_event is not None:
                            rc = _handle_input_event(interrupted_event)
                            if rc is not None:
                                return rc
                            # Skip to next iteration (channel change or other action)
                            continue
                        
                        if break_handled:
                            # A commercial break was just played (completed normally)
                            # Reset suppression and continue (skip auto-advance logic this iteration)
                            suppress_until_time = time.time() + 0.5
                            awaiting_mpv_path = _norm_path(current_media)
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
                            
                            # Check if we should play between-episode commercials
                            channel_cfg = channels_cfg.by_call_sign().get(station.active_call_sign)
                            if channel_cfg is not None and channel_cfg.show_commercials:
                                if settings.debug:
                                    print(f"[debug] commercials: playing between-episode commercials")
                                interrupted_event = _play_commercials(count=3)
                                
                                # If interrupted, handle the event instead of playing next episode
                                if interrupted_event is not None:
                                    if settings.debug:
                                        print(f"[debug] commercials: between-episode interrupted, handling event")
                                    rc = _handle_input_event(interrupted_event)
                                    if rc is not None:
                                        return rc
                                    # Skip to next iteration to let the channel change take effect
                                    continue
                            
                            # Now play the next episode
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

                        player.play(expected_file, expected_pos)

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

                        player.play(expected_file, expected_pos)

                        # Suppress retriggers while mpv transitions back.
                        suppress_until_time = time.time() + 0.5
                        awaiting_mpv_path = _norm_path(expected_file)

            # low CPU polling loop; no threads.
            time.sleep(0.05)
    finally:
        inp.close()
        if gpio is not None:
            try:
                gpio.close()
            except Exception:
                pass
        if player is not None:
            player.close()
        lock.release()
