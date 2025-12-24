from __future__ import annotations

from dataclasses import dataclass

from .keys import InputEvent


@dataclass
class GPIOInputStub:
    """Placeholder for Raspberry Pi GPIO input.

    Intentionally does nothing in this initial skeleton.
    """

    def poll(self) -> InputEvent | None:
        return None

