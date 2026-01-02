from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from .keys import InputEvent


@dataclass
class KeyboardInput:
    """Cross-platform keyboard input provider.

    - Windows: uses msvcrt for non-blocking polling (no threads).
    - Linux/posix: reads from stdin in cbreak mode and parses common escape sequences.

    Mappings:
    - PageUp: channel up
    - PageDown: channel down
    - Up arrow: channel up
    - Down arrow: channel down
    - Q: quit
    """

    _posix_fd: int | None = None
    _posix_old_termios: list[int] | None = None
    _posix_buf: bytearray = field(default_factory=bytearray)
    _posix_buf_max_size: int = 128  # Prevent unbounded growth

    def __post_init__(self) -> None:
        if os.name == "nt":
            import msvcrt  # noqa: F401
            return

        # POSIX (Linux / Pi): set terminal to cbreak for immediate keypress reads.
        if not sys.stdin.isatty():
            # Non-tty (e.g. piped input). Leave disabled.
            return

        import termios
        import tty

        fd = sys.stdin.fileno()
        self._posix_fd = fd
        self._posix_old_termios = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    def close(self) -> None:
        """Restore terminal settings (posix). Safe to call multiple times."""

        if os.name == "nt":
            return
        if self._posix_fd is None or self._posix_old_termios is None:
            return

        import termios

        try:
            termios.tcsetattr(self._posix_fd, termios.TCSADRAIN, self._posix_old_termios)
        finally:
            self._posix_fd = None
            self._posix_old_termios = None

    def poll(self) -> InputEvent | None:
        if os.name == "nt":
            return self._poll_windows()
        return self._poll_posix()

    def _poll_windows(self) -> InputEvent | None:
        import msvcrt

        if not msvcrt.kbhit():
            return None

        ch = msvcrt.getwch()

        # Handle special keys: msvcrt returns '\x00' or '\xe0', then a second code.
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            # PageUp=73, PageDown=81 in Windows console
            if code == "I" or ord(code) == 73:
                return InputEvent(kind="channel_up")
            if code == "Q" or ord(code) == 81:
                return InputEvent(kind="channel_down")
            return None

        if ch.lower() == "q":
            return InputEvent(kind="quit")
        return None

    def _poll_posix(self) -> InputEvent | None:
        if self._posix_fd is None:
            return None

        import select

        # Non-blocking poll.
        r, _w, _x = select.select([self._posix_fd], [], [], 0)
        if not r:
            return None

        try:
            data = os.read(self._posix_fd, 32)
        except OSError:
            return None
        if not data:
            return None

        self._posix_buf.extend(data)

        # Defensive: prevent unbounded buffer growth from unrecognized sequences.
        # Keep only the most recent bytes if buffer exceeds max size.
        if len(self._posix_buf) > self._posix_buf_max_size:
            self._posix_buf = self._posix_buf[-self._posix_buf_max_size:]

        # Parse buffer for known sequences.
        # Common escape sequences:
        # - Up:    ESC [ A
        # - Down:  ESC [ B
        # - PgUp:  ESC [ 5 ~
        # - PgDn:  ESC [ 6 ~
        while self._posix_buf:
            b0 = self._posix_buf[0]

            # Normal char.
            if b0 in (ord("q"), ord("Q")):
                del self._posix_buf[0]
                return InputEvent(kind="quit")

            # ESC-sequence.
            if b0 == 0x1B:
                if len(self._posix_buf) < 2:
                    return None
                if self._posix_buf[1] != ord("["):
                    # Unknown; consume ESC and continue.
                    del self._posix_buf[0]
                    continue
                if len(self._posix_buf) < 3:
                    return None

                b2 = self._posix_buf[2]

                # Arrow keys.
                if b2 == ord("A"):
                    del self._posix_buf[:3]
                    return InputEvent(kind="channel_up")
                if b2 == ord("B"):
                    del self._posix_buf[:3]
                    return InputEvent(kind="channel_down")

                # PageUp/PageDown.
                if b2 in (ord("5"), ord("6")):
                    if len(self._posix_buf) < 4:
                        return None
                    if self._posix_buf[3] == ord("~"):
                        del self._posix_buf[:4]
                        return InputEvent(kind="channel_up" if b2 == ord("5") else "channel_down")

                # Unknown CSI; consume ESC and retry.
                del self._posix_buf[0]
                continue

            # Unhandled byte; consume.
            del self._posix_buf[0]

        return None
