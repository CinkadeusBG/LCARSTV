from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import IO


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

    # Static burst configuration (for channel tune effect).
    static_burst_path: str | None = None
    static_burst_duration_sec: float = 0.5

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

    # Windows VLC config warning tracking.
    _vlc_config_warned: bool = False

    # Windows stderr log file handle.
    _stderr_log_file: IO[str] | None = None

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

        # Windows VLC config warning (once per session, or always in debug).
        if os.name == "nt" and (not self._vlc_config_warned or self.debug):
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                vlc_config = os.path.join(appdata, "vlc")
                if os.path.exists(vlc_config):
                    print(f"[info] VLC config found at {vlc_config}. If video appears off-screen, delete this folder and restart.")
                    self._vlc_config_warned = True

    def _build_args(self, file_path: str, start_sec: float) -> list[str]:
        exe = self._select_vlc_exe()
        if exe is None:
            raise FileNotFoundError("VLC not found on PATH. Install vlc/cvlc or adjust PATH.")

        start_sec = max(0.0, float(start_sec))

        # Platform-specific argument building.
        # Windows: Let VLC use native Direct3D/OpenGL output with default interface.
        # Linux/Pi: Use composite-friendly SDL output with dummy interface.
        is_windows = os.name == "nt"

        args = [exe, "--fullscreen", "--no-video-title-show", "--no-osd"]

        if is_windows:
            # Windows: native video output, no dummy interface.
            # Let VLC choose Direct3D/OpenGL automatically.
            pass
        else:
            # Linux/Pi: composite-friendly output.
            args.extend(["--intf", "dummy"])
            args.extend([f"--vout={self.vout}"])

        # Common args for all platforms.
        args.extend(["--avcodec-hw=none", f"--start-time={start_sec}", file_path])

        return args

    def _log_launch_context(self, args: list[str], env: dict[str, str]) -> None:
        if not self.debug:
            return
        
        platform_name = "Windows" if os.name == "nt" else "Linux/Pi"
        print(f"[debug] vlc: platform={platform_name}")
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

        # Close Windows stderr log file if open.
        if self._stderr_log_file:
            try:
                self._stderr_log_file.close()
            except Exception:
                pass
            self._stderr_log_file = None

        if p is None:
            return

        try:
            # Platform-specific robust termination.
            if os.name == "nt":
                # Windows: Use taskkill to terminate entire process tree.
                # VLC on Windows may spawn child processes that don't get cleaned up
                # by Python's terminate()/kill() alone.
                if self.debug:
                    print(f"[debug] vlc: stop: taskkill /PID {p.pid} /T /F")
                
                result = subprocess.run(
                    ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=3.0
                )
                
                if self.debug and result.returncode != 0:
                    print(f"[debug] vlc: taskkill returned {result.returncode}: {result.stderr.strip()}")
                
                # Brief wait to confirm termination.
                try:
                    p.wait(timeout=0.5)
                except Exception:
                    # Process already terminated or taskkill handled it.
                    pass
            else:
                # Linux: Use process group termination for clean shutdown.
                # Try SIGTERM first (graceful), then SIGKILL if needed.
                try:
                    if self.debug:
                        print(f"[debug] vlc: stop: killpg({p.pid}, SIGTERM)")
                    os.killpg(p.pid, signal.SIGTERM)
                    
                    # Wait briefly for graceful shutdown.
                    try:
                        p.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        # Process didn't terminate gracefully, use SIGKILL.
                        if self.debug:
                            print(f"[debug] vlc: stop: killpg({p.pid}, SIGKILL)")
                        os.killpg(p.pid, signal.SIGKILL)
                        try:
                            p.wait(timeout=0.5)
                        except Exception:
                            pass
                except ProcessLookupError:
                    # Process group already terminated.
                    if self.debug:
                        print(f"[debug] vlc: stop: process group {p.pid} already gone")
                except Exception as e:
                    # Fallback to standard terminate/kill if killpg fails.
                    if self.debug:
                        print(f"[debug] vlc: stop: killpg failed ({e}), using terminate/kill")
                    p.terminate()
                    try:
                        p.wait(timeout=1.0)
                    except Exception:
                        p.kill()
        except Exception as e:
            # Best-effort; never raise from stop().
            if self.debug:
                print(f"[debug] vlc: stop: exception during termination: {e}")

    def close(self) -> None:
        self.stop()

    def play_with_static_burst(self, file_path: str, start_sec: float, *, call_sign: str | None = None) -> None:
        """Play a short static burst before switching to the tuned channel.

        Sequence:
        - Play static.mp4 from the beginning
        - Wait for static_burst_duration_sec
        - Play the actual channel content at start_sec
        """

        # If static burst path is configured and exists, play it first.
        if self.static_burst_path:
            static_path = Path(self.static_burst_path)
            if static_path.exists():
                if self.debug:
                    print(f"[debug] vlc: static burst start: {self.static_burst_path}")

                # Set guard to suppress premature end triggers during static and tune.
                self.set_playback_guard(
                    seconds=float(self.static_burst_duration_sec) + 0.75,
                    reason="STATIC_TUNE"
                )

                # Play static burst from beginning.
                self.play(str(static_path), 0)

                # Wait for static burst duration.
                time.sleep(max(0.0, float(self.static_burst_duration_sec)))

                if self.debug:
                    print("[debug] vlc: static burst end")
            else:
                if self.debug:
                    print(f"[debug] vlc: static file missing, skipping: {self.static_burst_path}")

        # Play the actual channel content.
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

        # Platform-specific subprocess configuration.
        popen_kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": None,
            "stderr": None,
            "text": True,
            "env": env,
        }

        # Windows-specific: prevent console popup and log stderr to file.
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            # In non-debug mode, log stderr to temp directory.
            if not self.debug:
                temp_dir = os.environ.get("TEMP", ".")
                log_path = os.path.join(temp_dir, "lcarstv-vlc-stderr.log")
                try:
                    self._stderr_log_file = open(log_path, "w")
                    # Write header with timestamp and PID.
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._stderr_log_file.write(f"[{timestamp}] VLC stderr log, PID={os.getpid()}\n")
                    self._stderr_log_file.flush()
                    popen_kwargs["stderr"] = self._stderr_log_file
                except Exception as e:
                    # Best-effort logging; don't crash playback.
                    if self.debug:
                        print(f"[debug] vlc: failed to open stderr log: {e}")
        else:
            # Linux: Create new process group for reliable killpg() in stop().
            popen_kwargs["start_new_session"] = True

        self._proc = subprocess.Popen(args, **popen_kwargs)

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
