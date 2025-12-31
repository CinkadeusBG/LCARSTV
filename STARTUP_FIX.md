# LCARSTV Raspberry Pi Startup Fix

## Problem
When LCARSTV runs on auto-login via `.bash_profile`, MPV fails to create its IPC socket because the display server (X11/Wayland) and graphics subsystem aren't fully initialized yet.

## Solution

### Code Fix ✅ (Already Applied)
The MPV IPC socket timeout has been increased from 2 seconds to 10 seconds in `lcarstv/player/mpv_player.py` to accommodate slower startup environments.

### .bash_profile Fix (RECOMMENDED - Use This)

The code fix I made (10-second timeout) is actually sufficient! Use this simple, reliable version:

```bash
#!/bin/bash

# LCARSTV Auto-Start Configuration
echo "Preparing to start LCARSTV..."

# Set GPIO pin factory for hardware buttons
export GPIOZERO_PIN_FACTORY=lgpio

# Wait for DISPLAY variable to be set (indicates X11 is initializing)
timeout=30
while [ $timeout -gt 0 ] && [ -z "$DISPLAY" ]; do
    sleep 0.5
    timeout=$((timeout - 1))
done

if [ -n "$DISPLAY" ]; then
    echo "Display server detected: $DISPLAY"
else
    echo "Warning: DISPLAY not set, continuing anyway..."
fi

# Give graphics drivers and audio subsystem time to initialize
echo "Waiting for system to stabilize..."
sleep 5

# Configure audio BEFORE starting LCARSTV
echo "Configuring audio..."
amixer set PCM 100% unmute >/dev/null 2>&1

# Additional stabilization delay
sleep 2

# Start LCARSTV
echo "Starting LCARSTV..."
cd /home/cinkadeus/LCARSTV
python3 -m lcarstv --profile pi

# If LCARSTV exits, ensure audio settings persist
amixer set PCM 100% unmute >/dev/null 2>&1
```

## Alternative: Minimal Fix (Even Simpler)

If you want the absolute simplest solution that just works:

```bash
#!/bin/bash

# Set GPIO pin factory
export GPIOZERO_PIN_FACTORY=lgpio

# Wait for system to be ready (7 seconds total)
sleep 7

# Configure audio
amixer set PCM 100% unmute >/dev/null 2>&1

# Start LCARSTV (the 10-second code timeout handles the rest)
cd /home/cinkadeus/LCARSTV
python3 -m lcarstv --profile pi
```

## Key Changes Explained

1. **Display Server Wait**: The script now waits for X11/Wayland to be ready before starting LCARSTV
2. **Audio First**: Audio configuration happens BEFORE starting the app (was backwards before)
3. **Proper Delays**: Adequate time for graphics drivers and audio subsystem to stabilize
4. **Combined Timeout**: The code fix (10s) + bash delays (7s total) = 17s maximum wait time

## How to Apply

1. SSH into your Raspberry Pi
2. Edit your `.bash_profile`:
   ```bash
   nano ~/.bash_profile
   ```
3. Replace the LCARSTV startup section with one of the versions above
4. Save and exit (Ctrl+O, Enter, Ctrl+X)
5. Reboot to test:
   ```bash
   sudo reboot
   ```

## Testing

After rebooting:
- LCARSTV should start automatically without errors
- If issues persist, check logs: `journalctl --user -b`
- You can still run manually via SSH as before

## Troubleshooting

If the app still fails on startup:

1. **Check if display server is starting**: 
   ```bash
   echo $DISPLAY
   echo $WAYLAND_DISPLAY
   ```

2. **Verify MPV works manually**:
   ```bash
   mpv --version
   mpv --fullscreen --idle=yes
   ```

3. **Increase delays further**: Try `sleep 10` instead of `sleep 5`

4. **Check systemd logs**:
   ```bash
   journalctl --user -b | grep -i lcarstv
   journalctl --user -b | grep -i mpv
   ```

## Future Improvement: SystemD Service

For production use, consider converting to a systemd user service with proper dependency ordering. This would replace the `.bash_profile` approach entirely.

Let me know if you'd like help setting that up!
