makeitso

## Profiles / cross-platform config

LCARSTV supports selecting a runtime **profile** so you can run the same codebase on
Windows or Raspberry Pi/Linux without editing config files in-place.

Config resolution order:
1) `--settings` / `--channels` (explicit path overrides)
2) `config/settings.<profile>.json` / `config/channels.<profile>.json` (if present)
3) `config/settings.json` / `config/channels.json` (fallback)

Smoke tests:
- Windows:
  - `python -m lcarstv --profile windows --dry-run`
- Pi/Linux:
  - `python3 -m lcarstv --profile pi --dry-run`

## mpv fullscreen + IPC notes

LCARSTV launches **mpv in fullscreen** via command-line flags (no window-manager shortcuts):

- `--fullscreen`
- `--no-border`

IPC transport used for control:

- **Windows**: Named pipe `\\.\pipe\lcarstv-mpv`
- **Linux / Raspberry Pi**: Unix socket `/tmp/lcarstv-mpv.sock`

If mpv or the app crashes on Linux, a stale `/tmp/lcarstv-mpv.sock` can prevent the next start;
LCARSTV will best-effort delete it before launching mpv.
