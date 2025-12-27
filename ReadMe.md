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

## VLC backend (recommended for Raspberry Pi composite output)

If mpv output is unreliable on composite/SDL/DRM setups, LCARSTV supports using VLC
as a playback backend.

### Install VLC (Raspberry Pi OS / Debian)

```bash
sudo apt update
sudo apt install vlc
```

### Windows notes

1) Install VLC from: https://www.videolan.org/
2) Set `player_backend` to VLC in `config/settings.windows.json`:

```json
{
  "player_backend": "vlc"
}
```

3) Run:

```bash
python -m lcarstv --profile windows
```

Executable discovery order on Windows:
- `cvlc` on PATH
- `vlc` on PATH
- `C:\\Program Files\\VideoLAN\\VLC\\vlc.exe`
- `C:\\Program Files (x86)\\VideoLAN\\VLC\\vlc.exe`

#### Windows VLC window positioning troubleshooting

VLC persists window positions in `%APPDATA%\vlc`. If the video window appears off-screen
or is invisible:

1. Close LCARSTV
2. Delete the folder: `%APPDATA%\vlc`
3. Restart LCARSTV

This resets VLC's window placement to defaults.

**Note:** LCARSTV will log a reminder about this on startup if the VLC config folder is detected.

**VLC stderr output:** On Windows (non-debug mode), VLC stderr is logged to: `%TEMP%\lcarstv-vlc-stderr.log`

### Composite SDL test command

VLC is launched with `--vout=sdl` by default. You may need SDL framebuffer env vars
depending on your image / runtime (tty1 vs service).

Recommended quick test:

```bash
SDL_VIDEODRIVER=fbcon SDL_FBDEV=/dev/fb0 \
  cvlc --fullscreen --no-video-title-show --no-osd --avcodec-hw=none --vout=sdl \
  /srv/media/artifacts/static.mp4
```

Note:
- LCARSTV does **not** hardcode `/dev/fb0` in code. Only set `SDL_FBDEV` if your
  environment needs it.

### Enable VLC backend in settings

Add this to your settings JSON (e.g. `config/settings.pi.json`):

```json
{
  "player_backend": "vlc"
}
```

### How to test

1) Run playback with the Pi profile:

```bash
python3 -m lcarstv --profile pi
```

2) Confirm:
- Channel up/down restarts playback at the correct live offset.
- Rapid channel changes do not leave orphaned `vlc`/`cvlc` processes.
- Works headless from tty1.
- Works under systemd (stdout/stderr inherit to journald).

Example systemd environment (optional; only if your SDL setup needs it):

```ini
[Service]
Environment=SDL_VIDEODRIVER=fbcon
Environment=SDL_FBDEV=/dev/fb0
```

## mpv fullscreen + IPC notes

LCARSTV launches **mpv in fullscreen** via command-line flags (no window-manager shortcuts):

- `--fullscreen`
- `--no-border`

IPC transport used for control:

- **Windows**: Named pipe `\\.\pipe\lcarstv-mpv`
- **Linux / Raspberry Pi**: Unix socket `/tmp/lcarstv-mpv.sock`

If mpv or the app crashes on Linux, a stale `/tmp/lcarstv-mpv.sock` can prevent the next start;
LCARSTV will best-effort delete it before launching mpv.
