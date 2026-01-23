# LCARSTV Documentation Index

Complete guide to all LCARSTV documentation.

## 📚 Start Here

### New Users
1. **[README.md](README.md)** - Main documentation with installation and setup
2. **[Channel Setup Guide](README.md#channel-setup)** - Configure your TV channels
3. **[Quick Start](README.md#quick-start)** - Get running in 3 steps

### Returning Users
- **[docs/README.md](docs/README.md)** - Documentation hub with quick reference

## 📖 Core Documentation

### User Guide
**[README.md](README.md)** - Complete user manual

Sections:
- Overview and features
- Requirements and installation
- **Channel setup process and requirements** ⭐
- Settings configuration
- Cross-platform profiles
- Running the application
- GPIO buttons (Raspberry Pi)
- Optional features
- Troubleshooting

### Channel Configuration
All channel setup information is in **[README.md - Channel Setup](README.md#channel-setup)**

Key topics:
- **Required fields**: `call_sign`, `label`
- **Regular media channels**: Using `media_dirs` and `extensions`
- **Aggregate channels**: Combining content with `aggregate_from_channels`
- **Optional fields**: `show_commercials`, `cooldown`, `sequential_playthrough`
- **File naming requirements** for sequential playback (SxxExx pattern)
- Complete configuration examples

### Settings Reference
All settings documentation is in **[README.md - Settings Configuration](README.md#settings-configuration)**

Topics:
- Application settings structure
- Key settings explained
- GPIO button configuration
- Platform-specific settings

## 🎯 Feature Documentation

### Sequential Playthrough
**[SEQUENTIAL_PLAYTHROUGH.md](SEQUENTIAL_PLAYTHROUGH.md)**

Watch series in episode order instead of random shuffle.
- Configuration guide
- SxxExx naming requirements
- How it works internally
- State persistence

### Media Catalog Cache
**[docs/MEDIA_CATALOG.md](docs/MEDIA_CATALOG.md)**

95% faster startup through intelligent caching.
- Smart rescan logic
- Performance metrics
- Cache structure
- Error recovery

### Commercial Break Detection
**[docs/METADATA_GENERATOR.md](docs/METADATA_GENERATOR.md)**

Generate commercial break metadata using FFmpeg.
- Command-line usage
- Detection strategies
- Parameter tuning
- Output format

## 🔧 Technical Documentation

### Implementation Details
**[docs/technical/](docs/technical/)** - Developer and troubleshooting docs

#### Bug Fixes & Implementations

**[BUTTON_LAG_FIX.md](docs/technical/BUTTON_LAG_FIX.md)**
- Problem: Button lag after hours of runtime
- Root cause: Unbounded keyboard buffer growth
- Solution: Aggressive buffer management

**[SINGLE_INSTANCE_IMPLEMENTATION.md](docs/technical/SINGLE_INSTANCE_IMPLEMENTATION.md)**
- Prevents multiple LCARSTV instances
- Uses fcntl file locking (Linux/Pi)
- Testing and verification

**[STARTUP_FIX.md](docs/technical/STARTUP_FIX.md)**
- Raspberry Pi auto-start issues
- Display server initialization timing
- Recommended .bash_profile configurations

## 🗂️ Documentation Structure

```
LCARSTV/
├── README.md                          # Main user documentation ⭐
├── DOCUMENTATION_INDEX.md             # This file
├── SEQUENTIAL_PLAYTHROUGH.md          # Sequential playback feature
├── docs/
│   ├── README.md                      # Documentation hub
│   ├── MEDIA_CATALOG.md               # Caching system
│   ├── METADATA_GENERATOR.md          # Commercial detection tool
│   └── technical/
│       ├── README.md                  # Technical docs index
│       ├── BUTTON_LAG_FIX.md          # Performance fix
│       ├── SINGLE_INSTANCE_IMPLEMENTATION.md
│       └── STARTUP_FIX.md             # Raspberry Pi startup
├── config/                            # Configuration files
│   ├── channels.json                  # Channel definitions
│   ├── channels.pi.json
│   ├── channels.windows.json
│   ├── settings.json                  # Application settings
│   ├── settings.pi.json
│   └── settings.windows.json
└── data/                              # Runtime data (auto-generated)
    ├── media_catalog.json
    ├── durations.json
    └── state.json
```

## 🎯 Quick Navigation

### By Topic

**Setup & Configuration**
- [Installation](README.md#installation)
- [Channel Setup](README.md#channel-setup) ⭐
- [Settings](README.md#settings-configuration)
- [Profiles](README.md#cross-platform-profiles)

**Features**
- [Sequential Playthrough](SEQUENTIAL_PLAYTHROUGH.md)
- [Commercial Breaks](docs/METADATA_GENERATOR.md)
- [GPIO Buttons](README.md#gpio-buttons-raspberry-pi)
- [Media Caching](docs/MEDIA_CATALOG.md)

**Running & Control**
- [Running the App](README.md#running-the-application)
- [Keyboard Controls](README.md#keyboard-controls)
- [Troubleshooting](README.md#troubleshooting)

**Technical**
- [Button Lag Fix](docs/technical/BUTTON_LAG_FIX.md)
- [Single Instance Lock](docs/technical/SINGLE_INSTANCE_IMPLEMENTATION.md)
- [Startup Fix (Pi)](docs/technical/STARTUP_FIX.md)

### By User Type

**First-Time Users**
1. [README.md](README.md) - Read Overview and Installation
2. [Channel Setup](README.md#channel-setup) - Configure your channels
3. [Quick Start](README.md#quick-start) - Run the application

**Advanced Users**
- [Sequential Playthrough](SEQUENTIAL_PLAYTHROUGH.md) - Ordered episode playback
- [Metadata Generator](docs/METADATA_GENERATOR.md) - Commercial detection
- [Media Catalog](docs/MEDIA_CATALOG.md) - Caching internals

**Developers**
- [Technical Docs](docs/technical/) - Implementation details
- [Bug Fixes](docs/technical/BUTTON_LAG_FIX.md) - Problem analysis and solutions

**Raspberry Pi Users**
- [GPIO Buttons](README.md#gpio-buttons-raspberry-pi) - Hardware setup
- [Startup Fix](docs/technical/STARTUP_FIX.md) - Auto-start configuration

## 📋 Quick Reference

### Essential Commands

```bash
# Run with profile
python -m lcarstv --profile windows
python3 -m lcarstv --profile pi

# Test configuration
python -m lcarstv --dry-run

# Generate commercial metadata
python -m lcarstv_tools.generate_metadata --path "Z:/media" --recursive
```

### Configuration Files

| File | Purpose |
|------|---------|
| `config/channels.json` | Define your TV channels |
| `config/settings.json` | Application settings |
| `data/media_catalog.json` | Media library cache |
| `data/state.json` | Channel playback state |

### Keyboard Controls

| Key | Action |
|-----|--------|
| `UP` / `Page Up` | Channel up |
| `DOWN` / `Page Down` | Channel down |
| `Q` / `ESC` | Quit |

## 🔍 Finding Information

**Need to know how to...**
- Set up channels? → [README.md - Channel Setup](README.md#channel-setup)
- Configure settings? → [README.md - Settings Configuration](README.md#settings-configuration)
- Play episodes in order? → [SEQUENTIAL_PLAYTHROUGH.md](SEQUENTIAL_PLAYTHROUGH.md)
- Generate commercial breaks? → [docs/METADATA_GENERATOR.md](docs/METADATA_GENERATOR.md)
- Set up GPIO buttons? → [README.md - GPIO Buttons](README.md#gpio-buttons-raspberry-pi)
- Fix startup issues? → [README.md - Troubleshooting](README.md#troubleshooting)
- Understand technical details? → [docs/technical/](docs/technical/)

## 💡 Tips

- Start with [README.md](README.md) for complete setup guide
- All channel configuration details are in the main README
- Use `--dry-run` to test your configuration
- Enable `"debug": true` in settings.json for detailed logging
- Check [docs/technical/](docs/technical/) for known issues and fixes

---

**Ready to start?** → [README.md](README.md)
