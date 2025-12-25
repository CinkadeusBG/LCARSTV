from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _norm_path_key(p: str | Path) -> str:
    """Normalize paths for stable cache keys (Windows-safe).

    - Lowercase
    - Convert backslashes to forward slashes
    - Keep drive letters (e.g. z:/...)
    """

    s = str(p)
    return s.replace("\\", "/").lower()


@dataclass
class DurationCache:
    """Persistent best-effort media duration cache.

    Backed by a JSON file mapping normalized file paths -> duration seconds.

    - Uses ffprobe when not cached.
    - Falls back to default_duration_sec on errors.
    """

    path: Path
    debug: bool = False
    ffprobe_exe: str = "ffprobe"

    _loaded: bool = False
    _durations: dict[str, dict[str, Any]] | None = None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._durations = {}
        try:
            if not self.path.exists():
                return
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            d = data.get("durations") if isinstance(data, dict) else None
            if isinstance(d, dict):
                # Keep only sane entries.
                for k, v in d.items():
                    if not isinstance(k, str) or not isinstance(v, dict):
                        continue
                    dur = v.get("duration_sec")
                    if isinstance(dur, (int, float)) and float(dur) > 0:
                        self._durations[_norm_path_key(k)] = {
                            "duration_sec": float(dur),
                            "mtime_ns": int(v.get("mtime_ns", 0) or 0),
                            "size": int(v.get("size", 0) or 0),
                        }
        except Exception as e:
            # Corrupt duration cache should not crash the app.
            if self.debug:
                print(f"[debug] duration-cache: failed to load {self.path}: {e}")
            self._durations = {}

    def _save(self) -> None:
        self._ensure_loaded()
        assert self._durations is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "durations": dict(sorted(self._durations.items(), key=lambda kv: kv[0])),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def get_duration_sec(self, file_path: str | Path, *, default_duration_sec: float) -> float:
        """Return best-effort duration for a file.

        Rules:
        - If cached and file exists (and metadata matches), return cached.
        - Else run ffprobe, cache duration, return it.
        - Else fall back to default_duration_sec.
        """

        self._ensure_loaded()
        assert self._durations is not None

        p = Path(file_path)
        key = _norm_path_key(p)

        # Placeholder/missing files: fall back.
        if not p.exists():
            if self.debug:
                print(f"[debug] duration-cache: missing file; using default {default_duration_sec:.2f}s: {file_path}")
            return max(1.0, float(default_duration_sec))

        try:
            st = p.stat()
            mtime_ns = int(getattr(st, "st_mtime_ns", 0) or 0)
            size = int(getattr(st, "st_size", 0) or 0)
        except Exception:
            mtime_ns = 0
            size = 0

        cached = self._durations.get(key)
        if cached is not None:
            dur = cached.get("duration_sec")
            if (
                isinstance(dur, (int, float))
                and float(dur) > 0
                and int(cached.get("mtime_ns", 0) or 0) == mtime_ns
                and int(cached.get("size", 0) or 0) == size
            ):
                return float(dur)

        # Probe via ffprobe.
        # Use format duration, suppress all output except the number.
        cmd = [
            self.ffprobe_exe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(p),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            out = (proc.stdout or "").strip()
            dur = float(out) if out else 0.0
            if proc.returncode != 0 or not (dur > 0):
                raise RuntimeError(f"ffprobe rc={proc.returncode} out={out!r} err={(proc.stderr or '').strip()!r}")

            self._durations[key] = {
                "duration_sec": float(dur),
                "mtime_ns": mtime_ns,
                "size": size,
            }
            self._save()
            if self.debug:
                print(f"[debug] duration-cache: probed {Path(p).name} = {dur:.2f}s")
            return float(dur)
        except Exception as e:
            if self.debug:
                print(f"[debug] duration-cache: ffprobe failed; using default {default_duration_sec:.2f}s: {p} ({e})")
            return max(1.0, float(default_duration_sec))

