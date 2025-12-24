from __future__ import annotations

import os
from pathlib import Path
import subprocess
from dataclasses import dataclass

from .mpv_ipc import MpvIpcClient, MpvIpcError


@dataclass
class MpvPlayer:
    """Starts and reuses a single mpv process, controlling it via JSON IPC."""

    debug: bool = False
    pipe_name: str = "lcarstv-mpv"
    mpv_exe: str = "mpv"

    _proc: subprocess.Popen[str] | None = None
    _ipc: MpvIpcClient | None = None

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

    def play(self, file_path: str, start_sec: float) -> None:
        """Load a file and start at the specified live offset."""
        if self._proc is None or self._ipc is None:
            self.start()

        start_sec = max(0.0, float(start_sec))

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

        resp = self._ipc.command("seek", start_sec, "absolute", "exact", timeout_sec=10.0)
        if resp.get("error") not in (None, "success"):
            raise MpvIpcError(f"mpv seek failed: {resp}")

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
