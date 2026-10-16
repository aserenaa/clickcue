# ClickCue

Coordinate-based click automation with named steps, delays, and reusable
profiles. Point ClickCue at any application — a game, a form, a repetitive
workflow — capture the spots you want clicked, name them, set the timing, and
let it run.

## Features

- **Named steps** — every click has a name ("Button 1", "Close menu"), screen
  coordinates, and a delay in seconds before it fires.
- **Visual capture** — hover the cursor over the target and press **F8**; the
  position is captured without sending a click to the app underneath.
- **Loop playback** — run a sequence once, N times, or forever. **F6** starts
  and stops playback globally, even while the target app is in the foreground,
  and interrupts mid-delay. While a step waits, the playback status shows its
  remaining delay in seconds.
- **Window anchoring** — a profile records its target window's position and
  size. If the window has moved or been resized at playback time, ClickCue
  moves it back to the recorded bounds (or scales the coordinates when the
  window can't be moved), so clicks land exactly where you captured them.
- **Auto-focus** — before playback, ClickCue brings the target window to the
  foreground (via the Accessibility API on macOS, which works even when
  regular app activation is ignored) so the first click isn't swallowed by
  window focusing.
- **Click guard** — before *every* click, ClickCue verifies that the target
  window is actually on top at the click point. If something covers it (a
  notification, another app), it refocuses the target and retries; if the
  target stays covered, playback stops with a message instead of clicking
  into the wrong window.
- **Profiles** — save sequences under a name ("Game A - farming") and reload
  them later. Unsaved changes are marked with `*` in the title bar and
  confirmed before being discarded.

## Requirements

- Python 3.10+
- [pynput](https://pypi.org/project/pynput/) (installed automatically)
- tkinter (bundled with most Python installs; on Homebrew Python run
  `brew install python-tk`)

## Installation

```bash
git clone https://github.com/aserenaa/clickcue.git
cd clickcue
python3 -m venv .venv
.venv/bin/pip install .
```

Run it with:

```bash
.venv/bin/clickcue
```

or, without installing, `.venv/bin/pip install pynput` and
`.venv/bin/python -m clickcue`.

## macOS permissions (required)

macOS **silently drops** synthesized clicks and cursor movement unless the app
running Python has the right permissions — there is no error, clicks simply
don't happen. On first launch ClickCue triggers the system prompt and warns
you if permission is missing.

In **System Settings → Privacy & Security**, enable the app you launch Python
from (Terminal, iTerm, VS Code, …) under:

1. **Accessibility** — required to synthesize clicks.
2. **Input Monitoring** — required for the global F6/F8 hotkeys.

Then quit ClickCue and start it again; permissions only apply to new
processes. Note that the grant is per launcher app: Terminal and iTerm each
need their own.

> On Mac keyboards where F-keys control brightness/volume, press **Fn+F6 /
> Fn+F8**, or enable "Use F1, F2, etc. keys as standard function keys" in
> System Settings → Keyboard.

## Usage

1. **Choose the target window** (*Target window → Choose…*): pick the app the
   profile will click on. Its current position and size are recorded so the
   clicks stay anchored to it. If you skip this, the window under your first
   step is detected when you save.
2. **Capture a step**: click *Capture (F8)*, hover the cursor over the target
   point, and press **F8**. A dialog opens with the coordinates pre-filled;
   give the step a name and a delay. Don't move the target window while
   capturing — the anchor bounds are refreshed on each capture.
3. Add, edit (double-click), remove, and reorder (▲/▼) steps as needed.
   Select one or more steps and click *Copy* to duplicate them in order after
   the last selected step, keeping their names, coordinates, and delays. Use
   Shift-click to select a range, or Command-click (macOS) / Ctrl-click
   (Windows/Linux) to select separate steps. The new copies stay selected.
4. Choose *Loop forever* or a repeat count.
5. Press *▶ Start* or hit **F6**. ClickCue focuses the target window, restores
   its recorded position/size if it moved, then plays the sequence. **F6**
   stops it at any moment.
6. Save the sequence as a profile (*Save As…*) and reload it from the
   dropdown. *New* starts a fresh sequence.

## Profiles

Profiles are plain JSON files in `~/.clickcue/profiles/`:

```json
{
  "name": "Game A - farming",
  "loop_forever": true,
  "repetitions": 1,
  "target_window": { "app": "Some Game", "x": 40, "y": 60, "width": 1280, "height": 720 },
  "steps": [
    { "name": "Button 1", "x": 512, "y": 384, "delay": 1.5 }
  ]
}
```

`target_window` stores the window the profile is anchored to and its bounds at
capture time. Before playback ClickCue focuses that window and, if it has
moved or been resized, moves it back to those bounds (falling back to
proportional coordinate scaling when the OS refuses the move). Steps keep
absolute screen coordinates, valid when the window is at the recorded bounds.

## Platform support

Window anchoring and the click guard are best-effort safeguards, not a security
boundary. macOS identifies targets by application name; Windows uses window
titles. Another window from the same app or with the same title can be mistaken
for the target. Linux does not provide these window safeguards. Test sequences
in a harmless window first, and keep F6 available to stop playback.

| Platform | Status |
|---|---|
| macOS | Developed and tested here. Requires the permissions above. |
| Windows | Supported by design (pynput + Win32 window focus), but untested — feedback welcome. No special permissions needed. |
| Linux (X11) | Clicks and hotkeys work via pynput; window auto-focus is a no-op. Untested. |

Coordinates are in screen points, so Retina/HiDPI displays work without
scaling math. Window anchoring compensates for a moved or resized target
window; moving another app's window uses the Accessibility API on macOS and
`MoveWindow` on Windows.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/pip-audit --skip-editable
.venv/bin/python -m build
.venv/bin/twine check dist/*
```

Layout: `clickcue/models.py` (data model), `profiles.py` (JSON persistence),
`playback.py` (threaded click player), `platform_support.py` (permissions and
window focus per OS), `gui.py` (tkinter UI).

Tests use a dummy input backend and mocked mouse controllers so they cannot
send real clicks. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development
workflow and [SECURITY.md](SECURITY.md) for private vulnerability reporting.

## Privacy

ClickCue has no telemetry, network client, or automatic updates. Profiles stay
on your computer in `~/.clickcue/profiles/` as plain JSON. They contain step
names, coordinates, delays, and the target application's name (or window title
on Windows) and bounds. Window titles and step names can reveal private
information. Review and sanitize profiles and screenshots before sharing them.
The global keyboard listener handles F6 and F8; ClickCue does not save typed
text. Installation and dependency/security checks contact package services.

## Before publishing

Follow [docs/PUBLISHING.md](docs/PUBLISHING.md) to review Git's upload contents,
run secret and dependency scans, and enable GitHub's repository protections.
CI checks changes and runs weekly; it does not publish packages or releases.

## Disclaimer

Some games detect or ignore synthesized input, and automating clicks may
violate a game's terms of service. Use at your own risk, especially in online
games.

## License

[MIT](LICENSE)
