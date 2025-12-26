from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
import threading
from typing import Any

from .clock import parse_iso_utc, to_iso_utc


@dataclass
class PersistedChannel:
    # Live station state
    # v2: block-based live state
    current_block_id: str | None = None
    # v1 legacy (file-based) preserved for migration; may be absent in new saves
    current_file: str | None = None
    started_at: datetime | None = None

    # Scheduler state
    bag: list[str] | None = None
    bag_index: int = 0
    recent: list[str] | None = None
    last_played: str | None = None
    bag_epoch: int = 0

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PersistedChannel":
        started_at_raw = d.get("started_at")
        started_at = parse_iso_utc(str(started_at_raw)) if started_at_raw else None
        return PersistedChannel(
            current_block_id=d.get("current_block_id"),
            current_file=d.get("current_file"),
            started_at=started_at,
            bag=list(d.get("bag", [])) if d.get("bag") is not None else None,
            bag_index=int(d.get("bag_index", 0) or 0),
            recent=list(d.get("recent", [])) if d.get("recent") is not None else None,
            last_played=d.get("last_played"),
            bag_epoch=int(d.get("bag_epoch", 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_block_id": self.current_block_id,
            "current_file": self.current_file,
            "started_at": to_iso_utc(self.started_at) if self.started_at else None,
            "bag": list(self.bag or []),
            "bag_index": int(self.bag_index),
            "recent": list(self.recent or []),
            "last_played": self.last_played,
            "bag_epoch": int(self.bag_epoch),
        }


@dataclass
class PersistedState:
    version: int
    channels: dict[str, PersistedChannel]

    @staticmethod
    def empty() -> "PersistedState":
        return PersistedState(version=2, channels={})


class StateStore:
    """Loads/saves scheduler + live channel state to a single JSON file."""

    def __init__(self, *, path: Path, debug: bool = False) -> None:
        self.path = path
        self.debug = debug
        # Debug-only guardrail: disallow saves in contexts that must be read-only
        # (e.g., tune). Thread-local so timers/OSD clear callbacks don't interfere.
        self._tls = threading.local()

    def _saves_disallowed_reason(self) -> str | None:
        return getattr(self._tls, "disallow_saves_reason", None)

    @contextmanager
    def disallow_saves(self, *, reason: str) -> Any:
        """Debug-only: block `save()` calls inside this context.

        We use this to assert that tuning is strictly read-only.
        """

        prev = self._saves_disallowed_reason()
        self._tls.disallow_saves_reason = str(reason)
        try:
            yield
        finally:
            # Restore previous value (supports nesting).
            if prev is None:
                try:
                    delattr(self._tls, "disallow_saves_reason")
                except Exception:
                    pass
            else:
                self._tls.disallow_saves_reason = prev

    def load(self) -> PersistedState:
        try:
            if not self.path.exists():
                return PersistedState.empty()
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            chans_raw = data.get("channels", {})
            chans: dict[str, PersistedChannel] = {}
            if isinstance(chans_raw, dict):
                for k, v in chans_raw.items():
                    if isinstance(v, dict):
                        chans[str(k).upper()] = PersistedChannel.from_dict(v)
            version = int(data.get("version", 1) or 1)
            return PersistedState(version=version, channels=chans)
        except Exception as e:
            # Corrupt state should not crash the app.
            if self.debug:
                print(f"[debug] state: failed to load {self.path}: {e}")
            return PersistedState.empty()

    def save(self, state: PersistedState) -> None:
        # Guardrail: tune must never persist state.
        # In debug mode, log and skip the write.
        reason = self._saves_disallowed_reason()
        if reason is not None:
            if self.debug:
                print(f"[debug] ERROR state: save blocked (reason={reason})")
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            # v2: block-based schedule/live state
            "version": int(max(2, int(state.version))),
            "channels": {k: v.to_dict() for k, v in state.channels.items()},
        }
        # Atomic-ish write
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
