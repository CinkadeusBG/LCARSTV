# Sequential Playthrough Feature

## Overview

The Sequential Playthrough feature allows channels to play episodes in order (S01E01, S01E02, S01E03, etc.) instead of randomly shuffling them. When enabled, the channel will:

- Play all episodes in sequential order based on SxxExx naming
- Loop back to the first episode after finishing the last one
- Continue playing non-stop just like other channels
- Persist the current playback position across restarts

## Configuration

To enable sequential playthrough for a channel, add `"sequential_playthrough": true` to the channel configuration in `config/channels.json`:

```json
{
  "call_sign": "KTOS",
  "label": "Star Trek: The Original Series",
  "media_dirs": ["Z:/media/KTOS"],
  "extensions": [".mkv", ".mp4", ".avi"],
  "cooldown": 10,
  "sequential_playthrough": true
}
```

### Configuration Options

- **sequential_playthrough** (boolean, optional): Default is `false` (random playback)
  - `true`: Episodes play in sequential order
  - `false` or omitted: Episodes play in random order with cooldown

## Episode Naming Requirements

For sequential playback to work correctly, your episode files should follow the **SxxExx naming convention**:

### Supported Formats
- `S01E01 - Episode Title.mkv`
- `s02e15 - Episode Name.mp4`
- `Show.S03E22.1080p.mkv`
- `Show Name - S04E05 - Title.mkv`

### Pattern Details
- **Case insensitive**: Both `S01E01` and `s01e01` work
- **Season**: `S` followed by digits (e.g., `S01`, `S1`, `S001`)
- **Episode**: `E` followed by digits (e.g., `E01`, `E1`, `E001`)
- **Flexible**: Can appear anywhere in the filename

### Files Without Episode Numbers
Files that don't match the SxxExx pattern will:
- Be sorted alphabetically
- Play after all episodic content
- Still be included in the sequential rotation

## How It Works

### Sequential Selection Algorithm

1. **Sorting**: All eligible files are sorted by:
   - First: Season number (ascending)
   - Second: Episode number (ascending)
   - Third: Alphabetically (for non-episodic files)

2. **Index Tracking**: The system maintains a `sequential_index` for each channel that tracks the current position in the sorted list

3. **Wraparound**: When reaching the end of the list, the index wraps back to 0, creating an infinite loop

4. **Persistence**: The current index is saved to `data/state.json` so playback resumes at the correct position after restart

### State Storage

Sequential playthrough state is stored in `data/state.json`:

```json
{
  "version": 2,
  "channels": {
    "KTOS": {
      "sequential_index": 5,
      "current_block_id": "file:z:/media/ktos/s01e06.mkv",
      "started_at": "2026-01-11T19:30:00Z"
    }
  }
}
```

## Implementation Details

### Modified Files

1. **lcarstv/core/config.py**
   - Added `sequential_playthrough: bool` field to `ChannelConfig`
   - Parses the new field from JSON configuration

2. **lcarstv/core/state_store.py**
   - Added `sequential_index: int` field to `PersistedChannel`
   - Handles serialization/deserialization of the index

3. **lcarstv/core/selector.py**
   - Added `_parse_episode_info()` function to extract SxxExx patterns
   - Added `_sort_items_sequentially()` function to sort episodes
   - Added `pick_next_sequential()` method for sequential selection
   - Modified `pick_next()` to route to sequential mode when enabled

4. **lcarstv/core/channel.py**
   - Added `sequential_playthrough: bool` field to `ChannelRuntime`
   - Passes sequential flag to selector when picking next episode

5. **lcarstv/core/station.py**
   - Wires `sequential_playthrough` from config to channel runtime

6. **config/channels.json**
   - Updated example channels to demonstrate the feature

## Testing

A test script is provided to verify the sequential playthrough functionality:

```bash
python test_sequential.py
```

The test script verifies:
- Episode pattern parsing (SxxExx detection)
- Sequential sorting algorithm
- Wraparound behavior at the end of the list

## Example Usage

### Star Trek: TOS (Sequential)
```json
{
  "call_sign": "KTOS",
  "label": "Star Trek: The Original Series",
  "media_dirs": ["Z:/media/KTOS"],
  "cooldown": 10,
  "sequential_playthrough": true
}
```
Result: Episodes play S01E01 → S01E02 → ... → S03E24 → S01E01 (loop)

### Star Trek: DS9 (Random)
```json
{
  "call_sign": "KDSN",
  "label": "Star Trek: Deep Space Nine",
  "media_dirs": ["Z:/media/KDSN"],
  "cooldown": 10
}
```
Result: Episodes play in random order with cooldown to avoid repeats

## Backwards Compatibility

- **Default behavior**: Channels without `sequential_playthrough` use random playback (existing behavior)
- **Existing state files**: The new `sequential_index` field defaults to 0 for existing channels
- **Mixed channels**: You can have some channels sequential and others random in the same configuration

## Benefits

1. **Viewer Experience**: Watch shows in intended episode order
2. **Binge Watching**: Perfect for narrative-driven series
3. **No Manual Tracking**: System remembers where you left off
4. **Continuous Playback**: Still operates like a 24/7 TV channel
5. **Flexible**: Per-channel configuration allows mixing random and sequential channels

## Troubleshooting

### Episodes Not Playing in Order

1. **Check filename format**: Ensure files follow SxxExx naming convention
2. **Case sensitivity**: Pattern matching is case-insensitive, but check for typos
3. **Enable debug mode**: Set `"debug": true` in `config/settings.json` to see selection logs

### Sequential Index Reset

If you want to reset a channel back to the first episode:
1. Stop the application
2. Edit `data/state.json`
3. Set `"sequential_index": 0` for the desired channel
4. Restart the application

## Future Enhancements

Potential improvements for future versions:
- Support for multi-episode blocks in sequential mode
- Skip/rewind controls for manual navigation
- Chapter markers for long episodes
- Shuffle within seasons (maintain season order, randomize episodes)
