from __future__ import annotations

import json
import os
import socket
import time
from collections import deque
from dataclasses import dataclass, field
import threading
from typing import Any, BinaryIO


class MpvIpcError(RuntimeError):
    pass


@dataclass
class MpvIpcClient:
    r"""Minimal mpv JSON IPC client.

    Transport:
    - Windows: named pipe path like: \\.\pipe\lcarstv-mpv
    - Linux/Pi: Unix domain socket path like: /tmp/lcarstv-mpv.sock

    Synchronous request/response. No threads.
    
    Performance optimizations:
    - Buffered reading (batch reads instead of byte-by-byte)
    - Async event ring buffer (prevents unbounded accumulation)
    - Socket draining before commands (clears stale events)
    """

    pipe_path: str
    debug: bool = False
    trace: bool = False

    _fh: BinaryIO | None = None
    _sock: socket.socket | None = None
    _next_request_id: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    
    # Buffered reading state
    _read_buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    
    # Async event buffer (ring buffer with max size)
    _async_events: deque = field(default_factory=lambda: deque(maxlen=100), init=False, repr=False)
    
    # IPC performance monitoring
    _ipc_call_times: deque = field(default_factory=lambda: deque(maxlen=20), init=False, repr=False)

    def connect(self, *, timeout_sec: float = 2.0) -> None:
        deadline = time.time() + timeout_sec
        last_err: Exception | None = None
        retry_count = 0
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
                retry_count += 1
                
                # Use exponential backoff for retries to handle transient errors.
                # Permission denied (errno 13) and Connection refused (errno 111) are common
                # when mpv hasn't fully initialized the socket yet.
                if retry_count <= 3:
                    # Fast retries for the first few attempts
                    time.sleep(0.05)
                elif retry_count <= 10:
                    # Medium backoff
                    time.sleep(0.1)
                else:
                    # Longer backoff for persistent issues
                    time.sleep(0.2)
        
        # Provide a helpful error message with troubleshooting hints
        err_msg = (
            f"Failed to connect to mpv IPC pipe '{self.pipe_path}' after {timeout_sec}s ({retry_count} attempts). "
            f"Last error: {last_err}. "
        )
        if os.name != "nt":
            err_msg += (
                "Hint: On Linux/Pi, mpv may not have created the socket yet or it may have incorrect permissions. "
                "Ensure mpv is running with --input-ipc-server."
            )
        raise MpvIpcError(err_msg)

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

    def _read_chunk(self, max_bytes: int = 4096) -> bytes:
        """Read up to max_bytes from the transport.
        
        This replaces the old byte-by-byte reading with buffered reads,
        significantly reducing syscall overhead.
        
        Note: On Windows (file handles), this is a blocking read.
        On Unix (sockets), this uses non-blocking mode.
        """
        fh, sock = self._require_transport()
        try:
            if fh is not None:
                # Windows named pipe (blocking read)
                # Only read what's available; don't force a full buffer read
                return fh.read(max_bytes)
            assert sock is not None
            # Unix socket: use non-blocking recv to get available data
            sock.setblocking(False)
            try:
                data = sock.recv(max_bytes)
                return data
            finally:
                sock.setblocking(True)
        except BlockingIOError:
            # No data available (non-blocking mode, Unix only)
            return b""
        except OSError as e:
            raise MpvIpcError(f"Failed to read from mpv IPC transport: {e}")
    
    def _drain_socket_buffer(self, max_messages: int = 50) -> None:
        """Drain pending data from the socket buffer and store async events.
        
        This prevents stale event accumulation between commands.
        Called before each IPC command.
        
        Note: Only applies to Unix sockets. Windows named pipes don't need draining
        since they don't accumulate async events the same way.
        """
        # Skip draining for Windows file handles (named pipes)
        if self._sock is None:
            return
        
        messages_processed = 0
        while messages_processed < max_messages:
            # Try to read available data
            chunk = self._read_chunk(max_bytes=4096)
            if not chunk:
                break
            
            self._read_buffer.extend(chunk)
            
            # Process complete lines (newline-delimited JSON)
            while True:
                try:
                    newline_idx = self._read_buffer.index(b'\n')
                except ValueError:
                    # No complete line yet
                    break
                
                line = bytes(self._read_buffer[:newline_idx]).strip()
                del self._read_buffer[:newline_idx + 1]
                
                if not line:
                    continue
                
                try:
                    msg = json.loads(line.decode("utf-8"))
                    # Store async events for potential future use
                    if "request_id" not in msg and "event" in msg:
                        self._async_events.append(msg)
                    messages_processed += 1
                except json.JSONDecodeError:
                    continue
            
            # If we've read a lot of data but still have incomplete lines, 
            # prevent unbounded buffer growth
            if len(self._read_buffer) > 16384:  # 16KB limit
                if self.debug:
                    print(f"[debug] mpv: clearing oversized read buffer ({len(self._read_buffer)} bytes)")
                self._read_buffer.clear()
                break

    def command(self, *cmd: Any, timeout_sec: float = 2.0) -> dict[str, Any]:
        """Send an mpv IPC command and wait for the matching response."""

        # Thread-safe: some callers may schedule time-based follow-ups (e.g., clearing an
        # overlay) without impacting the main playback loop.
        with self._lock:
            # Drain socket buffer before issuing command to prevent stale event buildup
            # (Unix sockets only; Windows named pipes are skipped automatically)
            self._drain_socket_buffer(max_messages=50)
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

        start_time = time.time()
        
        req_id = self._next_request_id
        self._next_request_id += 1

        payload: dict[str, Any] = {"command": list(cmd), "request_id": req_id}
        raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

        do_trace = bool(self.trace or force_trace)
        if self.debug and do_trace:
            print(f"[debug] mpv >>> {payload}")

        self._write(raw)

        # Read and process messages until we find our response.
        # Uses buffered reading instead of byte-by-byte for performance.
        deadline = time.time() + timeout_sec
        
        while time.time() < deadline:
            # Try to read a chunk of data
            chunk = self._read_chunk(max_bytes=4096)
            if chunk:
                self._read_buffer.extend(chunk)
            
            # Process all complete lines in the buffer
            while True:
                try:
                    newline_idx = self._read_buffer.index(b'\n')
                except ValueError:
                    # No complete line yet, need to read more
                    break
                
                line = bytes(self._read_buffer[:newline_idx]).strip()
                del self._read_buffer[:newline_idx + 1]
                
                if not line:
                    continue
                
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                if "request_id" in msg and msg.get("request_id") == req_id:
                    elapsed = time.time() - start_time
                    
                    # Track IPC performance
                    self._ipc_call_times.append(elapsed)
                    
                    # Log slow IPC calls
                    if self.debug and elapsed > 0.1:
                        avg_time = sum(self._ipc_call_times) / len(self._ipc_call_times) if self._ipc_call_times else 0
                        print(f"[debug] mpv: slow IPC call ({elapsed*1000:.1f}ms, avg={avg_time*1000:.1f}ms): {cmd[0] if cmd else 'unknown'}")
                    
                    if self.debug and do_trace:
                        print(f"[debug] mpv <<< {msg}")
                    return msg

                # Store async events for potential future use
                if "event" in msg:
                    self._async_events.append(msg)
                # Ignore responses for other request_ids

            # Small sleep to avoid tight loop when no data available
            if not chunk:
                time.sleep(0.01)

        raise MpvIpcError(f"Timed out waiting for mpv IPC response for request_id={req_id}")
