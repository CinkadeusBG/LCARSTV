from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings
from .duration_cache import DurationCache
from .blocks import Block, BlockPlayback, compute_block_playback, display_block_id
from .models import ChannelState
from .selector import SmartRandomSelector
from .state_store import StateStore


@dataclass
class ChannelRuntime:
    call_sign: str
    blocks_by_id: dict[str, Block]
    eligible_block_ids: tuple[str, ...]
    settings: Settings
    cooldown: int
    selector: SmartRandomSelector
    store: StateStore
    state: ChannelState
    durations: DurationCache
    sequential_playthrough: bool = False
    is_aggregate: bool = False
    aggregate_source_infos: dict[str, dict] | None = None

    def get_current_block(self) -> Block:
        if self.state.current_block_id not in self.blocks_by_id:
            # Invalid block ID - this can happen if:
            # 1. Aggregate channel's sources changed between runs
            # 2. Media files moved/deleted
            # 3. State file corruption
            # Recovery: pick a new valid block and update state
            import sys
            print(
                f"[WARNING] {self.call_sign}: invalid block ID {self.state.current_block_id!r} not in blocks pool. "
                f"This can happen if aggregate sources changed or media was moved. Picking new block...",
                file=sys.stderr
            )
            
            # Pick a new valid block
            if self.is_aggregate:
                new_block_id = self.selector.pick_next_aggregate(
                    call_sign=self.call_sign,
                    source_infos=self.aggregate_source_infos or {},
                    persist=True,
                    save=True,
                )
            else:
                new_block_id = self.selector.pick_next(
                    call_sign=self.call_sign,
                    items=self.eligible_block_ids,
                    cooldown=self.cooldown,
                    current_item=None,
                    persist=True,
                    save=True,
                    sequential=self.sequential_playthrough,
                )
            
            # Validate that the picked block actually exists in our current blocks pool
            # This can happen if selector state references blocks from a previous run
            if new_block_id not in self.blocks_by_id:
                print(
                    f"[WARNING] {self.call_sign}: recovery picked block {new_block_id!r} "
                    f"which is not in current blocks pool. Selecting fallback block...",
                    file=sys.stderr
                )
                # Fallback: pick any valid block from current pool
                if self.eligible_block_ids:
                    new_block_id = self.eligible_block_ids[0]
                elif self.blocks_by_id:
                    new_block_id = next(iter(self.blocks_by_id.keys()))
                else:
                    raise ValueError(f"{self.call_sign}: no blocks available in blocks_by_id")
                
                print(
                    f"[WARNING] {self.call_sign}: using fallback block {new_block_id}",
                    file=sys.stderr
                )
            
            # Update state
            self.state.current_block_id = new_block_id
            
            # Ensure we have a valid started_at
            from datetime import datetime, timedelta
            from .clock import now_utc
            if self.state.started_at is None or self.state.started_at > now_utc():
                # Pick a random offset within the new block
                import random
                block = self.blocks_by_id[new_block_id]
                dur = max(1.0, float(block.total_duration_sec))
                offset = random.random() * dur
                self.state.started_at = now_utc() - timedelta(seconds=offset)
            
            # Persist the fix
            self._persist_live_state()
            
            print(
                f"[WARNING] {self.call_sign}: recovered with new block {new_block_id}",
                file=sys.stderr
            )
        
        block = self.blocks_by_id[self.state.current_block_id]

        # Hydrate durations on-demand for the current block.
        # We avoid probing the entire library at startup, but for correct schedule math
        # we need accurate durations for whatever is currently airing.
        try:
            new_durs = tuple(
                float(
                    self.durations.get_duration_sec(
                        p,
                        default_duration_sec=float(self.settings.default_duration_sec),
                    )
                )
                for p in block.files
            )
            if new_durs != block.durations_sec:
                total = float(sum(new_durs))
                block = Block(
                    id=block.id,
                    files=block.files,
                    durations_sec=new_durs,
                    total_duration_sec=total,
                )
                self.blocks_by_id[self.state.current_block_id] = block
        except Exception:
            # Best-effort: schedule math will fall back to whatever durations we had.
            pass

        return block

    def scheduled_playback(self, now: datetime) -> BlockPlayback:
        block = self.get_current_block()
        return compute_block_playback(block=block, started_at=self.state.started_at, now=now)

    def _persist_live_state(self) -> None:
        st = self.selector.state
        ch = st.channels.get(self.call_sign)
        if ch is not None:
            ch.current_block_id = self.state.current_block_id
            # v2: do not redundantly persist current_file (derived from schedule math)
            ch.current_file = None
            ch.started_at = self.state.started_at
            # Persist scheduler and live state together.
            # Scheduler state may have been mutated by selector.pick_next(...save=False).
            self.store.save(st)

    def _persist_live_state_if(self, *, persist: bool) -> bool:
        if not persist:
            return False
        self._persist_live_state()
        return True

    def sync_to_now(
        self,
        now: datetime,
        *,
        reason: str = "SYNC",
        debug: bool = False,
        persist: bool = True,
    ) -> int:
        """Advance (rollover) until the current airing block contains `now`.

        Deterministic invariant:
        - started_at only ever moves forward by *total durations of aired blocks*.
        - current_block_id only advances when (now - started_at) >= duration(current_block).

        Returns:
            Number of block rollovers applied.
        """

        if self.settings.default_duration_sec <= 0:
            raise ValueError("default_duration_sec must be > 0")

        rollovers = 0
        while True:
            block = self.get_current_block()
            dur = float(block.total_duration_sec)
            elapsed = (now - self.state.started_at).total_seconds()

            # Keep debug output high-signal: per-rollover logs are printed below.

            if elapsed < dur:
                return rollovers

            old_block = self.state.current_block_id
            old_started = self.state.started_at

            # Advance time by the just-finished block duration.
            self.state.started_at = self.state.started_at + timedelta(seconds=float(dur))

            # Guardrail: started_at must never go into the future, otherwise revisits
            # will clamp position to 0 forever.
            if self.state.started_at > now:
                if debug:
                    print(
                        f"[debug] ERROR advance produced future started_at; clamping call_sign={self.call_sign} started_at={self.state.started_at.isoformat()} now={now.isoformat()}"
                    )
                self.state.started_at = now

            # Advance to next block.
            if self.is_aggregate:
                # Aggregate channel: pick from sources
                next_block_id = self.selector.pick_next_aggregate(
                    call_sign=self.call_sign,
                    source_infos=self.aggregate_source_infos or {},
                    persist=persist,
                    save=False,
                )
            else:
                # Normal channel: pick from own eligible blocks
                next_block_id = self.selector.pick_next(
                    call_sign=self.call_sign,
                    items=self.eligible_block_ids,
                    cooldown=self.cooldown,
                    current_item=old_block,
                    persist=persist,
                    # We persist scheduler+live state as a single write below.
                    save=False,
                    sequential=self.sequential_playthrough,
                )
            
            # Validate the selected block exists (safety check for stale selector state)
            if next_block_id not in self.blocks_by_id:
                import sys
                print(
                    f"[WARNING] {self.call_sign}: rollover picked block {next_block_id!r} "
                    f"which is not in current blocks pool. Selecting fallback block...",
                    file=sys.stderr
                )
                # Fallback: pick any valid block from current pool
                if self.eligible_block_ids:
                    next_block_id = self.eligible_block_ids[0]
                elif self.blocks_by_id:
                    next_block_id = next(iter(self.blocks_by_id.keys()))
                else:
                    raise ValueError(f"{self.call_sign}: no blocks available in blocks_by_id")
                
                print(
                    f"[WARNING] {self.call_sign}: using fallback block {next_block_id}",
                    file=sys.stderr
                )
            
            self.state.current_block_id = str(next_block_id)

            persisted = self._persist_live_state_if(persist=persist)
            rollovers += 1

            if debug:
                print(
                    f"[debug] rollover reason={reason} call_sign={self.call_sign} {display_block_id(old_block)} -> {display_block_id(self.state.current_block_id)} {old_started.isoformat()} -> {self.state.started_at.isoformat()} persisted={'yes' if persisted else 'no'}"
                )
