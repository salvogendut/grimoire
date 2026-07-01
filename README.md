# Grimoire

Grimoire is an experimental GNOME-based voice control layer for Linux.
It does not replace Mutter or GNOME Shell. Instead, it adds a small GNOME
Shell extension that gives each visible window a colored frame and handle, then exposes
window actions over the session bus so a local voice daemon can execute
commands such as:

```text
focus yellow
focus sparrow
maximize blue
close green
open calculator
type make test
```

The current prototype covers focus/window management by color or bird handle,
application launching, and clipboard-paste dictation with terminal-aware Enter
handling. Window handles are remembered by app/title where possible, so a
reopened window can keep the same bird/color if that handle is still available.

## Screenshots

<p>
  <img src="screenshots/desktop.png" alt="Grimoire colored window frames with bird labels" width="49%">
  <img src="screenshots/framed-labels-files.png" alt="Multiple Grimoire framed windows with labels" width="49%">
</p>

<p>
  <img src="screenshots/framed-labels-gallery.png" alt="Grimoire framed labels across several overlapping windows" width="49%">
</p>

## Current State

This repository currently contains:

- `extension/grimoire@salvogendut.github.io`: GNOME Shell extension skeleton
  for colored frames, bird-name tabs, and focus/window actions over DBus.
- `daemon/grimoired.py`: small command parser and dispatcher for local testing.
- `docs/architecture.md`: system design and major constraints.
- `docs/protocol.md`: the first DBus contract between the daemon and extension.

The code targets the local development machine's GNOME Shell 50.x APIs.

## Development

Install the extension into your local GNOME Shell extensions directory:

```sh
make install-extension
```

Then enable it:

```sh
make enable-extension
```

If enabling says the extension does not exist, run `make install-extension`
again with the updated Makefile. On Wayland, you may still need to log out and
back in after installing a new extension for the first time because GNOME Shell
does not always rescan extension metadata in a running session. During
development, inspect extension errors with:

```sh
journalctl --user -f /usr/bin/gnome-shell
```

Once enabled, try the parser without touching DBus:

```sh
python3 daemon/grimoired.py --dry-run --command "focus yellow"
```

If the extension is running, send a command to GNOME Shell:

```sh
python3 daemon/grimoired.py --command "focus yellow"
```

List the windows known to the extension:

```sh
python3 daemon/grimoired.py --list-windows
```

List launchable apps known to the extension:

```sh
python3 daemon/grimoired.py --list-apps
```

Open an application by voice-style command text:

```sh
python3 daemon/grimoired.py --command "open calculator"
```

Ask for the current visible handles or launchable applications:

```sh
python3 daemon/grimoired.py --command "list windows"
python3 daemon/grimoired.py --command "show apps"
```

Deliberately clear remembered handle assignments and reassign current windows:

```sh
python3 daemon/grimoired.py --command "refresh handles"
```

Run the current tests:

```sh
make test
```

## Voice Prototype

The first voice path uses a local `whisper.cpp` install if present at:

```text
/var/home/salvogendut/Dev/whisper.cpp/build/bin/whisper-cli
/var/home/salvogendut/Dev/whisper.cpp/models/ggml-base.en.bin
```

Transcribe an existing WAV without executing anything:

```sh
python3 daemon/grimoired.py --audio-file /path/to/audio.wav
```

Record one short microphone utterance and parse it without executing:

```sh
python3 daemon/grimoired.py --listen --record-seconds 3
```

Run an Enter-to-record command loop:

```sh
python3 daemon/grimoired.py --listen-loop --record-seconds 3
```

In loop mode, press Enter to record one command and type `q` then Enter to
quit. Non-destructive window commands execute immediately. Destructive commands
such as `close sparrow` require confirmation. Use `--dry-run` with
`--listen-loop` to transcribe and parse without executing.

The daemon prints a compact trace while parsing and executing commands:

```text
heard: "type dove git status enter"
parsed: dictate target=dove text="git status enter"
action: focus dove -> ok
action: paste "git status" -> ok
action: press enter -> ok
```

Pass `--quiet` to suppress the trace.

Dictation uses the focused window. Say `type hello world` to paste text into
the active app. Common terminal words are normalized before paste, for example
`type ls minus la enter` pastes `ls -la` and then presses Enter.

Dictation can also target a window handle directly. Say
`type dove git status enter` or `dictate to crow hello world` to focus that
window before pasting.

Only execute listened commands when you explicitly opt in:

```sh
python3 daemon/grimoired.py --listen --record-seconds 3 --execute-listen
```

You can override the recognizer with an ASR command template:

```sh
python3 daemon/grimoired.py --audio-file sample.wav --asr-command "my-asr {audio}"
```

## Design Direction

Grimoire has three cooperating layers:

1. GNOME Shell extension: colored frames, window inventory, focus, close,
   minimize, maximize, fullscreen.
2. Voice daemon: microphone capture, speech recognition, command parsing, and
   dispatch.
3. Input executor: dictation and keyboard/paste events through a Wayland-safe
   path such as xdg-desktop-portal RemoteDesktop/EIS or an accessibility route
   where appropriate.

See [docs/architecture.md](docs/architecture.md) for details.
