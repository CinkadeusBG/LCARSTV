# 90s-Style TV Guide Channel

## Overview

The TV Guide channel (TVG) now features authentic 90s-style graphics inspired by the classic Prevue Channel/TV Guide Network aesthetic from the 1990s.

## Features

### Visual Design
- **Thick Outlined Text**: Heavy black outlines around all text (very 90s)
- **Vibrant Color Palette**:
  - Gold/Yellow (`#FFD700`)
  - Cyan (`#00FFFF`)
  - Orange (`#FFA500`)
  - White (`#FFFFFF`)
  - Lime Green (`#32FF32`)
- **Progress Bars**: Chunky block-style progress indicators using Unicode characters (█ and ░)
- **Digital Clock**: Real-time clock display in upper-right
- **Large Title**: "📺 TV GUIDE" header with emoji icon

### Layout

```
┌────────────────────────────────────────────────────────┐
│  📺 TV GUIDE                            9:48 PM        │  Header
├────────────────────────────────────────────────────────┤
│                                                         │
│  KTOS      S01E01 - The Man Trap        ███████████░░░ 73%  │  Channel row
│  WTNG      S03E15 - Yesterday's Ent...  ████████░░░░░░ 53%  │  Channel row
│  KDSN      S04E08 - Little Green Men    ██████████████ 93%  │  Channel row
│  ...                                                    │
│                                                         │
├────────────────────────────────────────────────────────┤
│      ◄◄◄  PRESS CHANNEL UP/DOWN TO EXIT  ►►►          │  Footer
└────────────────────────────────────────────────────────┘
```

### Color Cycling

Channel rows cycle through five vibrant colors:
1. Yellow - First channel
2. Cyan - Second channel
3. Orange - Third channel
4. White - Fourth channel
5. Lime Green - Fifth channel
(Pattern repeats for additional channels)

## Technical Implementation

### ASS (Advanced SubStation Alpha) Formatting

The guide uses mpv's built-in ASS subtitle rendering for maximum compatibility and performance:

- **No plugins required** - Works with standard mpv
- **Low overhead** - Text-based rendering is very efficient
- **Native support** - ASS formatting is part of mpv's core functionality
- **Real-time updates** - Refreshes every 3 seconds

### Key Components

1. **lcarstv/ui/tvguide_renderer.py**
   - Comprehensive renderer with PIL/Pillow for future image-based rendering
   - Gradient backgrounds, outlined text, progress bars
   - Ready for enhancement if raw image overlays become feasible

2. **lcarstv/player/mpv_player.py**
   - Enhanced `show_tvg_guide_osd()` method with 90s styling
   - ASS formatting with thick borders (`\bord3`, `\bord4`)
   - Color cycling and progress bar generation
   - Clock display integration

3. **lcarstv/app.py**
   - TVG refresh logic (every 3 seconds)
   - Time formatting for clock display
   - Guide data collection and display

## Usage

1. **Tune to TVG Channel**: Press Channel Up/Down until you reach "TVG"
2. **View Guide**: The guide displays automatically with:
   - All active channels
   - Currently playing episodes
   - Progress bars showing how far into each episode
   - Real-time clock
3. **Exit Guide**: Press Channel Up or Channel Down to return to normal viewing

## Future Enhancements

The `tvguide_renderer.py` module is ready for:
- Full bitmap rendering with PIL/Pillow
- Scrolling animations
- Channel logos
- More complex gradients and effects
- Time-slot grids (showing future programming)

Currently, the ASS text-based approach provides excellent 90s aesthetics with minimal complexity.

## Dependencies

- **Pillow >= 10.0.0** (added to pyproject.toml)
- Standard Python libraries (datetime, etc.)
- mpv with ASS subtitle support (standard)

## Color Reference

```python
# ASS uses BGR format: &HBBGGRR&
color_yellow = "&H00D7FF&"  # Gold/Yellow (255, 215, 0)
color_cyan = "&HFFFF00&"    # Cyan (0, 255, 255)
color_white = "&HFFFFFF&"   # White (255, 255, 255)
color_orange = "&H00A5FF&"  # Orange (255, 165, 0)
color_lime = "&H32FF32&"    # Lime (50, 255, 50)
color_black = "&H000000&"   # Black (outline)
```

## Design Philosophy

The design captures the essence of 90s TV Guide channels:
- **Bold and Colorful**: Saturated colors that pop on CRT displays
- **Outlined Text**: Heavy black outlines for maximum readability
- **Chunky Elements**: Progress bars use thick blocks
- **Simple Animations**: Minimal movement, static layout
- **Functional**: Clear hierarchy, easy to scan

---

*Created: 2/25/2026*
*Version: 1.0*
