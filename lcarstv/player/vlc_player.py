from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from shutil import which


@dataclass
class VlcPlayer:
    """Simple VLC-based playback backend.

    Design goals:
    - Deterministic, simple process control: kill/restart on each `play()`.
    - Works well in headless environments (tty1/systemd) and composite output.
    - Avoid hardcoding framebuffer device paths; honor SDL env vars if user sets them.
    - Do not swallow stdout/stderr by default.
    """

    debug: bool = False

    # Prefer cvlc when available (CLI-only), fall back to vlc.
    cvlc_exe: str = "cvlc"
    vlc_exe: str = "vlc"

    # Use SDL video output by default for composite setups.
    vout: str = "sdl"

    # Playback state tracking.
    _proc: subprocess.Popen[str] | None = None
    _current_media_path: str | None = None

    # Guard to suppress immediate "end" triggers right after (re)start.
    _guard_until: float = 0.0

    # Edge-triggered EOF/exit detection.
    _last_proc_running: bool = False
    _ended_for_path: str | None = None

    # Best-effort: avoid repeating diagnostics every start.
    _did_diag: bool = False

    def set_playback_guard(self, *, seconds: float, reason: str) -> None:
        seconds = max(0.0, float(seconds))
        self._guard_until = max(self._guard_until, time.time() + seconds)
        if self.debug:
            ms = int(seconds * 1000)
            print(f"[debug] guard: set until={self._guard_until:.3f} (+{ms}ms) reason={reason}")

    def playback_guard_active(self) -> bool:
        return time.time() < float(self._guard_until)

    @property
    def current_media_path(self) -> str | None:
        return self._current_media_path

    # --- mpv-compat helpers (used by app loop) ---
    def current_duration_sec(self) -> float | None:
        # Not required for VLC backend (schedule is authoritative).
        return None

    def current_mpv_path(self) -> str | None:
        # Used by app.py only to suppress double-advance while the player transitions.
        # For VLC, we don't have a separate reported "path"; return our requested path.
        return self._current_media_path

    def _select_vlc_exe(self) -> str | None:
        """Return the best VLC executable path, or None if not found.

        Search order:
        1) PATH: cvlc
        2) PATH: vlc
        3) Windows common install paths
        """

        # Use PATH resolution if possible.
        cvlc_path = which(self.cvlc_exe)
        if cvlc_path:
            return cvlc_path

        vlc_path = which(self.vlc_exe)
        if vlc_path:
            return vlc_path

        # Windows fallback: common installer locations.
        if os.name == "nt":
            candidates = [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            ]
            for c in candidates:
                try:
                    if Path(c).exists():
                        return c
                except Exception:
                    continue

        return None

    def resolve_vlc_exe(self) -> str | None:
        """Return the resolved VLC executable path (if available)."""

        return self._select_vlc_exe()

    def has_vlc(self) -> bool:
        """Public helper for factory selection."""

        return self.resolve_vlc_exe() is not None

    def _diag(self) -> None:
        if self._did_diag or not self.debug:
            return
        self._did_diag = True

        exe = self._select_vlc_exe()
        if exe is None:
            print("[debug] vlc: not found on PATH (cvlc/vlc)")
            return

        print(f"[debug] vlc: selected exe: {exe}")

        # Best-effort diagnostics: never crash playback.
        try:
            subprocess.run([exe, "--version"], check=False)
        except Exception as e:
            print(f"[debug] vlc: version probe failed: {e}")

        # Some VLC builds support --vout=help. If unsupported, this just prints an error.
        try:
            subprocess.run([exe, "--vout=help"], check=False)
        except Exception as e:
            print(f"[debug] vlc: vout help probe failed: {e}")

    def start(self) -> None:
        # Nothing to keep warm; VLC is started per-play.
        self._diag()

    def _build_args(self, file_path: str, start_sec: float) -> list[str]:
        exe = self._select_vlc_exe()
        if exe is None:
            raise FileNotFoundError("VLC not found on PATH. Install vlc/cvlc or adjust PATH.")

        start_sec = max(0.0, float(start_sec))

        # Keep this minimal and explicit.
        # Notes:
        # - --intf dummy: no UI.
        # - --no-video-title-show: hide filename/title overlay.
        # - --no-osd: disable OSD.
        # - --avcodec-hw=none: force software decoding.
        # - --vout=sdl: SDL output tends to behave well for composite.
        # - --start-time=<sec>: deterministic start offset.
        # - --play-and-exit: process exits at end of file (enables EOF via proc exit).
        args = [
            exe,
            "--intf",
            "dummy",
            "--fullscreen",
            "--no-video-title-show",
            "--no-osd",
            "--avcodec-hw=none",
            f"--vout={self.vout}",
            f"--start-time={start_sec}",
            "--play-and-exit",
            file_path,
        ]
        return args

    def _log_launch_context(self, args: list[str], env: dict[str, str]) -> None:
        if not self.debug:
            return
        print(f"[debug] vlc: launch args: {subprocess.list2cmdline(args)}")
        # Only *honor* SDL env vars by inheriting env; do not hardcode defaults.
        keys = ["SDL_VIDEODRIVER", "SDL_FBDEV", "SDL_AUDIODRIVER", "DISPLAY", "WAYLAND_DISPLAY"]
        for k in keys:
            if k in env:
                print(f"[debug] vlc: env {k}={env.get(k)!r}")

    def stop(self) -> None:
        # Stop playback cleanly; never raise.
        p = self._proc
        self._proc = None

        self._current_media_path = None
        self._last_proc_running = False
        self._ended_for_path = None

        if p is None:
            return

        try:
            # Most reliable for VLC; terminate first, then kill if needed.
            p.terminate()
            try:
                p.wait(timeout=2.0)
            except Exception:
                p.kill()
        except Exception:
            pass

    def close(self) -> None:
        self.stop()

    def play_with_static_burst(self, file_path: str, start_sec: float, *, call_sign: str | None = None) -> None:
        # mpv-specific UX effect; for VLC backend keep deterministic and simple.
        self.play(file_path, start_sec, call_sign=call_sign)

    def play(self, file_path: str, start_sec: float, *, call_sign: str | None = None) -> None:
        # call_sign currently mpv-only (OSD overlay); accepted for compatibility.
        _ = call_sign

        # Ensure old process is gone before starting a new one.
        self.stop()
        self.start()

        p = Path(file_path)
        if not p.exists():
            if self.debug:
                print(f"[debug] vlc: file does not exist; skipping: {file_path}")
            return

        env = dict(os.environ)
        args = self._build_args(str(p), start_sec)
        self._log_launch_context(args, env)

        # Do not swallow output: inherit stdout/stderr for journald/console.
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            text=True,
            env=env,
        )

        self._current_media_path = str(p)
        self._last_proc_running = True
        self._ended_for_path = None

        # Guard window after spawn to avoid transient end triggers.
        self.set_playback_guard(seconds=0.75, reason="LOAD_START")

    def poll_end_of_episode(
        self, *, end_epsilon_sec: float = 0.25
    ) -> tuple[str, float | None, float | None] | None:
        """Return a trigger tuple once when VLC exits for the current media.

        For VLC we use process exit as the authoritative EOF signal.
        Return shape matches MpvPlayer: (reason, time_pos, duration).
        """

        _ = end_epsilon_sec  # unused, kept for API parity

        if self.playback_guard_active():
            # Update running edge state but suppress triggers.
            self._last_proc_running = self._proc is not None and self._proc.poll() is None
            return None

        if self._current_media_path is None:
            self._last_proc_running = self._proc is not None and self._proc.poll() is None
            return None

        # If already ended for this path, do not re-trigger.
        if self._ended_for_path == self._current_media_path:
            self._last_proc_running = self._proc is not None and self._proc.poll() is None
            return None

        # Detect exit edge.
        running = self._proc is not None and self._proc.poll() is None
        exited_edge = (not running) and bool(self._last_proc_running)
        self._last_proc_running = bool(running)

        if exited_edge:
            self._ended_for_path = self._current_media_path
            return ("EOF", None, None)

        return None
