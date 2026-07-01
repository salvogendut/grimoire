# Architecture

Grimoire should be treated as a GNOME control plane, not a standalone window
manager. Under GNOME, Mutter remains the compositor/window manager and GNOME
Shell remains the process with legitimate access to window actors. Grimoire
adds voice-friendly handles and routes user-approved commands into those layers.

## Goals

- Put visually distinct, speech-addressable handles on each manageable GNOME
  window: a colored frame and a bird-name tab.
- Let the user say commands like `focus yellow`, `close green`, and
  `maximize blue`.
- Support dictation into the currently focused terminal or text field without
  relying on X11-only tools.
- Keep speech recognition and AI parsing outside GNOME Shell so the extension
  stays small and debuggable.

## Non-Goals

- Replacing Mutter.
- Implementing a full tiling window manager in the first milestone.
- Sending arbitrary input behind the user's back. Wayland's restrictions are a
  feature here; the input layer needs explicit user consent.

## Layers

### 1. GNOME Shell Extension

Responsibilities:

- Track manageable `Meta.Window` instances.
- Draw one colored frame and bird-name tab per tracked window.
- Show a top-bar daemon status indicator based on daemon heartbeats.
- Let the top-bar indicator or keyboard shortcut arm or disarm listened command
  execution.
- Maintain a handle-to-window registry.
- Expose a session-bus API for window inventory and commands.
- Execute compositor-native actions: focus, close, minimize, maximize, and
  fullscreen.

Why this belongs in the extension:

- GNOME Shell extensions run inside the shell process and can use Mutter/GNOME
  Shell APIs such as window signals, frame rectangles, and activation.
- A normal user process cannot reliably decorate or focus arbitrary Wayland
  windows from outside the compositor.

Risks:

- GNOME Shell extension APIs can break between shell versions. The first
  scaffold targets GNOME Shell 50.x, which is what this workstation is running.
- Overlay frames are not true server-side decorations. They are visual markers
  in the shell scene graph and must be kept synchronized with window geometry.

### 2. Voice Daemon

Responsibilities:

- Capture microphone audio through PipeWire/PulseAudio.
- Run voice activity detection and speech recognition.
- Convert transcripts into structured intents.
- Call the extension over DBus.
- Send a heartbeat while listening so the shell indicator can show whether the
  daemon is currently running.
- Check the shell-owned execution gate before dispatching any continuously
  listened command.
- Eventually manage wake word or push-to-talk activation.

First implementation:

- A deterministic grammar parser handles commands such as `focus yellow`.
- The local ASR path uses whisper.cpp when configured, with an override hook for
  other recognizers.

Candidate ASR backends:

- Whisper.cpp or faster-whisper for higher accuracy and broad language support.
- Vosk for lighter offline command recognition.
- A cloud model only as an optional backend, never as a default dependency.

### 3. Intent/AI Layer

The first parser should be grammar-first:

```text
focus yellow
close yellow
maximize blue
type make test
```

An AI layer can be added after the deterministic path works. Its job should be
to normalize natural phrases into explicit intents, not to hold desktop
authority itself. For example:

```text
"put the yellow terminal on the left" -> { action: "tile_left", color: "yellow" }
```

The daemon should log transcript, parsed intent, confidence, and executed action
so mistakes can be diagnosed.

### 4. Dictation/Input Executor

Dictation is separate from window control. On Wayland, `xdotool`-style input
injection is not a reliable foundation. Grimoire should choose one of these
paths:

- xdg-desktop-portal RemoteDesktop/EIS: user-consented keyboard events, suitable
  for paste and key synthesis.
- Clipboard-plus-paste: set clipboard text, then invoke the target application's
  paste shortcut. This is practical for terminals but requires terminal-aware
  shortcuts such as Ctrl+Shift+V.
- AT-SPI: useful for accessibility-aware applications and semantic actions, but
  not a universal replacement for keyboard input.

Recommended prototype path:

1. Focus the target window through the extension.
2. Set clipboard text from the daemon.
3. Send paste through a user-approved RemoteDesktop/EIS session.
4. Add terminal-specific behavior after detecting the focused app class.

## Process Boundaries

```text
microphone
  -> voice daemon
    -> ASR
    -> parser / AI intent normalizer
    -> DBus: org.grimoire.Shell
      -> GNOME Shell extension
        -> Mutter/GNOME Shell window APIs

dictation text
  -> input executor
    -> portal/EIS or accessibility path
      -> focused application
```

## Handle Registry

Colors and bird names are voice handles, not stable window identities. The
extension assigns the first unused color and bird name from human-speakable
palettes to each manageable window. When a window closes, its handles become
available again.

Open design questions:

- What happens when there are more windows than available color/bird handles?
- Should handles persist per application, per window, or per workspace?
- Should the user be able to say `swap yellow and green`?

The first prototype chooses a finite palette and leaves overflow windows
unassigned until a more expressive handle scheme exists.

## References

- GNOME Shell extension guide: https://gjs.guide/extensions/
- Mutter `Meta.Window` API: https://gnome.pages.gitlab.gnome.org/mutter/meta/class.Window.html
- xdg-desktop-portal RemoteDesktop: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html
- AT-SPI API: https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/
