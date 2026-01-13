from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import random

from .config import ChannelsConfig, Settings
from .blocks import (
    Block,
    build_channel_blocks,
    compute_block_playback,
    display_block_id,
    implicit_block_id_for_file,
    norm_abs_path,
)
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

    def _prewarm_channels(self, now: datetime) -> None:
        """Prewarm channel runtime state to reduce first-tune latency.

        Why this exists:
        - Tuning is intentionally read-only (no StateStore writes).
        - The first tune to a channel can still be slow because:
          1) SmartRandomSelector may need to build a per-thread preview copy of a
             channel's scheduler state (deep copy of the bag).
          2) ChannelRuntime.get_current_block() may run ffprobe (DurationCache.get_duration_sec)
             if the currently-airing block contains files without cached durations.

        Prewarming shifts those one-time costs to startup.

        Notes:
        - This intentionally MAY write to durations.json (duration cache) because that
          is a performance cache, not live scheduler state.
        - This MUST NOT write to the station StateStore; we only operate with persist=False
          for scheduler preview operations.
        """

        for call_sign, chan in self.channels.items():
            # Ensure preview scheduler state exists (no persistence).
            try:
                chan.selector.ensure_initialized(
                    call_sign=call_sign,
                    items=chan.eligible_block_ids,
                    persist=False,
                    save=False,
                )
            except Exception:
                # Best-effort; never fail boot due to prewarm.
                pass

            # Bring schedule up to now in-memory (no persistence).
            # Keep debug=False here to avoid startup log spam; tune/advance already log
            # at the appropriate times.
            try:
                chan.sync_to_now(now, reason="STARTUP", debug=False, persist=False)
            except Exception:
                pass

            # Hydrate durations for the *currently airing* block.
            # This may trigger ffprobe for uncached files, but only for one block per channel.
            try:
                chan.get_current_block()
            except Exception:
                pass

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
        
        # Process normal channels first, then aggregate channels
        normal_channels = [ch for ch in channels_cfg.channels if ch.aggregate_from_channels is None]
        aggregate_channels = [ch for ch in channels_cfg.channels if ch.aggregate_from_channels is not None]
        
        # Build normal channels
        for ch in normal_channels:
            scan = scan_media_dirs(repo_root, ch.media_dirs, settings.extensions)
            if not scan.files:
                # Keep deterministic, but allow boot without media present.
                # A real deployment will have media files.
                # We'll synthesize a placeholder filename so logic still runs.
                placeholder = repo_root / "media" / ch.call_sign / "_NO_MEDIA_FOUND_.mp4"
                files = (placeholder,)
            else:
                files = scan.files

            explicit_blocks = tuple((b.id, b.files) for b in (ch.blocks or ()))
            blocks_by_id, eligible_block_ids = build_channel_blocks(
                call_sign=ch.call_sign,
                repo_root=repo_root,
                media_dirs=ch.media_dirs,
                scanned_files=files,
                explicit_blocks=explicit_blocks,
                durations=durations,
                default_duration_sec=float(settings.default_duration_sec),
            )

            cooldown = int(ch.cooldown) if ch.cooldown is not None else int(settings.default_cooldown)

            # Restore persisted live state if valid.
            # NOTE: We must migrate file-based scheduler state (v1) *before* calling
            # ensure_initialized(), otherwise ensure_initialized() would prune it.
            persisted_ch = selector.state.channels.get(ch.call_sign)
            if persisted_ch is None:
                selector.state.channels[ch.call_sign] = PersistedChannel()
                persisted_ch = selector.state.channels[ch.call_sign]

            def clamp_started_at(s: datetime | None) -> datetime | None:
                if s is None:
                    return None
                return s if s <= now else now

            restored_started = clamp_started_at(persisted_ch.started_at)

            # --- Migrate scheduler state (bag/recent/last_played) from v1 file paths to block ids ---
            # We do this even if state.version already says 2, because an older file-based
            # state file might have been saved with version bumped.
            def map_item(x: str) -> str | None:
                if not x:
                    return None
                # If already a known block id, keep.
                if x in blocks_by_id:
                    return x
                # If looks like a file path, map to explicit-containing or implicit id.
                key = norm_abs_path(x)
                for bb in blocks_by_id.values():
                    for f in bb.files:
                        if norm_abs_path(f) == key:
                            return bb.id
                # Otherwise treat as implicit single-file id.
                return implicit_block_id_for_file(x)

            if selector.state.version < 2:
                selector.state.version = 2

            if persisted_ch.bag is not None:
                mapped = [map_item(v) for v in persisted_ch.bag]
                bag2 = [m for m in mapped if m is not None and m in blocks_by_id]
                persisted_ch.bag = list(dict.fromkeys(bag2))
            if persisted_ch.recent is not None:
                mapped = [map_item(v) for v in persisted_ch.recent]
                persisted_ch.recent = [m for m in mapped if m is not None and m in blocks_by_id]
            if persisted_ch.last_played:
                lp = map_item(persisted_ch.last_played)
                persisted_ch.last_played = lp if lp in blocks_by_id else None

            # Ensure scheduler has a bag for this channel/eligible blocks.
            # Avoid writing yet; we batch writes below.
            selector.ensure_initialized(ch.call_sign, eligible_block_ids, persist=True, save=False)

            # --- Migration + restore ---
            # Priority:
            # 1) v2: current_block_id
            # 2) v1: current_file -> map to block and adjust started_at to block start
            current_block_id: str | None = None
            started_at: datetime | None = restored_started

            if persisted_ch.current_block_id and persisted_ch.current_block_id in blocks_by_id:
                current_block_id = persisted_ch.current_block_id
            elif persisted_ch.current_file and started_at is not None:
                # Try to find which block contains this file.
                file_key = norm_abs_path(persisted_ch.current_file)
                found_block: Block | None = None
                found_index: int | None = None
                for b in blocks_by_id.values():
                    for i, f in enumerate(b.files):
                        if norm_abs_path(f) == file_key:
                            found_block = b
                            found_index = i
                            break
                    if found_block is not None:
                        break

                if found_block is not None and found_index is not None:
                    current_block_id = found_block.id
                    # Old started_at referred to file start; v2 started_at refers to block start.
                    # So shift started_at backward by sum(durations before current file).
                    shift = float(sum(found_block.durations_sec[:found_index]))
                    started_at = started_at - timedelta(seconds=shift)

            # If we still don't have a valid block, initialize.
            if current_block_id is None or started_at is None:
                current_block_id = selector.pick_next(
                    call_sign=ch.call_sign,
                    items=eligible_block_ids,
                    cooldown=cooldown,
                    current_item=None,
                )
                block = blocks_by_id[current_block_id]

                dur = max(1.0, float(block.total_duration_sec))
                offset = random.random() * dur
                started_at = now - timedelta(seconds=offset)

                if settings.debug:
                    pb = compute_block_playback(block=block, started_at=started_at, now=now)
                    print(
                        f"[debug] {ch.call_sign} init: block={display_block_id(current_block_id)} started_at={started_at.isoformat()} offset={offset:.2f}s file={pb.file_path.name} file_offset={pb.file_offset_sec:.2f}s"
                    )

                # Persist initial live state.
                pch = selector.state.channels.get(ch.call_sign)
                if pch is not None:
                    pch.current_block_id = current_block_id
                    pch.current_file = None
                    pch.started_at = started_at
                    store.save(selector.state)
            else:
                # Persist migration/clamp fixes when we can.
                pch = selector.state.channels.get(ch.call_sign)
                if pch is not None:
                    pch.current_block_id = current_block_id
                    pch.current_file = None
                    pch.started_at = started_at
                    store.save(selector.state)

                if settings.debug:
                    print(
                        f"[debug] {ch.call_sign} restore: block={display_block_id(current_block_id)} started_at={started_at.isoformat()}"
                    )

            assert started_at is not None
            state = ChannelState(call_sign=ch.call_sign, current_block_id=current_block_id, started_at=started_at)
            channels[ch.call_sign] = ChannelRuntime(
                call_sign=ch.call_sign,
                blocks_by_id=blocks_by_id,
                eligible_block_ids=eligible_block_ids,
                settings=settings,
                cooldown=cooldown,
                selector=selector,
                store=store,
                state=state,
                durations=durations,
                sequential_playthrough=ch.sequential_playthrough,
            )
        
        # Build aggregate channels
        for ch in aggregate_channels:
            assert ch.aggregate_from_channels is not None
            
            # Validate that all source channels exist
            for source_cs in ch.aggregate_from_channels:
                if source_cs not in channels:
                    raise ValueError(
                        f"{ch.call_sign}: aggregate source channel {source_cs!r} does not exist. "
                        f"Aggregate channels must be defined after their sources in channels.json."
                    )
            
            # Collect blocks and metadata from all source channels
            aggregate_blocks_by_id: dict[str, Block] = {}
            aggregate_source_infos: dict[str, dict] = {}
            
            for source_cs in ch.aggregate_from_channels:
                source_chan = channels[source_cs]
                
                # Add all blocks from this source to the aggregate's block pool
                for block_id, block in source_chan.blocks_by_id.items():
                    aggregate_blocks_by_id[block_id] = block
                
                # Store source metadata for the selector
                aggregate_source_infos[source_cs] = {
                    "eligible_block_ids": source_chan.eligible_block_ids,
                    "is_sequential": source_chan.sequential_playthrough,
                    "cooldown": source_chan.cooldown,
                }
            
            if not aggregate_blocks_by_id:
                raise ValueError(f"{ch.call_sign}: aggregate channel has no blocks from any source")
            
            # Aggregate channels don't have their own eligible list - they pull from sources dynamically
            # But we need a combined list for get_current_block() to work
            aggregate_eligible = tuple(aggregate_blocks_by_id.keys())
            
            cooldown = int(ch.cooldown) if ch.cooldown is not None else 0
            
            # Restore or initialize aggregate channel state
            persisted_ch = selector.state.channels.get(ch.call_sign)
            if persisted_ch is None:
                selector.state.channels[ch.call_sign] = PersistedChannel()
                persisted_ch = selector.state.channels[ch.call_sign]
            
            # Initialize aggregate-specific state if needed
            if persisted_ch.aggregate_set is None:
                persisted_ch.aggregate_set = []
            if persisted_ch.aggregate_set_index is None:
                persisted_ch.aggregate_set_index = 0
            if persisted_ch.aggregate_source_states is None:
                persisted_ch.aggregate_source_states = {}
            
            def clamp_started_at(s: datetime | None) -> datetime | None:
                if s is None:
                    return None
                return s if s <= now else now
            
            restored_started = clamp_started_at(persisted_ch.started_at)
            
            # Restore or initialize current block
            current_block_id: str | None = None
            started_at: datetime | None = restored_started
            
            if persisted_ch.current_block_id and persisted_ch.current_block_id in aggregate_blocks_by_id:
                current_block_id = persisted_ch.current_block_id
            
            # If we don't have a valid block, pick one using aggregate logic
            if current_block_id is None or started_at is None:
                current_block_id = selector.pick_next_aggregate(
                    call_sign=ch.call_sign,
                    source_infos=aggregate_source_infos,
                    persist=True,
                    save=False,
                )
                block = aggregate_blocks_by_id[current_block_id]
                
                dur = max(1.0, float(block.total_duration_sec))
                offset = random.random() * dur
                started_at = now - timedelta(seconds=offset)
                
                if settings.debug:
                    pb = compute_block_playback(block=block, started_at=started_at, now=now)
                    print(
                        f"[debug] {ch.call_sign} aggregate init: block={display_block_id(current_block_id)} started_at={started_at.isoformat()} offset={offset:.2f}s file={pb.file_path.name} file_offset={pb.file_offset_sec:.2f}s"
                    )
                
                # Persist initial state
                pch = selector.state.channels.get(ch.call_sign)
                if pch is not None:
                    pch.current_block_id = current_block_id
                    pch.current_file = None
                    pch.started_at = started_at
                    store.save(selector.state)
            else:
                # Persist restored state
                pch = selector.state.channels.get(ch.call_sign)
                if pch is not None:
                    pch.current_block_id = current_block_id
                    pch.current_file = None
                    pch.started_at = started_at
                    store.save(selector.state)
                
                if settings.debug:
                    print(
                        f"[debug] {ch.call_sign} aggregate restore: block={display_block_id(current_block_id)} started_at={started_at.isoformat()}"
                    )
            
            assert started_at is not None
            state = ChannelState(call_sign=ch.call_sign, current_block_id=current_block_id, started_at=started_at)
            channels[ch.call_sign] = ChannelRuntime(
                call_sign=ch.call_sign,
                blocks_by_id=aggregate_blocks_by_id,
                eligible_block_ids=aggregate_eligible,
                settings=settings,
                cooldown=cooldown,
                selector=selector,
                store=store,
                state=state,
                durations=durations,
                sequential_playthrough=False,  # Aggregate channels don't use sequential mode directly
                is_aggregate=True,
                aggregate_source_infos=aggregate_source_infos,
            )

        call_signs = channels_cfg.ordered_call_signs()
        active = call_signs[0]

        st = Station(call_signs=call_signs, channels=channels, active_call_sign=active, settings=settings)
        st._prewarm_channels(now)
        return st

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

        # Tuning must be strictly read-only: no state persistence.
        # Rollover catch-up is allowed in-memory, but we block any StateStore.save().
        with chan.store.disallow_saves(reason="TUNE"):
            # If we are behind, apply schedule-based rollovers in-memory.
            # Log reason as SCHEDULE to match the advance instrumentation contract.
            chan.sync_to_now(now, reason="SCHEDULE", debug=self.settings.debug, persist=False)
            pb = chan.scheduled_playback(now)
            pos = float(pb.file_offset_sec)

            if self.settings.debug:
                print(
                    f"[debug] tune call_sign={call_sign} block_id={chan.state.current_block_id} current_file={pb.file_path} started_at={chan.state.started_at.isoformat()} file_offset_sec={pos:.2f}"
                )
        print(f"Block: {display_block_id(chan.state.current_block_id)}")
        print(f"Airing: {pb.file_path.name}")
        print(f"Started at: {chan.state.started_at.isoformat()}")
        print(f"Position: {pos:.2f}s")
        print()
        return TuneInfo(
            call_sign=call_sign,
            block_id=chan.state.current_block_id,
            current_file=str(pb.file_path),
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
        chan.sync_to_now(now, reason=reason, debug=self.settings.debug, persist=True)
        pb = chan.scheduled_playback(now)
        pos = float(pb.file_offset_sec)
        return TuneInfo(
            call_sign=call_sign,
            block_id=chan.state.current_block_id,
            current_file=str(pb.file_path),
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

        # NOTE:
        # Historically this method forced a rollover even if the real schedule hadn't ended
        # (by pushing `started_at` into the future). That breaks live-TV invariants and can
        # cause revisits to restart at 0s.
        #
        # New rule: we only advance when the schedule says we're at/after end.
        # Callers that observe early mpv EOF/IDLE (e.g. user scrub) should correct playback
        # by re-loading/seeking to the scheduled file/position instead of forcing an advance.

        chan = self.channels[call_sign]
        chan.sync_to_now(now, reason=reason, debug=self.settings.debug, persist=True)

        pb = chan.scheduled_playback(now)
        pos = float(pb.file_offset_sec)
        return TuneInfo(
            call_sign=call_sign,
            block_id=chan.state.current_block_id,
            current_file=str(pb.file_path),
            started_at=chan.state.started_at,
            position_sec=pos,
        )
