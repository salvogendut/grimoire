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
    "focused": true,
    "handle_source": "remembered"
  }
]
```

The JSON return type keeps the DBus contract simple while the schema is still
changing. It can become a structured DBus type once the fields stabilize.

### `GetPalette() -> as`

Returns the ordered list of color names currently used for assignment.

### `GetBirds() -> as`

Returns the ordered list of bird names currently used for assignment.

### `ListApps() -> s`

Returns a JSON string containing launchable applications visible to GNOME.

Example payload:

```json
[
  {
    "id": "org.gnome.Calculator.desktop",
    "name": "Calculator"
  }
]
```

### `LaunchApp(s query) -> b`

Launches the app matched by `query`. The extension first checks common aliases
such as `calculator`, then falls back to GNOME's visible application registry.

### `PasteText(s text) -> b`

Copies `text` to the clipboard and emits the paste shortcut into the currently
focused window. The extension uses terminal paste (`Ctrl+Shift+V`) for common
terminal windows and normal paste (`Ctrl+V`) elsewhere.

### `PressKey(s key) -> b`

Emits a single supported key press into the currently focused window. The first
supported key is `enter`, used so terminal commands execute as real Return key
events instead of pasted newlines.

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

Forces the extension to rescan windows and reposition frames.

### `RefreshHandles() -> b`

Clears remembered handle assignments and deliberately reassigns handles for the
currently visible windows.

### `SetDaemonStatus(b running) -> b`

Compatibility method for older daemons. `true` maps to the `idle` daemon state
and `false` maps to `inactive`.

### `SetDaemonState(s state, s detail) -> b`

Updates the GNOME top-bar daemon indicator with the daemon's current phase. The
daemon sends the current state immediately on phase changes and repeats it as a
heartbeat while it is listening. The extension expires the active state
automatically if heartbeats stop.

Supported states:

- `inactive`
- `idle`
- `recording`
- `transcribing`
- `parsing`
- `parsed`
- `executing`
- `blocked`
- `error`

The optional `detail` string is short human-readable context for accessibility
and debugging, such as the parsed command or error summary.

### `GetExecutionMode() -> b`

Returns whether the daemon is currently allowed to execute listened commands.
The daemon checks this immediately before dispatching a recognized command.

### `SetExecutionMode(b enabled) -> b`

Sets the execution gate used by `GetExecutionMode`. The top-bar icon toggles
this value. The extension forces it off when the daemon is inactive or the
heartbeat expires.

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
