from __future__ import annotations

import os
from dataclasses import dataclass

from .keys import InputEvent


@dataclass
class KeyboardInput:
    """Windows keyboard input provider.

    Uses msvcrt for non-blocking polling (no threads).
    - PageUp: channel up
    - PageDown: channel down
    - Q: quit
    """

    def __post_init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("KeyboardInput is Windows-only; use GPIO adapter on Pi")
        import msvcrt  # noqa: F401

    def poll(self) -> InputEvent | None:
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

