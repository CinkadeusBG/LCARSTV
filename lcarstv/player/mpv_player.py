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
    ipc_trace: bool = False
    pipe_name: str = "lcarstv-mpv"
    mpv_exe: str = "mpv"

    static_burst_path: str | None = None
    static_burst_duration_sec: float = 0.4

    _proc: subprocess.Popen[str] | None = None
    _ipc: MpvIpcClient | None = None

    # Playback state tracking for edge-triggered EOF detection.
    _current_media_path: str | None = None
    _seen_active_path: bool = False
    _seen_time_pos: bool = False
    _last_eof_reached: bool = False
    _last_idle_active: bool = False
    _last_near_end: bool = False
    _ended_for_path: str | None = None

    # Cached metadata to avoid polling spam.
    _cached_duration_for_path: str | None = None
    _cached_duration_sec: float | None = None
    _cached_duration_last_fetch_time: float = 0.0

    # Used for debug-only "cleared" logging without interfering with playback.
    _osd_token: int = 0

    # mpv OSD overlay id reserved for the call-sign.
    _call_sign_overlay_id: int = 4242

    # Suppress end-triggers during static burst and immediately after load/seek.
    _guard_until: float = 0.0
    _guard_reason: str | None = None

    def set_playback_guard(self, *, seconds: float, reason: str) -> None:
        seconds = max(0.0, float(seconds))
        until = time.time() + seconds
        self._guard_until = max(self._guard_until, until)
        self._guard_reason = str(reason)
        if self.debug:
            ms = int(seconds * 1000)
            print(f"[debug] guard: set until={self._guard_until:.3f} (+{ms}ms) reason={self._guard_reason}")

    def _guard_active(self) -> bool:
        return time.time() < float(self._guard_until)

    def playback_guard_active(self) -> bool:
        """Public read-only guard state for app-level schedule checks."""

        return self._guard_active()

    @property
    def current_media_path(self) -> str | None:
        """Last media path requested via `play()`.

        This is used by the app loop to debounce auto-advance triggers.
        """

        return self._current_media_path

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

        # Font size target: 75% larger than the baseline (28 -> 49).
        base_font_size = 28
        font_scale = 1.75
        font_size = max(1, int(round(base_font_size * font_scale)))

        # Light stroke around the letters (no background box).
        outline_px = 2

        ass_text = (
            rf"{{\an9\fnConsolas\fs{font_size}\1c&H00FF00&\bord{outline_px}\3c&H000000&\shad0}}{text}"
        )

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

    # --- Best-effort property helpers (never raise) ---
    def _get_property(self, name: str, *, timeout_sec: float = 1.0) -> dict:
        if self._ipc is None:
            return {"error": "no-ipc", "data": None}
        try:
            # Property polling should be quiet; IPC trace is for high-signal events.
            return self._ipc.command("get_property", name, timeout_sec=timeout_sec)
        except Exception:
            return {"error": "exception", "data": None}

    def _get_bool_property(self, name: str) -> bool | None:
        resp = self._get_property(name)
        if resp.get("error") in (None, "success"):
            data = resp.get("data")
            if isinstance(data, bool):
                return data
        return None

    def _get_float_property(self, name: str) -> float | None:
        resp = self._get_property(name)
        if resp.get("error") in (None, "success"):
            data = resp.get("data")
            if isinstance(data, (int, float)):
                return float(data)
        return None

    def _get_str_property(self, name: str) -> str | None:
        resp = self._get_property(name)
        if resp.get("error") in (None, "success"):
            data = resp.get("data")
            if isinstance(data, str):
                return data
        return None

    def current_duration_sec(self) -> float | None:
        """Best-effort duration of the currently loaded media in seconds."""

        if self._current_media_path is None:
            return None

        # Cache per file. If duration wasn't available yet, retry occasionally.
        if self._cached_duration_for_path == self._current_media_path:
            if self._cached_duration_sec is not None:
                return self._cached_duration_sec

            # If duration is unknown for this path, retry with a slow backoff.
            # (Prevents spamming IPC while still eventually learning duration.)
            if (time.time() - float(self._cached_duration_last_fetch_time)) < 1.0:
                return None

        dur = self._get_float_property("duration")
        self._cached_duration_last_fetch_time = time.time()
        if dur is not None and dur > 0:
            self._cached_duration_for_path = self._current_media_path
            self._cached_duration_sec = float(dur)
        else:
            # Avoid hammering duration if mpv isn't ready yet; cache negative for this path.
            self._cached_duration_for_path = self._current_media_path
            self._cached_duration_sec = None

        return self._cached_duration_sec

    def poll_end_of_file(self) -> bool:
        """Return True once when mpv reaches EOF for the currently loaded file.

        Edge-triggered and guarded to avoid false positives when mpv is idling
        on startup.

        Preferred signal: `eof-reached` property.
        Fallback: transition to `idle-active` after previously having an active `path`.
        """

        if self._ipc is None:
            return False

        # Minimize property polling:
        # - Once we have confirmed playback actually started (via time-pos or path), we
        #   avoid repeatedly querying those properties every cycle.
        # - We still prefer eof-reached when available, which is a single bool poll.

        # If we haven't yet observed evidence of active playback, try to learn it.
        if not (self._seen_active_path or self._seen_time_pos):
            # `time-pos` is a good signal that playback started.
            tp = self._get_float_property("time-pos")
            if tp is not None:
                self._seen_time_pos = True
            else:
                # Fall back to `path` if time-pos isn't available yet.
                p = self._get_str_property("path")
                if p:
                    self._seen_active_path = True

        # If we do not currently have a media loaded (from our perspective), never
        # treat idle as an EOF event. (Still update edge state below.)
        if self._current_media_path is None:
            eof_reached = self._get_bool_property("eof-reached")
            if eof_reached is not None:
                self._last_eof_reached = bool(eof_reached)
                return False
            idle_active = self._get_bool_property("idle-active")
            self._last_idle_active = bool(idle_active) if idle_active is not None else False
            return False

        # Preferred: eof-reached rising edge.
        eof_reached = self._get_bool_property("eof-reached")
        if eof_reached is not None:
            triggered = bool(eof_reached) and not self._last_eof_reached and (self._seen_active_path or self._seen_time_pos)
            self._last_eof_reached = bool(eof_reached)
            if triggered:
                # Ensure we only trigger once per loaded file.
                if self._current_media_path and self._ended_for_path == self._current_media_path:
                    return False
                self._ended_for_path = self._current_media_path
                return True
            return False

        # Fallback heuristic.
        idle_active = self._get_bool_property("idle-active")
        time_pos = self._get_float_property("time-pos")
        if time_pos is not None:
            self._seen_time_pos = True
        # Only query `path` in fallback mode if we haven't already seen it.
        path = None
        if not self._seen_active_path:
            path = self._get_str_property("path")
            if path:
                self._seen_active_path = True
        idle_active_bool = bool(idle_active) if idle_active is not None else False

        # Rising edge into idle while we previously had a real path loaded.
        triggered = (
            self._seen_active_path
            and self._seen_time_pos
            and idle_active_bool
            and not self._last_idle_active
            and (not path or time_pos is None)
        )

        self._last_idle_active = idle_active_bool

        if triggered:
            if self._current_media_path and self._ended_for_path == self._current_media_path:
                return False
            self._ended_for_path = self._current_media_path
            return True

        return False

    def poll_end_of_episode(
        self, *, end_epsilon_sec: float = 0.25
    ) -> tuple[str, float | None, float | None] | None:
        """Return a trigger tuple once when the currently loaded media ends.

        Triggers (edge-detected; fires once per loaded file):
        - EOF: `eof-reached` rising edge
        - IDLE: `idle-active` rising edge after we previously observed an active `path`
        - NEAR_END: `time-pos >= duration - end_epsilon_sec` (when both props are available)

        Returns:
            (reason, time_pos_sec, duration_sec) where reason is one of
            {"EOF", "IDLE", "NEAR_END"}, or None if no trigger.

        Notes:
        - This method is intended to be polled at a low rate (e.g., 5Hz).
        - Property polling is kept quiet; IPC trace is reserved for high-signal commands.
        """

        if self._ipc is None:
            return None

        # If guarded, suppress triggers, but still update edge state so we don't
        # immediately fire on guard expiry.
        if self._guard_active():
            remaining = float(self._guard_until) - time.time()
            if self.debug:
                print(
                    f"[debug] guard: suppress triggers remaining={max(0.0, remaining):.3f}s reason={self._guard_reason}"
                )
            # Best-effort edge updates.
            eof_reached = self._get_bool_property("eof-reached")
            if eof_reached is not None:
                self._last_eof_reached = bool(eof_reached)
            idle_active = self._get_bool_property("idle-active")
            self._last_idle_active = bool(idle_active) if idle_active is not None else False
            tp = self._get_float_property("time-pos")
            if tp is not None:
                self._seen_time_pos = True
            dur_tmp = self.current_duration_sec()
            near_end_tmp = (
                tp is not None
                and dur_tmp is not None
                and dur_tmp > 0
                and tp >= (dur_tmp - max(0.0, float(end_epsilon_sec)))
            )
            self._last_near_end = bool(near_end_tmp)
            return None

        # Clamp epsilon to a sane non-negative range.
        eps = max(0.0, float(end_epsilon_sec))

        # If we haven't yet observed evidence of active playback, try to learn it.
        # (Needed so idle on startup doesn't look like an end-of-episode.)
        if not (self._seen_active_path or self._seen_time_pos):
            tp0 = self._get_float_property("time-pos")
            if tp0 is not None:
                self._seen_time_pos = True
            else:
                p0 = self._get_str_property("path")
                if p0:
                    self._seen_active_path = True

        # If we do not currently have a media loaded (from our perspective), never
        # treat idle or near-end as an end event. Still update edge state.
        if self._current_media_path is None:
            eof_reached = self._get_bool_property("eof-reached")
            if eof_reached is not None:
                self._last_eof_reached = bool(eof_reached)
            idle_active = self._get_bool_property("idle-active")
            self._last_idle_active = bool(idle_active) if idle_active is not None else False
            self._last_near_end = False
            return None

        # Poll the high-signal bools first.
        eof_reached = self._get_bool_property("eof-reached")
        idle_active = self._get_bool_property("idle-active")

        # Track playback evidence.
        time_pos = self._get_float_property("time-pos")
        if time_pos is not None:
            self._seen_time_pos = True

        # Ensure we have observed a real path at least once for IDLE gating.
        if not self._seen_active_path:
            p = self._get_str_property("path")
            if p:
                self._seen_active_path = True

        # If we've already ended for this file, don't trigger again.
        if self._ended_for_path is not None and self._ended_for_path == self._current_media_path:
            # Still update edges so state stays consistent.
            if eof_reached is not None:
                self._last_eof_reached = bool(eof_reached)
            idle_bool = bool(idle_active) if idle_active is not None else False
            self._last_idle_active = idle_bool
            # Update near-end edge state.
            dur_tmp = self.current_duration_sec()
            near_end_tmp = (
                time_pos is not None
                and dur_tmp is not None
                and dur_tmp > 0
                and time_pos >= (dur_tmp - eps)
            )
            self._last_near_end = bool(near_end_tmp)
            return None

        # --- Trigger A) EOF rising edge ---
        if eof_reached is not None:
            eof_bool = bool(eof_reached)
            # Require real playback evidence (`time-pos`) to avoid stale eof states right after load.
            eof_edge = eof_bool and not self._last_eof_reached and self._seen_time_pos
            self._last_eof_reached = eof_bool
            if eof_edge:
                self._ended_for_path = self._current_media_path
                return ("EOF", time_pos, self.current_duration_sec())

        # --- Trigger B) IDLE rising edge (after known active path) ---
        idle_bool = bool(idle_active) if idle_active is not None else False
        # Require real playback evidence (`time-pos`) and a prior active path.
        idle_edge = idle_bool and not self._last_idle_active and self._seen_active_path and self._seen_time_pos
        self._last_idle_active = idle_bool
        if idle_edge:
            self._ended_for_path = self._current_media_path
            return ("IDLE", time_pos, self.current_duration_sec())

        # --- Trigger C) NEAR_END rising edge ---
        dur = self.current_duration_sec()
        near_end = (
            time_pos is not None
            and dur is not None
            and dur > 0
            and time_pos >= (dur - eps)
        )
        near_end_edge = bool(near_end) and not self._last_near_end and self._seen_time_pos
        self._last_near_end = bool(near_end)
        if near_end_edge:
            self._ended_for_path = self._current_media_path
            return ("NEAR_END", time_pos, dur)

        return None

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
            # High-signal command: allow tracing if enabled.
            cmd = self._ipc.trace_command if self.ipc_trace else self._ipc.command
            resp = cmd("seek", start_sec, "absolute", "exact", timeout_sec=10.0)
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
        # Linux/Pi: mpv expects a Unix domain socket path.
        # Use a stable name so supervision / scripts can locate it if needed.
        return f"/tmp/{self.pipe_name}.sock"

    def _cleanup_stale_ipc_path(self) -> None:
        """Best-effort cleanup of leftover IPC socket on non-Windows.

        If the app or mpv crashes, the socket file may remain, and mpv will
        fail to bind the next time with "address already in use".
        """

        if os.name == "nt":
            return

        try:
            p = Path(self.pipe_path)
            if p.exists():
                p.unlink()
        except Exception:
            # Best-effort; do not prevent playback.
            pass

    def start(self) -> None:
        if self._proc is not None:
            return

        # Non-Windows uses a Unix socket for --input-ipc-server; remove any stale
        # socket path before starting mpv.
        self._cleanup_stale_ipc_path()

        # Start mpv idle and controllable via IPC.
        # Keep this minimal; no overlays/shaders/UI extras.
        args = [
            self.mpv_exe,
            # Force fullscreen on all platforms.
            # Use launch flags (not window manager shortcuts).
            "--fullscreen",
            # Optional but helpful for kiosk/fullscreen setups.
            "--no-border",
            "--idle=yes",
            "--force-window=no",
            "--no-terminal",
            f"--input-ipc-server={self.pipe_path}",
            "--audio-display=no",
            # Disable subtitles globally.
            # - Ensure embedded subtitle tracks (e.g., MKV) never render.
            # - Ensure mpv never auto-loads external subs.
            "--sid=no",
            "--sub-auto=no",
            "--keep-open=no",
            "--volume=100",
        ]

        if self.debug:
            # Print the full command line used to launch mpv.
            # Using list2cmdline preserves quoting rules on Windows.
            print(f"[debug] mpv: launch args: {subprocess.list2cmdline(args)}")

        # Detach from console output; we log IPC ourselves when debug enabled.
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        self._ipc = MpvIpcClient(pipe_path=self.pipe_path, debug=self.debug, trace=self.ipc_trace)
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
        # High-signal command: allow tracing if enabled.
        cmd = self._ipc.trace_command if self.ipc_trace else self._ipc.command
        resp = cmd("loadfile", file_path, "replace", timeout_sec=10.0)
        if resp.get("error") not in (None, "success"):
            raise MpvIpcError(f"mpv loadfile failed: {resp}")

        # Record last loaded media path for EOF edge-detection.
        self._current_media_path = str(file_path)
        self._seen_active_path = False
        self._seen_time_pos = False
        self._last_eof_reached = False
        self._last_idle_active = False
        self._last_near_end = False
        self._ended_for_path = None

        # Reset duration cache for new media.
        self._cached_duration_for_path = None
        self._cached_duration_sec = None
        self._cached_duration_last_fetch_time = 0.0

        # Wait briefly for the file to become seekable, then seek best-effort.
        # If we can't seek, keep running and just play from 0.
        self._wait_for_media_ready(timeout_sec=2.0, poll_interval_sec=0.05)
        self._best_effort_seek(start_sec, retries=10, delay_sec=0.05)

        # Guard window after load+seek to avoid transient end triggers.
        self.set_playback_guard(seconds=0.75, reason="LOAD_SEEK")

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

                # Suppress end triggers during static and immediately after the real tune.
                self.set_playback_guard(seconds=float(self.static_burst_duration_sec) + 0.75, reason="TUNE")

                # Use loadfile directly here, then wait.
                if self._proc is None or self._ipc is None:
                    self.start()
                cmd = self._ipc.trace_command if self.ipc_trace else self._ipc.command
                cmd("loadfile", str(static_path), "replace", timeout_sec=10.0)
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

        # Reset EOF tracking so idle doesn't look like EOF.
        self._current_media_path = None
        self._seen_active_path = False
        self._seen_time_pos = False
        self._last_eof_reached = False
        self._last_idle_active = False
        self._last_near_end = False
        self._ended_for_path = None

    def close(self) -> None:
        # Best-effort shutdown. We don't want threads or complex supervision.
        try:
            if self._ipc is not None:
                try:
                    cmd = self._ipc.trace_command if self.ipc_trace else self._ipc.command
                    cmd("quit", timeout_sec=1.0)
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

        # Defensive: clear any remembered playback state.
        self._current_media_path = None
        self._seen_active_path = False
        self._seen_time_pos = False
        self._last_eof_reached = False
        self._last_idle_active = False
        self._last_near_end = False
        self._ended_for_path = None

    def current_mpv_path(self) -> str | None:
        """Best-effort mpv-reported `path` for suppression/debouncing.

        This can differ briefly from `current_media_path` right after `loadfile`
        while mpv is transitioning.
        """

        return self._get_str_property("path")
