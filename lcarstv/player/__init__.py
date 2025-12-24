"""Player adapters.

Windows playback uses mpv controlled via JSON IPC.
"""

from .mpv_player import MpvPlayer

__all__ = ["MpvPlayer"]
