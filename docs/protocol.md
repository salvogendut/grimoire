# Shell Protocol

The GNOME Shell extension exports a session-bus service for the local voice
daemon.

## Bus

- Bus name: `org.grimoire.Shell`
- Object path: `/org/grimoire/Shell`
- Interface: `org.grimoire.Shell`

## Methods

### `ListWindows() -> s`

Returns a JSON string containing the extension's current handle registry.

Example payload:

```json
[
  {
    "color": "yellow",
    "bird": "sparrow",
    "title": "Terminal",
    "wm_class": "org.gnome.Terminal",
    "pid": 1234,
    "stable_sequence": 42,
    "focused": true
  }
]
```

The JSON return type keeps the DBus contract simple while the schema is still
changing. It can become a structured DBus type once the fields stabilize.

### `GetPalette() -> as`

Returns the ordered list of color names currently used for assignment.

### `GetBirds() -> as`

Returns the ordered list of bird names currently used for assignment.

### `FocusColor(s color) -> b`

Focuses the window assigned to `color`. This method is kept for compatibility
but its argument can be any handle, including a bird name.

### `RunWindowCommand(s handle, s command) -> b`

Runs a command against the window assigned to `handle`. Handles currently include
color names such as `yellow` and bird names such as `sparrow`.

Supported commands in the first scaffold:

- `focus`
- `close`
- `minimize`
- `unminimize`
- `maximize`
- `unmaximize`
- `fullscreen`
- `unfullscreen`

### `Refresh() -> b`

Forces the extension to rescan windows and reposition sidebars.

## Signals

### `WindowsChanged()`

Emitted when the color registry changes or window metadata may need to be
refreshed by the daemon.

## Example Calls

```sh
gdbus call \
  --session \
  --dest org.grimoire.Shell \
  --object-path /org/grimoire/Shell \
  --method org.grimoire.Shell.RunWindowCommand \
  sparrow focus
```

```sh
gdbus call \
  --session \
  --dest org.grimoire.Shell \
  --object-path /org/grimoire/Shell \
  --method org.grimoire.Shell.ListWindows
```
