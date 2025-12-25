makeitso

## mpv fullscreen + IPC notes

LCARSTV launches **mpv in fullscreen** via command-line flags (no window-manager shortcuts):

- `--fullscreen`
- `--no-border`

IPC transport used for control:

- **Windows**: Named pipe `\\.\pipe\lcarstv-mpv`
- **Linux / Raspberry Pi**: Unix socket `/tmp/lcarstv-mpv.sock`

If mpv or the app crashes on Linux, a stale `/tmp/lcarstv-mpv.sock` can prevent the next start;
LCARSTV will best-effort delete it before launching mpv.
