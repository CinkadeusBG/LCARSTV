from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
import threading
from typing import Any, BinaryIO


class MpvIpcError(RuntimeError):
    pass


@dataclass
class MpvIpcClient:
    """Minimal mpv JSON IPC client.

    Transport:
    - Windows: named pipe path like: \\.\pipe\lcarstv-mpv
    - Linux/Pi: Unix domain socket path like: /tmp/lcarstv-mpv.sock

    Synchronous request/response. No threads.
    """

    pipe_path: str
    debug: bool = False
    trace: bool = False

    _fh: BinaryIO | None = None
    _sock: socket.socket | None = None
    _next_request_id: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def connect(self, *, timeout_sec: float = 2.0) -> None:
        deadline = time.time() + timeout_sec
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                # mpv expects line-delimited JSON.
                #
                # Windows named pipes can be opened like files.
                if os.name == "nt":
                    self._fh = open(self.pipe_path, "r+b", buffering=0)
                    self._sock = None
                    return

                # On Linux, mpv's --input-ipc-server creates a Unix domain socket.
                # Use an actual socket so reads/writes behave predictably.
                s: socket.socket | None = None
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(0.25)
                    s.connect(self.pipe_path)
                    # Switch back to blocking mode for our manual read loop.
                    s.settimeout(None)
                    self._sock = s
                    self._fh = None
                    return
                except OSError:
                    # Ensure we don't leak sockets while retrying.
                    if s is not None:
                        try:
                            s.close()
                        except Exception:
                            pass
                    raise
            except OSError as e:
                # Ensure we don't hold onto a stale transport between retries.
                self._sock = None
                self._fh = None
                last_err = e
                time.sleep(0.05)
        
        # Provide specific guidance for permission errors
        if last_err and getattr(last_err, 'errno', None) == 13:  # Permission denied
            raise MpvIpcError(
                f"Failed to connect to mpv IPC pipe {self.pipe_path!r}: Permission denied.\n"
                f"This usually means a stale socket file exists from a previous run.\n"
                f"Try manually removing it: rm {self.pipe_path}"
            )
        
        raise MpvIpcError(f"Failed to connect to mpv IPC pipe {self.pipe_path!r}: {last_err}")

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _require_transport(self) -> tuple[BinaryIO | None, socket.socket | None]:
        if self._fh is None and self._sock is None:
            raise MpvIpcError("Not connected")
        return self._fh, self._sock

    def _require_fh(self) -> BinaryIO:
        if self._fh is None:
            raise MpvIpcError("Not connected")
        return self._fh

    def _write(self, raw: bytes) -> None:
        fh, sock = self._require_transport()
        try:
            if fh is not None:
                fh.write(raw)
                return
            assert sock is not None
            sock.sendall(raw)
        except OSError as e:
            raise MpvIpcError(f"Failed to write to mpv IPC transport: {e}")

    def _read_byte(self) -> bytes:
        fh, sock = self._require_transport()
        try:
            if fh is not None:
                return fh.read(1)
            assert sock is not None
            return sock.recv(1)
        except OSError as e:
            raise MpvIpcError(f"Failed to read from mpv IPC transport: {e}")

    def command(self, *cmd: Any, timeout_sec: float = 2.0) -> dict[str, Any]:
        """Send an mpv IPC command and wait for the matching response."""

        # Thread-safe: some callers may schedule time-based follow-ups (e.g., clearing an
        # overlay) without impacting the main playback loop.
        with self._lock:
            return self._command_locked(*cmd, timeout_sec=timeout_sec)

    def trace_command(self, *cmd: Any, timeout_sec: float = 2.0) -> dict[str, Any]:
        """Send an mpv command with request/response tracing regardless of `trace`.

        Use this for high-signal operations (loadfile/seek/quit) or debug-only state
        transitions. Property polling should use `command()` so it stays quiet.
        """

        with self._lock:
            return self._command_locked(*cmd, timeout_sec=timeout_sec, force_trace=True)

    def _command_locked(
        self, *cmd: Any, timeout_sec: float = 2.0, force_trace: bool = False
    ) -> dict[str, Any]:
        """Implementation for `command()`. Call only while holding `_lock`."""

        req_id = self._next_request_id
        self._next_request_id += 1

        payload: dict[str, Any] = {"command": list(cmd), "request_id": req_id}
        raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

        do_trace = bool(self.trace or force_trace)
        if self.debug and do_trace:
            print(f"[debug] mpv >>> {payload}")

        self._write(raw)

        # Read lines until we see the response with matching request_id.
        # mpv may also send async 'event' messages; ignore them.
        deadline = time.time() + timeout_sec
        buf = bytearray()
        while time.time() < deadline:
            b = self._read_byte()

            if not b:
                time.sleep(0.01)
                continue

            if b == b"\n":
                line = bytes(buf).strip()
                buf.clear()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                if "request_id" in msg and msg.get("request_id") == req_id:
                    if self.debug and do_trace:
                        print(f"[debug] mpv <<< {msg}")
                    return msg

                # ignore event or other command responses
                continue

            buf += b

        raise MpvIpcError(f"Timed out waiting for mpv IPC response for request_id={req_id}")
