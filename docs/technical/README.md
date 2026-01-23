# Technical Documentation

This directory contains technical implementation details, bug fixes, and architectural decisions for LCARSTV.

## Contents

### Implementation Details

- **[BUTTON_LAG_FIX.md](BUTTON_LAG_FIX.md)** - Resolution of long-running performance issue with button inputs
  - Problem: Button lag after 2-6 hours of runtime
  - Root cause: Unbounded buffer growth in keyboard input handler
  - Solution: Aggressive buffer management and iteration limits

- **[SINGLE_INSTANCE_IMPLEMENTATION.md](SINGLE_INSTANCE_IMPLEMENTATION.md)** - Single-instance lock implementation
  - Prevents multiple LCARSTV instances from running simultaneously
  - Uses fcntl file locking on Linux/Pi
  - No-op on Windows for development flexibility

- **[STARTUP_FIX.md](STARTUP_FIX.md)** - Raspberry Pi auto-start configuration
  - Problem: MPV fails to start when launched via .bash_profile
  - Solution: Wait for display server and graphics initialization
  - Includes recommended .bash_profile configurations

## Purpose

These documents serve as:
- **Historical record** of issues encountered and resolved
- **Reference material** for understanding implementation decisions
- **Troubleshooting guides** for similar issues in the future
- **Development documentation** for contributors

## Related Documentation

For user-facing documentation, see:
- [Main README](../../README.md) - User guide and setup instructions
- [Sequential Playthrough](../../SEQUENTIAL_PLAYTHROUGH.md) - Feature documentation
- [Media Catalog](../MEDIA_CATALOG.md) - Performance caching system
- [Metadata Generator](../METADATA_GENERATOR.md) - Commercial break detection tool
