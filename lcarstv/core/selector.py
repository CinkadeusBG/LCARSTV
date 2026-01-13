from __future__ import annotations

import hashlib
import random
import re
import copy
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .state_store import PersistedChannel, PersistedState, StateStore


def _fingerprint_items(items: tuple[str, ...]) -> str:
    # Stable fingerprint of eligible set to detect library changes.
    joined = "\n".join(str(x).lower() for x in items)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _seed_for(call_sign: str, *, bag_epoch: int, items_fp: str) -> int:
    raw = f"{call_sign}:{bag_epoch}:{items_fp}".encode("utf-8")
    # 64-bit seed
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=False)


def _parse_episode_info(item_id: str) -> tuple[int, int] | None:
    """Parse SxxExx pattern from item ID (typically a file path).
    
    Returns (season, episode) tuple or None if pattern not found.
    """
    # Extract filename from path-like strings
    filename = Path(item_id).name if "/" in item_id or "\\" in item_id else item_id
    
    # Match patterns like S01E05, s02e12, etc.
    match = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
    if match:
        season = int(match.group(1))
        episode = int(match.group(2))
        return (season, episode)
    return None


def _sort_items_sequentially(items: tuple[str, ...]) -> list[str]:
    """Sort items by season/episode number, falling back to alphabetical for items without SxxExx.
    
    Items with episode info are sorted first by season then episode.
    Items without episode info are sorted alphabetically and placed at the end.
    """
    items_with_ep: list[tuple[str, int, int]] = []
    items_without_ep: list[str] = []
    
    for item in items:
        ep_info = _parse_episode_info(item)
        if ep_info:
            season, episode = ep_info
            items_with_ep.append((item, season, episode))
        else:
            items_without_ep.append(item)
    
    # Sort items with episode info by (season, episode)
    items_with_ep.sort(key=lambda x: (x[1], x[2]))
    
    # Sort items without episode info alphabetically
    items_without_ep.sort(key=lambda x: str(x).lower())
    
    # Combine: episodic content first, then non-episodic
    result = [item for item, _, _ in items_with_ep]
    result.extend(items_without_ep)
    
    return result


@dataclass
class SmartRandomSelector:
    """Shuffle-bag + cooldown, persisted per channel.

    In v2, the scheduler operates on arbitrary *item IDs* (block IDs).
    """

    store: StateStore
    state: PersistedState
    debug: bool = False

    # Thread-local preview state for read-only tuning.
    # Allows deterministic multi-rollover selection without mutating persisted scheduler state.
    _tls: threading.local = field(default_factory=threading.local, init=False, repr=False)

    def _get_channel_preview(self, call_sign: str) -> PersistedChannel:
        """Get a per-thread preview copy of a channel scheduler state.

        This must NOT mutate `self.state.channels`.
        """

        cs = call_sign.strip().upper()
        preview: dict[str, PersistedChannel] | None = getattr(self._tls, "preview_channels", None)
        if preview is None:
            preview = {}
            self._tls.preview_channels = preview

        if cs in preview:
            return preview[cs]

        base = self.state.channels.get(cs)
        if base is None:
            # No persisted scheduler state yet. Create ephemeral defaults.
            base = PersistedChannel(bag=[], recent=[])

        # Deep copy so we can advance bag_index/recent without side effects.
        ch = copy.deepcopy(base)
        if ch.bag is None:
            ch.bag = []
        if ch.recent is None:
            ch.recent = []
        ch.bag_index = max(0, int(ch.bag_index))
        ch.bag_epoch = max(0, int(ch.bag_epoch))
        preview[cs] = ch
        return ch

    def _get_channel_ref(self, call_sign: str, *, persist: bool) -> PersistedChannel:
        return self._get_channel(call_sign) if persist else self._get_channel_preview(call_sign)

    def _get_channel(self, call_sign: str) -> PersistedChannel:
        cs = call_sign.strip().upper()
        if cs not in self.state.channels:
            self.state.channels[cs] = PersistedChannel(bag=[], recent=[])
        ch = self.state.channels[cs]
        if ch.bag is None:
            ch.bag = []
        if ch.recent is None:
            ch.recent = []
        ch.bag_index = max(0, int(ch.bag_index))
        ch.bag_epoch = max(0, int(ch.bag_epoch))
        return ch

    def _reshuffle(self, call_sign: str, ch: PersistedChannel, items: tuple[str, ...], *, log: bool = True) -> None:
        # Build bag from current eligible set.
        bag = [str(x) for x in items]
        items_fp = _fingerprint_items(items)
        seed = _seed_for(call_sign, bag_epoch=ch.bag_epoch, items_fp=items_fp)
        rng = random.Random(seed)
        rng.shuffle(bag)
        ch.bag = bag
        ch.bag_index = 0
        ch.bag_epoch += 1
        if self.debug and log:
            print(
                f"[debug] {call_sign} reshuffle: bag_size={len(bag)} bag_epoch={ch.bag_epoch}"
            )

    def ensure_initialized(
        self,
        call_sign: str,
        items: tuple[str, ...],
        *,
        persist: bool = True,
        save: bool = True,
    ) -> None:
        """Ensure we have a valid bag for the current eligible set.

        Args:
            persist: If False, operate on a preview copy and never mutate persisted scheduler state.
            save: If False, do not write to StateStore (even if persist=True). Used so callers can
                batch multiple state changes into a single `StateStore.save()`.
        """
        ch = self._get_channel_ref(call_sign, persist=persist)
        eligible = {str(x) for x in items}

        # Prune recent/last_played that no longer exist.
        if ch.last_played and ch.last_played not in eligible:
            ch.last_played = None
        if ch.recent:
            ch.recent = [p for p in ch.recent if p in eligible]

        # If bag missing or doesn't match eligible set, regenerate.
        bag_set = set(ch.bag or [])
        if not ch.bag or bag_set != eligible:
            # Keep existing bag_epoch so reshuffles are stable across restarts.
            self._reshuffle(call_sign, ch, items, log=bool(persist and save))
            if persist and save:
                self.store.save(self.state)
            return

        # Clamp bag_index
        if ch.bag_index < 0 or ch.bag_index > len(ch.bag):
            ch.bag_index = 0
            if persist and save:
                self.store.save(self.state)

    def pick_next_sequential(
        self,
        *,
        call_sign: str,
        items: tuple[str, ...],
        persist: bool = True,
        save: bool = True,
    ) -> str:
        """Pick the next item in sequential order (S01E01, S01E02, etc.).
        
        Uses sequential_index to track position in the sorted list.
        Wraps back to start when reaching the end.
        """
        if not items:
            raise ValueError("items must be non-empty")
        
        cs = call_sign.strip().upper()
        ch = self._get_channel_ref(cs, persist=persist)
        
        # Sort items sequentially
        sorted_items = _sort_items_sequentially(items)
        
        # Clamp index to valid range
        if ch.sequential_index < 0 or ch.sequential_index >= len(sorted_items):
            ch.sequential_index = 0
        
        # Select current item
        selected = sorted_items[ch.sequential_index]
        
        # Advance to next item (wrap around at end)
        ch.sequential_index = (ch.sequential_index + 1) % len(sorted_items)
        
        if persist and save:
            self.store.save(self.state)
        
        if self.debug and persist and save:
            print(
                f"[debug] {cs} sequential: selected={selected} next_index={ch.sequential_index}/{len(sorted_items)}"
            )
        
        return str(selected)

    def pick_next_aggregate(
        self,
        *,
        call_sign: str,
        source_infos: dict[str, dict[str, Any]],
        persist: bool = True,
        save: bool = True,
    ) -> str:
        """Pick the next item from an aggregate channel.
        
        Aggregate channels create "sets" where each set contains one block from each source channel.
        Sets are shuffled and played sequentially. When a set is exhausted, a new set is created.
        
        Args:
            call_sign: The aggregate channel's call sign
            source_infos: Dict mapping source call_sign -> {
                "eligible_block_ids": tuple of block IDs,
                "is_sequential": bool,
                "cooldown": int
            }
            persist: Whether to persist state changes
            save: Whether to save to disk
            
        Returns:
            The next block ID to play
        """
        if not source_infos:
            raise ValueError(f"{call_sign}: aggregate channel has no sources")
        
        cs = call_sign.strip().upper()
        ch = self._get_channel_ref(cs, persist=persist)
        
        # Initialize aggregate state if needed
        if ch.aggregate_set is None:
            ch.aggregate_set = []
        if ch.aggregate_source_states is None:
            ch.aggregate_source_states = {}
        
        # Check if we need to create a new set
        if ch.aggregate_set_index >= len(ch.aggregate_set):
            # Build new set: pick one block from each source
            new_set: list[str] = []
            
            for source_cs, source_info in source_infos.items():
                eligible = source_info["eligible_block_ids"]
                is_sequential = source_info["is_sequential"]
                cooldown = source_info["cooldown"]
                
                if not eligible:
                    # Skip sources with no media (shouldn't happen in normal operation)
                    if self.debug and persist and save:
                        print(f"[debug] {cs} aggregate: skipping {source_cs} (no eligible blocks)")
                    continue
                
                # Get or initialize shadow state for this source
                if source_cs not in ch.aggregate_source_states:
                    ch.aggregate_source_states[source_cs] = {}
                
                shadow_state = ch.aggregate_source_states[source_cs]
                
                # Pick next block from this source
                if is_sequential:
                    # Sequential: use shadow sequential_index
                    seq_index = int(shadow_state.get("sequential_index", 0))
                    sorted_items = _sort_items_sequentially(tuple(eligible))
                    
                    # Wrap around if needed
                    if seq_index < 0 or seq_index >= len(sorted_items):
                        seq_index = 0
                    
                    selected = sorted_items[seq_index]
                    
                    # Advance shadow index
                    seq_index = (seq_index + 1) % len(sorted_items)
                    shadow_state["sequential_index"] = seq_index
                    
                    if self.debug and persist and save:
                        print(
                            f"[debug] {cs} aggregate: {source_cs} sequential selected={selected} next_index={seq_index}/{len(sorted_items)}"
                        )
                else:
                    # Random with cooldown: use shadow bag state
                    if "bag" not in shadow_state or not shadow_state["bag"]:
                        # Initialize shadow bag
                        shadow_state["bag"] = []
                        shadow_state["bag_index"] = 0
                        shadow_state["bag_epoch"] = 0
                        shadow_state["recent"] = []
                        shadow_state["last_played"] = None
                    
                    # Ensure bag is initialized and valid
                    bag = shadow_state.get("bag", [])
                    bag_set = set(bag)
                    eligible_set = set(eligible)
                    
                    if not bag or bag_set != eligible_set:
                        # Reshuffle
                        bag = list(eligible)
                        items_fp = _fingerprint_items(tuple(eligible))
                        bag_epoch = int(shadow_state.get("bag_epoch", 0))
                        seed = _seed_for(f"{cs}:{source_cs}", bag_epoch=bag_epoch, items_fp=items_fp)
                        rng = random.Random(seed)
                        rng.shuffle(bag)
                        shadow_state["bag"] = bag
                        shadow_state["bag_index"] = 0
                        shadow_state["bag_epoch"] = bag_epoch + 1
                        
                        if self.debug and persist and save:
                            print(
                                f"[debug] {cs} aggregate: {source_cs} reshuffle bag_size={len(bag)} bag_epoch={shadow_state['bag_epoch']}"
                            )
                    
                    bag_index = int(shadow_state.get("bag_index", 0))
                    recent = list(shadow_state.get("recent", []))
                    last_played = shadow_state.get("last_played")
                    cooldown_n = max(0, int(cooldown))
                    
                    # Clamp recent to cooldown size
                    if cooldown_n > 0:
                        recent = recent[-cooldown_n:]
                    else:
                        recent = []
                    
                    # Pick next from bag (with cooldown avoidance)
                    selected: str | None = None
                    for _ in range(2):  # Two passes: strict then relaxed
                        if bag_index >= len(bag):
                            # Bag exhausted, reshuffle
                            items_fp = _fingerprint_items(tuple(eligible))
                            bag_epoch = int(shadow_state.get("bag_epoch", 0))
                            seed = _seed_for(f"{cs}:{source_cs}", bag_epoch=bag_epoch, items_fp=items_fp)
                            rng = random.Random(seed)
                            rng.shuffle(bag)
                            shadow_state["bag"] = bag
                            shadow_state["bag_index"] = 0
                            shadow_state["bag_epoch"] = bag_epoch + 1
                            bag_index = 0
                        
                        # Walk through bag
                        while bag_index < len(bag):
                            cand = bag[bag_index]
                            bag_index += 1
                            
                            # Avoid immediate repeat
                            if last_played and cand == last_played:
                                continue
                            # Avoid recent (first pass only)
                            if cooldown_n > 0 and cand in recent:
                                continue
                            
                            selected = cand
                            break
                        
                        if selected:
                            break
                    
                    if not selected:
                        # Fallback: just pick first item
                        selected = bag[0]
                        bag_index = 1
                    
                    # Update shadow state
                    shadow_state["bag_index"] = bag_index
                    shadow_state["last_played"] = selected
                    if cooldown_n > 0:
                        recent = [r for r in recent if r != selected]
                        recent.append(selected)
                        recent = recent[-cooldown_n:]
                        shadow_state["recent"] = recent
                    else:
                        shadow_state["recent"] = []
                    
                    if self.debug and persist and save:
                        print(
                            f"[debug] {cs} aggregate: {source_cs} random selected={selected} bag_index={bag_index} recent_len={len(recent)}"
                        )
                
                new_set.append(selected)
            
            if not new_set:
                raise ValueError(f"{cs}: aggregate channel produced empty set")
            
            # Shuffle the new set
            # Use aggregate channel's own epoch for set shuffling
            set_epoch = int(ch.aggregate_source_states.get("_set_epoch", 0))
            set_seed = _seed_for(cs, bag_epoch=set_epoch, items_fp=_fingerprint_items(tuple(new_set)))
            rng = random.Random(set_seed)
            rng.shuffle(new_set)
            
            ch.aggregate_set = new_set
            ch.aggregate_set_index = 0
            ch.aggregate_source_states["_set_epoch"] = set_epoch + 1
            
            if self.debug and persist and save:
                print(
                    f"[debug] {cs} aggregate: new set created set_size={len(new_set)} set_epoch={set_epoch + 1}"
                )
        
        # Return next block from set
        selected_block = ch.aggregate_set[ch.aggregate_set_index]
        ch.aggregate_set_index += 1
        
        if persist and save:
            self.store.save(self.state)
        
        if self.debug and persist and save:
            print(
                f"[debug] {cs} aggregate: selected={selected_block} set_index={ch.aggregate_set_index}/{len(ch.aggregate_set)}"
            )
        
        return str(selected_block)

    def pick_next(
        self,
        *,
        call_sign: str,
        items: tuple[str, ...],
        cooldown: int,
        current_item: str | None,
        persist: bool = True,
        save: bool = True,
        sequential: bool = False,
    ) -> str:
        """Pick the next item to air.

        If sequential=True, picks items in order (S01E01, S01E02, etc.).
        Otherwise uses shuffle-bag with cooldown.
        
        Hard rule: avoid immediate repeat.
        Soft rule: avoid last N (cooldown) via recent queue.
        """

        if not items:
            raise ValueError("items must be non-empty")
        
        # Route to sequential mode if enabled
        if sequential:
            return self.pick_next_sequential(
                call_sign=call_sign,
                items=items,
                persist=persist,
                save=save,
            )

        cs = call_sign.strip().upper()
        self.ensure_initialized(cs, items, persist=persist, save=save)

        ch = self._get_channel_ref(cs, persist=persist)

        last_played = ch.last_played
        immediate_block = str(current_item) if current_item is not None else last_played
        cooldown_n = max(0, int(cooldown))
        recent = list(ch.recent or [])
        # Ensure recent doesn't exceed cooldown
        if cooldown_n > 0:
            recent = recent[-cooldown_n:]
        else:
            recent = []

        def is_immediate_repeat(candidate: str) -> bool:
            return immediate_block is not None and candidate == immediate_block

        def is_in_recent(candidate: str) -> bool:
            return candidate in recent

        # Try strict selection (not immediate, not in recent). If blocked, reshuffle once and try again.
        selected: str | None = None
        for strict_pass in range(2):
            if strict_pass == 1:
                # second pass: new ordering
                self._reshuffle(cs, ch, items, log=bool(persist and save))

            if ch.bag_index >= len(ch.bag or []):
                # bag exhausted -> reshuffle
                self._reshuffle(cs, ch, items, log=bool(persist and save))

            # Walk forward through bag
            while ch.bag_index < len(ch.bag or []):
                cand = str((ch.bag or [])[ch.bag_index])
                ch.bag_index += 1

                if is_immediate_repeat(cand):
                    if self.debug and persist and save:
                        print(f"[debug] {cs} skip: immediate repeat: {cand}")
                    continue
                if is_in_recent(cand):
                    if self.debug and persist and save:
                        print(f"[debug] {cs} skip: in recent: {cand}")
                    continue

                selected = cand
                break

            if selected is not None:
                break

        if selected is None:
            # Relax rule minimally: allow recent hits, but still avoid immediate repeat.
            if self.debug and persist and save:
                print(f"[debug] {cs} relax: cooldown blocked all candidates; allowing recent")

            # Make sure bag exists.
            if not ch.bag:
                self._reshuffle(cs, ch, items, log=bool(persist and save))

            # Try a full pass through the bag.
            attempts = 0
            while attempts < len(ch.bag):
                if ch.bag_index >= len(ch.bag):
                    ch.bag_index = 0
                cand = ch.bag[ch.bag_index]
                ch.bag_index += 1
                attempts += 1
                if is_immediate_repeat(cand):
                    if self.debug and persist and save:
                        print(f"[debug] {cs} skip: immediate repeat (relaxed): {cand}")
                    continue
                selected = cand
                break

            if selected is None:
                # Only possible if library size == 1.
                selected = ch.bag[0]

        # Update scheduler state
        ch.last_played = selected
        if cooldown_n > 0:
            # Avoid duplicates for tiny libraries; still preserves ordering.
            recent = [p for p in recent if p != selected]
            recent.append(selected)
            recent = recent[-cooldown_n:]
            ch.recent = recent
        else:
            ch.recent = []

        if persist and save:
            self.store.save(self.state)

        if self.debug and persist and save:
            print(
                f"[debug] {cs} selected: {selected} bag_index={ch.bag_index} recent_len={len(ch.recent or [])}"
            )

        return str(selected)
