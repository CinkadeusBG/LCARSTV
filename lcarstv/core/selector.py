from __future__ import annotations

import hashlib
import random
import copy
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .state_store import PersistedChannel, PersistedState, StateStore


def _fingerprint_files(files: tuple[Path, ...]) -> str:
    # Stable fingerprint of eligible set to detect library changes.
    joined = "\n".join(str(p).lower() for p in files)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _seed_for(call_sign: str, *, bag_epoch: int, files_fp: str) -> int:
    raw = f"{call_sign}:{bag_epoch}:{files_fp}".encode("utf-8")
    # 64-bit seed
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=False)


@dataclass
class SmartRandomSelector:
    """Shuffle-bag + cooldown, persisted per channel."""

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

    def _reshuffle(self, call_sign: str, ch: PersistedChannel, files: tuple[Path, ...], *, log: bool = True) -> None:
        # Build bag from current eligible set.
        bag = [str(p) for p in files]
        files_fp = _fingerprint_files(files)
        seed = _seed_for(call_sign, bag_epoch=ch.bag_epoch, files_fp=files_fp)
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
        files: tuple[Path, ...],
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
        eligible = {str(p) for p in files}

        # Prune recent/last_played that no longer exist.
        if ch.last_played and ch.last_played not in eligible:
            ch.last_played = None
        if ch.recent:
            ch.recent = [p for p in ch.recent if p in eligible]

        # If bag missing or doesn't match eligible set, regenerate.
        bag_set = set(ch.bag or [])
        if not ch.bag or bag_set != eligible:
            # Keep existing bag_epoch so reshuffles are stable across restarts.
            self._reshuffle(call_sign, ch, files, log=bool(persist and save))
            if persist and save:
                self.store.save(self.state)
            return

        # Clamp bag_index
        if ch.bag_index < 0 or ch.bag_index > len(ch.bag):
            ch.bag_index = 0
            if persist and save:
                self.store.save(self.state)

    def pick_next(
        self,
        *,
        call_sign: str,
        files: tuple[Path, ...],
        cooldown: int,
        current_file: Path | None,
        persist: bool = True,
        save: bool = True,
    ) -> Path:
        """Pick the next file to air.

        Hard rule: avoid immediate repeat.
        Soft rule: avoid last N (cooldown) via recent queue.
        """

        if not files:
            raise ValueError("files must be non-empty")

        cs = call_sign.strip().upper()
        self.ensure_initialized(cs, files, persist=persist, save=save)

        ch = self._get_channel_ref(cs, persist=persist)

        last_played = ch.last_played
        immediate_block = str(current_file) if current_file is not None else last_played
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
                self._reshuffle(cs, ch, files, log=bool(persist and save))

            if ch.bag_index >= len(ch.bag or []):
                # bag exhausted -> reshuffle
                self._reshuffle(cs, ch, files, log=bool(persist and save))

            # Walk forward through bag
            while ch.bag_index < len(ch.bag or []):
                cand = str((ch.bag or [])[ch.bag_index])
                ch.bag_index += 1

                if is_immediate_repeat(cand):
                    if self.debug and persist and save:
                        print(f"[debug] {cs} skip: immediate repeat: {Path(cand).name}")
                    continue
                if is_in_recent(cand):
                    if self.debug and persist and save:
                        print(f"[debug] {cs} skip: in recent: {Path(cand).name}")
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
                self._reshuffle(cs, ch, files, log=bool(persist and save))

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
                        print(f"[debug] {cs} skip: immediate repeat (relaxed): {Path(cand).name}")
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
                f"[debug] {cs} selected: {Path(selected).name} bag_index={ch.bag_index} recent_len={len(ch.recent or [])}"
            )

        return Path(selected)
