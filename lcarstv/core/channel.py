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

    def get_current_block(self) -> Block:
        if self.state.current_block_id not in self.blocks_by_id:
            raise KeyError(f"Unknown block id for {self.call_sign}: {self.state.current_block_id!r}")
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
            self.state.current_block_id = str(next_block_id)

            persisted = self._persist_live_state_if(persist=persist)
            rollovers += 1

            if debug:
                print(
                    f"[debug] rollover reason={reason} call_sign={self.call_sign} {display_block_id(old_block)} -> {display_block_id(self.state.current_block_id)} {old_started.isoformat()} -> {self.state.started_at.isoformat()} persisted={'yes' if persisted else 'no'}"
                )
