# LCARSTV Documentation

Complete documentation for LCARSTV - a 24/7 TV channel simulator.

## Getting Started

New to LCARSTV? Start here:
- **[Main README](../README.md)** - Installation, setup, and usage guide
- **[Channel Setup Guide](../README.md#channel-setup)** - How to configure your channels
- **[Settings Configuration](../README.md#settings-configuration)** - Application settings

## Feature Documentation

### Sequential Playthrough
**[SEQUENTIAL_PLAYTHROUGH.md](../SEQUENTIAL_PLAYTHROUGH.md)**

Play TV series episodes in order (S01E01 → S01E02 → ...) instead of random shuffle.

Topics covered:
- Configuration and usage
- File naming requirements (SxxExx pattern)
- State persistence
- Implementation details

### Media Catalog Cache
**[MEDIA_CATALOG.md](MEDIA_CATALOG.md)**

Automatic media library caching for 95% faster startup times.

Topics covered:
- How the smart rescan logic works
- Performance impact (5-10s → 0.5s)
- Cache structure and invalidation
- Debug output and troubleshooting

### Commercial Break Detection
**[METADATA_GENERATOR.md](METADATA_GENERATOR.md)**

Standalone tool to generate commercial break metadata using FFmpeg detection.

Topics covered:
- Usage and command-line options
- Detection strategies (blackdetect vs. silence)
- Parameter tuning
- Output format and workflow

## Technical Documentation

**[technical/](technical/)** - Implementation details and bug fixes

- **[BUTTON_LAG_FIX.md](technical/BUTTON_LAG_FIX.md)** - Keyboard buffer issue resolution
- **[SINGLE_INSTANCE_IMPLEMENTATION.md](technical/SINGLE_INSTANCE_IMPLEMENTATION.md)** - Lock mechanism
- **[STARTUP_FIX.md](technical/STARTUP_FIX.md)** - Raspberry Pi auto-start configuration

## Quick Reference

### File Locations

| File | Purpose |
|------|---------|
| `config/channels.json` | Channel definitions |
| `config/settings.json` | Application settings |
| `data/media_catalog.json` | Media library cache |
| `data/durations.json` | FFprobe duration cache |
| `data/state.json` | Channel playback state |

### Command-Line Usage

```bash
# Run with profile
python -m lcarstv --profile windows
python3 -m lcarstv --profile pi

# Test configuration
python -m lcarstv --dry-run

# Generate commercial metadata
python -m lcarstv_tools.generate_metadata --path "Z:/media" --recursive
```

### Keyboard Controls

| Key | Action |
|-----|--------|
| `UP` / `Page Up` | Channel up |
| `DOWN` / `Page Down` | Channel down |
| `Q` / `ESC` | Quit |

## Contributing

For development and contribution guidelines, see the main repository.

## Need Help?

1. Check the [Troubleshooting section](../README.md#troubleshooting) in the main README
2. Review relevant feature documentation above
3. Check technical docs for known issues and fixes
4. Enable debug mode: Set `"debug": true` in `config/settings.json`
