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

## Raspberry Pi GPIO buttons (optional)

LCARSTV can read **physical GPIO buttons** to change channels.

Rules:
- GPIO is **never** initialized on Windows.
- On Pi/Linux, GPIO is only initialized when `gpio_enable: true` in settings.

Wiring:
- Use **BCM pin numbering** (not physical header numbers).
- Default wiring expects **internal pull-ups** (`gpio_pull_up: true`):
  - Wire each button between the GPIO pin and **GND**.

Example `config/settings.pi.json` snippet:

```json
{
  "gpio_enable": true,
  "gpio_btn_up": 17,
  "gpio_btn_down": 27,
  "gpio_btn_quit": 22,
  "gpio_pull_up": true,
  "gpio_bounce_sec": 0.05
}
```

Notes:
- Debounce is handled via the GPIO library (`bounce_time` / `bouncetime`) plus a small repeat guard.
- Backend preference: `gpiozero` (preferred) then `RPi.GPIO`.

## mpv fullscreen + IPC notes

LCARSTV launches **mpv in fullscreen** via command-line flags (no window-manager shortcuts):

- `--fullscreen`
- `--no-border`

IPC transport used for control:

- **Windows**: Named pipe `\\.\pipe\lcarstv-mpv`
- **Linux / Raspberry Pi**: Unix socket `/tmp/lcarstv-mpv.sock`

If mpv or the app crashes on Linux, a stale `/tmp/lcarstv-mpv.sock` can prevent the next start;
LCARSTV will best-effort delete it before launching mpv.
