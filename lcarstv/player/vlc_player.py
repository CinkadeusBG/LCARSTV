from __future__ import annotations

import os
import subprocess
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import IO


class VlcRcError(RuntimeError):
    pass


@dataclass
class VlcRcClient:
    """Minimal VLC RC (remote control) client over TCP.

    VLC is started with:
      --extraintf rc --rc-host 127.0.0.1:4212

    We only need best-effort fire-and-forget commands.
    """

    host: str = "127.0.0.1"
    port: int = 4212
    debug: bool = False

    _sock: socket.socket | None = None

    def connected(self) -> bool:
        return self._sock is not None

    def connect(self, *, timeout_sec: float = 3.0, poll_interval_sec: float = 0.1) -> None:
        if self._sock is not None:
            return

        deadline = time.time() + max(0.0, float(timeout_sec))
        last_err: Exception | None = None

        while time.time() < deadline:
            try:
                s = socket.create_connection((self.host, int(self.port)), timeout=0.5)
                # Keep reads/writes bounded. We do not need to read responses.
                s.settimeout(0.5)
                self._sock = s
                # Drain any initial banner text (best-effort).
                self._drain()
                return
            except Exception as e:
                last_err = e
                time.sleep(max(0.0, float(poll_interval_sec)))

        raise VlcRcError(f"RC connect failed to {self.host}:{self.port}: {last_err}")

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.close()
        except Exception:
            pass
        finally:
            self._sock = None

    def _drain(self) -> None:
        """Best-effort read to avoid RC output growing indefinitely."""

        if self._sock is None:
            return

        try:
            while True:
                data = self._sock.recv(4096)
                if not data:
                    break
                # Stop early if we didn't fill the buffer.
                if len(data) < 4096:
                    break
        except socket.timeout:
            return
        except Exception:
            return

    def send(self, cmd: str) -> None:
        if self._sock is None:
            raise VlcRcError("RC socket not connected")

        cmd = str(cmd).rstrip("\r\n")
        payload = (cmd + "\n").encode("utf-8", errors="replace")

        if self.debug:
            print(f"[debug] vlc-rc: >> {cmd}")

        try:
            self._sock.sendall(payload)
            # Drain any immediate response (best-effort).
            self._drain()
        except Exception as e:
            raise VlcRcError(f"RC send failed ({cmd!r}): {e}")

    def request(self, cmd: str, *, timeout_sec: float = 0.5) -> str:
        """Send a command and read back a short response (best-effort)."""

        if self._sock is None:
            raise VlcRcError("RC socket not connected")

        cmd = str(cmd).rstrip("\r\n")
        payload = (cmd + "\n").encode("utf-8", errors="replace")

        if self.debug:
            print(f"[debug] vlc-rc: >> {cmd}")

        try:
            self._sock.sendall(payload)

            # Read until newline or timeout.
            old_timeout = self._sock.gettimeout()
            self._sock.settimeout(max(0.05, float(timeout_sec)))
            data = b""
            try:
                deadline = time.time() + max(0.0, float(timeout_sec))
                while time.time() < deadline and b"\n" not in data:
                    try:
                        chunk = self._sock.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    data += chunk
            finally:
                try:
                    self._sock.settimeout(old_timeout)
                except Exception:
                    pass

            # Drain remaining output.
            self._drain()
            return data.decode("utf-8", errors="ignore")
        except Exception as e:
            raise VlcRcError(f"RC request failed ({cmd!r}): {e}")


@dataclass
class VlcPlayer:
    """Persistent VLC-based playback backend controlled via RC (TCP).

    Design goals:
    - Start one VLC process in `start()`.
    - Reuse it for all `play()`/channel changes by using RC commands.
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

    # VLC RC interface endpoint.
    rc_host: str = "127.0.0.1"
    rc_port: int = 4212

    # Static burst configuration (for channel tune effect).
    static_burst_path: str | None = None
    static_burst_duration_sec: float = 0.5

    # Playback state tracking.
    _proc: subprocess.Popen[str] | None = None
    _rc: VlcRcClient | None = None
    _current_media_path: str | None = None

    # VLC state tracking for edge-triggered end detection.
    _last_is_playing: bool = False

    # Guard to suppress immediate "end" triggers right after (re)start.
    _guard_until: float = 0.0

    # Edge-triggered EOF detection.
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

    def _build_start_args(self) -> list[str]:
        exe = self._select_vlc_exe()
        if exe is None:
            raise FileNotFoundError("VLC not found on PATH. Install vlc/cvlc or adjust PATH.")

        is_windows = os.name == "nt"

        args: list[str] = [
            exe,
            "--fullscreen",
            "--no-video-title-show",
            "--no-osd",
            "--avcodec-hw=none",
            "--extraintf",
            "rc",
            "--rc-quiet",
            "--rc-host",
            f"{self.rc_host}:{int(self.rc_port)}",
        ]

        if is_windows:
            # Windows: do not force vout or dummy interface.
            pass
        else:
            # Linux/Pi: keep headless and composite-friendly defaults.
            # Allow SDL vout but do not hardcode any framebuffer device.
            args.extend(["--intf", "dummy"])
            if self.vout:
                args.append(f"--vout={self.vout}")

        return args

    def _quote_rc_arg(self, s: str) -> str:
        # RC parses quotes; we just need something safe for paths with spaces.
        # Escape embedded quotes.
        s = str(s).replace('"', r"\"")
        return f'"{s}"'

    def _rc_send(self, cmd: str) -> bool:
        """Send an RC command; returns False if it failed and the RC/VLC should be restarted."""

        if self._rc is None:
            return False
        try:
            # Log key commands only when debug.
            self._rc.send(cmd)
            return True
        except Exception as e:
            if self.debug:
                print(f"[debug] vlc: rc send failed; will restart on next play(): {e}")
            try:
                self._rc.close()
            except Exception:
                pass
            self._rc = None
            return False

    def _rc_query_is_playing(self) -> bool | None:
        """Best-effort check whether VLC thinks it is currently playing.

        The RC interface supports `is_playing` which prints 0/1.
        We attempt to read a short response from the socket.
        """

        if self._rc is None:
            return None

        try:
            txt = self._rc.request("is_playing", timeout_sec=0.25).strip()
            # Typical response contains "0" or "1" possibly with prompts.
            # Extract last token that is 0/1.
            for token in reversed(txt.replace("\r", " ").replace("\n", " ").split()):
                if token in ("0", "1"):
                    return token == "1"
        except Exception:
            return None

        return None

    def _rc_query_time_sec(self) -> int | None:
        if self._rc is None:
            return None

        try:
            txt = self._rc.request("get_time", timeout_sec=0.25).strip()
            # Extract last integer token.
            for token in reversed(txt.replace("\r", " ").replace("\n", " ").split()):
                try:
                    return int(token)
                except Exception:
                    continue
        except Exception:
            return None

        return None

    def _path_to_mrl(self, p: Path) -> str:
        """Convert a local path to a VLC-friendly MRL.

        On Windows, raw `C:\...` paths can be mis-parsed by VLC/RC; using a file URI
        is more reliable.
        """

        try:
            return p.resolve().as_uri()
        except Exception:
            return str(p)


    def start(self) -> None:
        # Ensure VLC is running and RC is connected.
        self._diag()

        # If we already have a running process + connected RC, nothing to do.
        if self._proc is not None and self._proc.poll() is None and self._rc is not None and self._rc.connected():
            return

        # If process exists but died, clean up handles.
        if self._proc is not None and self._proc.poll() is not None:
            self._proc = None

        # Reset RC client if present.
        if self._rc is not None:
            try:
                self._rc.close()
            except Exception:
                pass
            self._rc = None

        # Windows VLC config warning (once per session, or always in debug).
        if os.name == "nt" and (not self._vlc_config_warned or self.debug):
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                vlc_config = os.path.join(appdata, "vlc")
                if os.path.exists(vlc_config):
                    print(
                        f"[info] VLC config found at {vlc_config}. If video appears off-screen, delete this folder and restart."
                    )
                    self._vlc_config_warned = True

        env = dict(os.environ)
        args = self._build_start_args()
        self._log_launch_context(args, env)

        popen_kwargs: dict = {
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
            # Truncate each VLC session.
            if not self.debug:
                temp_dir = os.environ.get("TEMP", ".")
                log_path = os.path.join(temp_dir, "lcarstv-vlc-stderr.log")
                try:
                    # Close any prior session handle.
                    if self._stderr_log_file:
                        try:
                            self._stderr_log_file.close()
                        except Exception:
                            pass
                        self._stderr_log_file = None

                    self._stderr_log_file = open(log_path, "w")
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._stderr_log_file.write(f"[{timestamp}] VLC stderr log\n")
                    self._stderr_log_file.flush()
                    popen_kwargs["stderr"] = self._stderr_log_file
                except Exception as e:
                    if self.debug:
                        print(f"[debug] vlc: failed to open stderr log: {e}")

        self._proc = subprocess.Popen(args, **popen_kwargs)

        # Connect RC.
        self._rc = VlcRcClient(host=self.rc_host, port=int(self.rc_port), debug=self.debug)
        self._rc.connect(timeout_sec=3.0, poll_interval_sec=0.1)

    def _build_args(self, file_path: str, start_sec: float) -> list[str]:
        """Legacy shim (kept only to minimize file churn).

        VLC is now persistent; per-play args are RC commands. This method is unused.
        """

        _ = (file_path, start_sec)
        return self._build_start_args()

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
        # Stop playback via RC, keep VLC running.
        self._current_media_path = None
        self._ended_for_path = None
        self._last_is_playing = False

        if self._proc is not None and self._proc.poll() is not None:
            # VLC died; clear handles.
            self._proc = None
            if self._rc is not None:
                try:
                    self._rc.close()
                except Exception:
                    pass
                self._rc = None
            return

        if self._rc is None:
            return

        self._rc_send("stop")

    def close(self) -> None:
        # Attempt graceful quit over RC, then ensure process is terminated.
        try:
            if self._rc is not None:
                self._rc_send("quit")
        finally:
            if self._rc is not None:
                try:
                    self._rc.close()
                except Exception:
                    pass
                self._rc = None

            p = self._proc
            self._proc = None

            if p is not None:
                try:
                    p.wait(timeout=2.0)
                except Exception:
                    try:
                        p.terminate()
                        p.wait(timeout=1.0)
                    except Exception:
                        try:
                            p.kill()
                        except Exception:
                            pass

            # Close Windows stderr log file if open.
            if self._stderr_log_file:
                try:
                    self._stderr_log_file.close()
                except Exception:
                    pass
                self._stderr_log_file = None

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

        # Ensure VLC is running.
        self.start()

        p = Path(file_path)
        if not p.exists():
            if self.debug:
                print(f"[debug] vlc: file does not exist; skipping: {file_path}")
            return

        # If VLC died between start() and here, restart.
        if self._proc is None or self._proc.poll() is not None:
            self._proc = None
            if self._rc is not None:
                try:
                    self._rc.close()
                except Exception:
                    pass
                self._rc = None
            self.start()

        if self._rc is None:
            # start() should have connected, but keep best-effort behavior.
            if self.debug:
                print("[debug] vlc: no RC connection; cannot play")
            return

        def _issue_play_commands() -> bool:
            # Channel switch via RC.
            if not self._rc_send("clear"):
                return False

            mrl = self._path_to_mrl(p)
            if not self._rc_send(f"add {self._quote_rc_arg(mrl)}"):
                return False

            # Retry seek: VLC may not be ready immediately after add.
            start_i_local = int(max(0.0, float(start_sec)))
            for _attempt in range(30):
                # RC seek itself doesn't ack success reliably; verify via get_time.
                if not self._rc_send(f"seek {start_i_local}"):
                    return False
                tcur = self._rc_query_time_sec()
                if tcur is not None and abs(int(tcur) - start_i_local) <= 2:
                    return True
                time.sleep(0.05)
            # If seek never succeeds, treat as success (playback still switches).
            if self.debug:
                print(
                    f"[debug] vlc: seek failed after retries; continuing from 0s. start_sec={start_i_local}"
                )
            return True

        ok = _issue_play_commands()

        # If RC failed (or VLC died), restart once and retry.
        if not ok:
            if self.debug:
                print("[debug] vlc: play: rc command sequence failed; restarting VLC and retrying")
            try:
                self.close()
            except Exception:
                pass
            self.start()
            if self._rc is None:
                return
            _issue_play_commands()

        # Update our requested media path.
        self._current_media_path = str(p)
        self._ended_for_path = None
        self._last_is_playing = False

        # Guard window after load+seek to avoid transient end triggers.
        self.set_playback_guard(seconds=0.75, reason="LOAD_SEEK")

    def poll_end_of_episode(
        self, *, end_epsilon_sec: float = 0.25
    ) -> tuple[str, float | None, float | None] | None:
        """Return a trigger tuple once when VLC finishes playback for current media.

        Since VLC is persistent (does not exit per file), we detect end by
        watching VLC's `is_playing` state via RC.

        Return shape matches MpvPlayer: (reason, time_pos, duration).
        """

        _ = end_epsilon_sec  # unused (VLC schedule is authoritative)

        # If VLC died, treat it as an EOF-like trigger once so app can correct.
        if self._proc is not None and self._proc.poll() is not None:
            # Mark ended for current path to avoid spamming.
            if self._current_media_path is not None and self._ended_for_path != self._current_media_path:
                self._ended_for_path = self._current_media_path
                return ("EOF", None, None)
            return None

        if self.playback_guard_active():
            # Keep edges updated but suppress triggers.
            st = self._rc_query_is_playing()
            if st is not None:
                self._last_is_playing = bool(st)
            return None

        if self._current_media_path is None:
            st = self._rc_query_is_playing()
            if st is not None:
                self._last_is_playing = bool(st)
            return None

        # If already ended for this path, do not re-trigger.
        if self._ended_for_path == self._current_media_path:
            st = self._rc_query_is_playing()
            if st is not None:
                self._last_is_playing = bool(st)
            return None

        st = self._rc_query_is_playing()
        if st is None:
            return None

        playing = bool(st)
        # Falling edge from playing->not playing.
        ended_edge = (not playing) and bool(self._last_is_playing)
        self._last_is_playing = playing

        if ended_edge:
            self._ended_for_path = self._current_media_path
            return ("EOF", None, None)

        return None
