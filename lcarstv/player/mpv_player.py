from __future__ import annotations

import os
import time
from pathlib import Path
import subprocess
from dataclasses import dataclass
import threading

from .mpv_ipc import MpvIpcClient, MpvIpcError


@dataclass
class MpvPlayer:
    """Starts and reuses a single mpv process, controlling it via JSON IPC."""

    debug: bool = False
    pipe_name: str = "lcarstv-mpv"
    mpv_exe: str = "mpv"

    static_burst_path: str | None = None
    static_burst_duration_sec: float = 0.4

    _proc: subprocess.Popen[str] | None = None
    _ipc: MpvIpcClient | None = None

    # Used for debug-only "cleared" logging without interfering with playback.
    _osd_token: int = 0

    # mpv OSD overlay id reserved for the call-sign.
    _call_sign_overlay_id: int = 4242

    def _clear_call_sign_osd(self) -> None:
        if self._ipc is None:
            return
        # Remove overlay.
        self._ipc.command("osd-overlay", self._call_sign_overlay_id, "none", "", timeout_sec=2.0)

    def show_call_sign_osd(self, call_sign: str, *, duration_sec: float = 1.5) -> None:
        """Show the channel call sign in mpv's OSD using ASS formatting.

        - Bright green, monospaced, upper-right.
        - No fades/animations.
        - Auto-clears after duration via mpv.

        This must not interfere with playback/seek; it's a single IPC command.
        """

        if self._ipc is None:
            return

        text = str(call_sign).strip().upper()
        if not text:
            return

        duration_sec = max(0.0, float(duration_sec))
        duration_ms = int(duration_sec * 1000)

        if self.debug:
            print(f"[debug] osd: show call-sign {text} ({duration_ms}ms)")

        # Use `osd-overlay` with `format=ass-events` so mpv interprets ASS tags.
        # `show-text` in some builds displays raw strings and does not parse ASS.
        #
        # We render a single ASS "Dialogue" line. Timing fields are not used.
        # Alignment/placement/appearance are set via override tags.
        #  - \an9: top-right alignment
        #  - \fn: monospace font
        #  - \fs: font size
        #  - \1c: primary color (BGR in &HBBGGRR&), bright green => 00FF00
        #  - \bord/\3c: outline for legibility
        #  - \shad0: no shadow
        # For format=ass-events, mpv expects the *Text* portion for each ASS event,
        # one per line. (Do NOT include the full "Dialogue: ..." header.)
        # Font size target: ~75% larger than initial (28 -> 49).
        ass_text = rf"{{\an9\fnConsolas\fs49\1c&H00FF00&\bord2\3c&H000000&\shad0}}{text}"

        # Replace the overlay each time (same id).
        self._ipc.command("osd-overlay", self._call_sign_overlay_id, "ass-events", ass_text, timeout_sec=2.0)

        # Schedule overlay removal after duration.
        self._osd_token += 1
        token = self._osd_token

        def _clear_and_log() -> None:
            # Only clear/log if no newer overlay was requested.
            if token != self._osd_token:
                return
            try:
                self._clear_call_sign_osd()
            except Exception:
                # Best-effort.
                pass
            if self.debug:
                print(f"[debug] osd: cleared call-sign {text}")

        if duration_sec <= 0:
            _clear_and_log()
        else:
            t = threading.Timer(duration_sec, _clear_and_log)
            t.daemon = True
            t.start()

    def _wait_for_media_ready(self, *, timeout_sec: float = 2.0, poll_interval_sec: float = 0.05) -> bool:
        """Wait until mpv has loaded enough metadata that seeking should work.

        On Windows (and especially on dev builds), mpv can acknowledge `loadfile`
        before the demuxer has populated properties like `duration`. If we call
        `seek` immediately, mpv may respond with `error running command`.

        We use `duration` as a simple proxy for "media is ready".
        """

        if self._ipc is None:
            return False

        deadline = time.time() + float(timeout_sec)
        while time.time() < deadline:
            try:
                resp = self._ipc.command("get_property", "duration", timeout_sec=2.0)
            except Exception:
                # IPC glitches: treat as not ready yet.
                resp = {"error": "exception"}

            if resp.get("error") in (None, "success"):
                # duration could be None for streams; but for local files we expect a number.
                if resp.get("data") is not None:
                    return True

            time.sleep(max(0.0, float(poll_interval_sec)))

        return False

    def _best_effort_seek(self, start_sec: float, *, retries: int = 10, delay_sec: float = 0.05) -> bool:
        """Try to seek; retry briefly; never raise.

        Returns True if the seek succeeded, False otherwise.
        """

        if self._ipc is None:
            return False

        # Clamp to >= 0
        start_sec = max(0.0, float(start_sec))

        # Optional clamp to duration (helps if channel position exceeds file length)
        try:
            dur_resp = self._ipc.command("get_property", "duration", timeout_sec=2.0)
            if dur_resp.get("error") in (None, "success") and isinstance(dur_resp.get("data"), (int, float)):
                duration = float(dur_resp["data"])
                if duration > 0:
                    start_sec = min(start_sec, max(0.0, duration - 0.25))
        except Exception:
            pass

        for attempt in range(max(1, int(retries))):
            resp = self._ipc.command("seek", start_sec, "absolute", "exact", timeout_sec=10.0)
            if resp.get("error") in (None, "success"):
                return True

            # mpv is sometimes not ready immediately after loadfile
            time.sleep(max(0.0, float(delay_sec)))

        if self.debug:
            print(f"[debug] mpv: seek failed after retries; continuing from 0s. start_sec={start_sec}")
        return False

    @property
    def pipe_path(self) -> str:
        if os.name == "nt":
            return rf"\\.\pipe\{self.pipe_name}"
        # Not used yet; Pi/Linux will be added later.
        return f"/tmp/{self.pipe_name}"

    def start(self) -> None:
        if self._proc is not None:
            return

        if os.name != "nt":
            raise RuntimeError("Windows mpv IPC integration is implemented first; non-Windows not yet supported")

        # Start mpv idle and controllable via IPC.
        # Keep this minimal; no overlays/shaders/UI extras.
        args = [
            self.mpv_exe,
            "--idle=yes",
            "--force-window=yes",
            "--no-terminal",
            f"--input-ipc-server={self.pipe_path}",
            "--audio-display=no",
            "--keep-open=no",
            "--volume=100",
        ]

        # Detach from console output; we log IPC ourselves when debug enabled.
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        self._ipc = MpvIpcClient(pipe_path=self.pipe_path, debug=self.debug)
        self._ipc.connect(timeout_sec=3.0)

    def play(self, file_path: str, start_sec: float, *, call_sign: str | None = None) -> None:
        """Load a file and start at the specified live offset."""
        if self._proc is None or self._ipc is None:
            self.start()

        # In early dev, channels may point at placeholder paths that don't exist.
        # Keep mpv alive/idling rather than crashing the whole app.
        p = Path(file_path)
        if not p.exists():
            if self.debug:
                print(f"[debug] mpv: file does not exist, idling instead: {file_path}")
            self.stop()
            return

        # Safer than relying on loadfile's "start=" option (varies by mpv build).
        resp = self._ipc.command("loadfile", file_path, "replace", timeout_sec=10.0)
        if resp.get("error") not in (None, "success"):
            raise MpvIpcError(f"mpv loadfile failed: {resp}")

        # Wait briefly for the file to become seekable, then seek best-effort.
        # If we can't seek, keep running and just play from 0.
        self._wait_for_media_ready(timeout_sec=2.0, poll_interval_sec=0.05)
        self._best_effort_seek(start_sec, retries=10, delay_sec=0.05)

        # Overlay must appear after the static burst (if any) and when real content begins.
        if call_sign is not None:
            try:
                self.show_call_sign_osd(call_sign)
            except Exception:
                # Best-effort: never break playback for an overlay.
                if self.debug:
                    print(f"[debug] osd: failed to show call-sign {call_sign!r}")

    def play_with_static_burst(self, file_path: str, start_sec: float, *, call_sign: str | None = None) -> None:
        """Play a short static burst before switching to the tuned channel.

        Sequence:
        - loadfile(static)
        - wait briefly
        - loadfile(channel)
        - seek to live offset
        """

        if self.static_burst_path:
            static_path = Path(self.static_burst_path)
            if static_path.exists():
                if self.debug:
                    print(f"[debug] static burst start: {self.static_burst_path}")

                # Use loadfile directly here, then wait.
                if self._proc is None or self._ipc is None:
                    self.start()
                self._ipc.command("loadfile", str(static_path), "replace", timeout_sec=10.0)
                time.sleep(max(0.0, float(self.static_burst_duration_sec)))

                if self.debug:
                    print("[debug] static burst end")
            else:
                if self.debug:
                    print(f"[debug] static burst missing, skipping: {self.static_burst_path}")

        self.play(file_path, start_sec, call_sign=call_sign)

    def stop(self) -> None:
        # Keep minimal: stop playback and return to idle.
        if self._ipc is None:
            return
        try:
            self._ipc.command("stop")
        except Exception:
            # best-effort
            pass

    def close(self) -> None:
        # Best-effort shutdown. We don't want threads or complex supervision.
        try:
            if self._ipc is not None:
                try:
                    self._ipc.command("quit", timeout_sec=1.0)
                except Exception:
                    pass
        finally:
            if self._ipc is not None:
                self._ipc.close()
                self._ipc = None

            if self._proc is not None:
                try:
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                finally:
                    self._proc = None
