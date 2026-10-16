<h1 align="center">ClickCue</h1>

<p align="center">
  <strong>Point. Capture. Replay.<br>Turn repetitive clicks into a reusable routine.</strong>
</p>

<p align="center">
  <a href="https://github.com/aserenaa/clickcue/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/aserenaa/clickcue/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.10 or newer" src="https://img.shields.io/badge/python-%E2%89%A53.10-3776AB?logo=python&amp;logoColor=white">
  <img alt="Desktop app built with Tkinter" src="https://img.shields.io/badge/desktop-Tkinter-475569">
  <img alt="Profiles stored locally" src="https://img.shields.io/badge/profiles-local%20only-2E7D32">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue"></a>
</p>

<p align="center">
  <a href="#installation">Get started</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#platform-support">Platforms</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="SECURITY.md">Security</a> ·
  <a href="LICENSE">License</a>
</p>

---

Opening the same menu, choosing the same button, waiting, and doing it again
gets old quickly. **ClickCue** turns that routine into named steps: capture
screen positions, choose the timing, and replay the sequence once or on a loop.

Everything stays in a small desktop window. Copy steps instead of capturing
them again, add pauses without clicks, watch the countdown, and save profiles
for next time. **F6** starts or stops playback even when another app has focus.

> **A simple routine:** open a menu → choose a view → wait for it to load →
> refresh. Give each step a name and delay, then save the sequence as a profile.

## Features

- 🎯 **Named steps** — every click has a name ("Button 1", "Close menu"), screen
  coordinates, and a delay in seconds before it fires.
- ⏳ **Wait-only steps** — pause without moving the pointer or clicking. In the
  Add/Edit dialog, enable *Wait only (no click)* and enter the delay.
- 📍 **Visual capture** — hover the cursor over the target and press **F8**; the
  position is captured without sending a click to the app underneath.
- 📋 **Copy multiple steps** — select a range or separate steps and duplicate
  them together, preserving their order and settings.
- 🔁 **Loop playback** — run a sequence once, N times, or forever. **F6** starts
  and stops playback globally, even while the target app is in the foreground,
  and interrupts mid-delay. While a step waits, the playback status shows its
  remaining delay in seconds. The list selects and scrolls when the active step
  changes; countdown updates leave the list in place.
- 🪟 **Window anchoring** — a profile records its target window's position and
  size. If the window has moved or been resized at playback time, ClickCue
  moves it back to the recorded bounds (or scales the coordinates when the
  window can't be moved) to keep click positions aligned with the window.
- 🔎 **Auto-focus** — before playback, ClickCue brings the target window to the
  foreground (via the Accessibility API on macOS, which works even when
  regular app activation is ignored) so the first click isn't swallowed by
  window focusing.
- 🛡️ **Click guard** — before *every* anchored click, ClickCue checks that the target
  window is actually on top at the click point. If something covers it (a
  notification, another app), it refocuses the target and retries; if the
  target stays covered, playback stops with a message instead of clicking
  into the wrong window.
- 💾 **Local profiles** — save sequences under a name ("Morning workspace") and reload
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

<details>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
git clone https://github.com/aserenaa/clickcue.git
cd clickcue
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\clickcue.exe
```

Python must be version 3.10 or newer. GUI and window behavior on Windows still
need manual verification; CI runs isolated unit tests.

</details>

### macOS permissions

macOS **silently drops** synthesized clicks and cursor movement unless the app
running Python has the right permissions — there is no error, clicks simply
don't happen. On first launch ClickCue triggers the system prompt and warns
you if permission is missing.

In **System Settings → Privacy & Security**, enable the app you launch Python
from (Terminal, iTerm, VS Code, …) under:

1. **Accessibility** — required to synthesize clicks.
2. **Input Monitoring** — required for the global hotkeys (F6/F8 by default).

Then quit ClickCue and start it again; permissions only apply to new
processes. Note that the grant is per launcher app: Terminal and iTerm each
need their own.

> On Mac keyboards where F-keys control brightness/volume, hold **Fn** with
> your chosen function key (**Fn+F6 / Fn+F8** by default), or enable
> "Use F1, F2, etc. keys as standard function keys" in
> System Settings → Keyboard.

## Usage

Under *Global hotkeys (this machine)*, choose separate **F1–F12** keys for
start/stop and capture, then click *Apply*. Stop playback and finish any armed
capture before applying changes. Holding a key triggers its action once;
release it before pressing again. Buttons and hints show the active keys.
On X11, synthetic auto-repeat release/press pairs are normalized locally;
your system keyboard repeat settings are unchanged.
The examples below use the defaults, **F6** and **F8**.

Hotkey preferences are saved atomically in `~/.clickcue/hotkeys.json`, separate
from shareable profiles. Missing or invalid preferences use F6/F8. Choose keys
that do not conflict with shortcuts in your target app or operating system.

| Shortcut | Action |
|---|---|
| **F8** | Capture the cursor position after arming *Capture* |
| **F6** | Start or stop playback globally |
| **Double-click** a step | Edit its name, delay, coordinates, or wait-only mode |
| **Shift-click** | Select a range of steps |
| **Command-click** / **Ctrl-click** | Select separate steps on macOS / Windows and Linux |

1. **Choose the target window** (*Target window → Choose…*): pick the app the
   profile will click on. Its current position and size are recorded so the
   clicks stay anchored to it. If you skip this, the window under your first
   step is detected when you save.
2. **Capture a step**: click *Capture (F8)*, hover the cursor over the target
   point, and press **F8**. A dialog opens with the coordinates pre-filled;
   give the step a name and a delay. Don't move the target window while
   capturing — the anchor bounds are refreshed on each capture. For a pause
   without a click, use *Add* and enable *Wait only (no click)*; coordinates
   are ignored. A normal click step still clicks after its delay expires.
3. Add, edit (double-click), remove, and reorder (▲/▼) steps as needed.
   *Remove* deletes all selected steps. The arrows move each selected group one
   row, preserving order; groups already at that edge stay put while other
   groups can move. Moved steps stay selected and scroll into view.
   Select one or more steps and click *Copy* to duplicate them in order after
   the last selected step, keeping their names, coordinates, and delays. Use
   Shift-click to select a range, or Command-click (macOS) / Ctrl-click
   (Windows/Linux) to select separate steps. The new copies stay selected.
4. Choose *Loop forever* or a repeat count. Infinite playback requires at least
   one click or a positive delay; a sequence of only zero-delay waits is rejected.
   During fast playback, the status shows the latest progress update so controls
   and hotkeys stay responsive.
5. Press *▶ Start* or hit **F6**. ClickCue focuses the target window, restores
   its recorded position/size if it moved, then plays the sequence. **F6**
   stops playback, cancelling a pending click during guard focus retries or during
   the wait before pressing. If the mouse button is already held, stopping
   releases it promptly. A press already being sent to the OS may still occur.
   If the saved target is unavailable, click playback stays stopped: open the
   target app, reselect it with *Target window → Choose…*, or use *Clear* to
   explicitly play with saved absolute coordinates. This leaves your steps
   unchanged. Wait-only sequences can play even when the target is unavailable.
6. Save the sequence as a profile (*Save As…*) and reload it from the
   dropdown. *New* starts a fresh sequence.

Use **Undo** / **Redo** for the last 100 sequence edits (add, capture, edit,
copy, remove, and reorder, including multiple selected steps). Windows/Linux:
**Ctrl+Z** / **Ctrl+Y**; macOS: **Command+Z** / **Shift+Command+Z**. Shortcuts
leave text fields and dialogs to their native editing controls. Undo/redo is
unavailable during playback or while capture is armed. A new sequence edit clears
redo; New, opening another profile, and successful imports clear both histories.
Cancelled or invalid imports preserve history. Settings and target
window changes are outside this history. Undo/redo marks the profile unsaved,
even when the sequence returns to its saved content; save again to clear the mark.


## Profiles

Profiles are plain JSON files in `~/.clickcue/profiles/`. Save one with
**Save As…** and choose it from the profile dropdown the next time you need it.
Saves replace the profile file only after the new data has been written. If a
save or delete fails, ClickCue shows an error and keeps your current sequence;
failed saves leave your edits marked as unsaved so you can retry.

Use **Import…** to open an external JSON profile as an unsaved editor copy;
the filename need not match its profile name. ClickCue validates the complete
file before asking to discard unsaved edits. Use **Save** or **Save As…** to
choose a local name afterward; replacing an existing local file requires
confirmation. Import itself does not write any local profile.

Use **Export…** to write the current sequence, loop settings, and target window
to a chosen JSON file, including unsaved edits. Export leaves the active local
profile and unsaved marker unchanged. The native save dialog confirms replacing
an existing file; export uses the same atomic replacement as local saves, so a
failed write leaves the previous file intact.

The dropdown includes only valid, readable profiles whose names match their
filenames (using ClickCue's filename sanitization). If a file cannot be listed,
**Profile errors** shows its filename and reason, including invalid JSON, invalid
fields, encoding problems, or read permissions. Files are left unchanged. Correct
the reported files, then click **Profile errors** again to refresh; valid profiles
remain available while you repair the others.

<details>
<summary><strong>Profile format and window anchoring</strong></summary>

```json
{
  "schema_version": 1,
  "name": "Game A - farming",
  "loop_forever": true,
  "repetitions": 1,
  "target_window": { "app": "Some Game", "x": 40, "y": 60, "width": 1280, "height": 720 },
  "steps": [
    { "name": "Button 1", "x": 512, "y": 384, "delay": 1.5 },
    { "name": "Pause", "x": 0, "y": 0, "delay": 210, "wait_only": true }
  ]
}
```

`target_window` stores the window the profile is anchored to and its bounds at
capture time. Before playback ClickCue focuses that window and, if it has
moved or been resized, moves it back to those bounds (falling back to
proportional coordinate scaling when the OS refuses the move). Steps keep
absolute screen coordinates, valid when the window is at the recorded bounds.

`wait_only` defaults to `false` for existing steps. Set it to `true` for a
delay without a click; the step's name and coordinates do not imply a pause.
Wait-only steps do not check window coverage, and automatic target detection
uses the first click step. Blocked clicks report the coordinates checked and
the detected window so misplaced click points can be diagnosed.

Profile and step names must be nonempty. Delays must be finite, nonnegative
numbers; coordinates must be integers (negative coordinates are supported for
secondary screens). Repeat counts must be positive integers, and boolean
fields must use JSON `true` or `false`, not strings. Invalid profiles report
the offending field and, for steps, its position starting at 1.

Target bounds must either all be absent/null or include integer `x`, `y`,
`width`, and `height`, with positive dimensions. Legacy `target_app` profiles
remain supported; missing loop settings default to looping forever with a
repeat count of 1, and missing steps default to an empty list.
New saves and exports include `schema_version: 1`. Unversioned files and
version `0` remain supported with the same legacy defaults. The version must
be a nonnegative JSON integer (not a boolean); newer unsupported versions
are rejected with an error rather than interpreted as an older format.

</details>

## Platform support

Window anchoring and the click guard are best-effort safeguards, not a security
boundary. Each playback resolves one live native window and the guard checks
that window's identity, including against other windows of the same app/title.
Profiles save names and bounds, never native handles or PIDs. After loading,
macOS application names and Windows titles must match exactly one selectable
window; if missing or ambiguous, use **Choose…** to select it explicitly.
The picker lists individual windows with a title/ID and position.

Coverage checks include small, untitled, and nonstandard overlays, even when
those windows are excluded from the picker. Rectangle coverage is conservative:
transparent regions and invisible resize borders can block playback. Windows
requires the native point lookup's root window and PID to agree with rectangle
coverage; unknown or conflicting results block clicks. This does not guarantee
input delivery under mouse capture or application-defined hit testing. macOS
focus/move requires a unique public Accessibility bounds/title match; when
that mapping is unavailable it declines the action rather than moving another
window. Missing titles leave coincident macOS windows ambiguous. OS permissions,
disappearing/reused native IDs, and changes between checking and clicking remain best-effort limitations. Linux does not provide
these window safeguards. Test sequences in a harmless window first, and keep your start/stop hotkey available to stop playback.

| Platform | Status |
|---|---|
| macOS | Developed and tested here. Requires the permissions above. |
| Windows | Supported by design (pynput + Win32 window focus), but untested — feedback welcome. No special permissions needed. |
| Linux (X11) | Clicks and hotkeys work via pynput; window auto-focus is a no-op. Untested. |

Coordinates use screen points on macOS and physical pixels on Windows.
Windows requires Windows 10 (1703+) and per-monitor DPI awareness, established
before creating the GUI; window operations also use that coordinate context.
Older profiles captured with a different Windows DPI context may need recapture.
Window anchoring compensates for a moved or resized target
window; moving another app's window uses the Accessibility API on macOS and
`MoveWindow` on Windows.

## Contributing

Bug reports, fixes, and documentation improvements are welcome. Read the
[contribution guide](CONTRIBUTING.md) for setup, testing, and pull requests.
Please use synthetic profiles and remove private information from screenshots.

### Development checks

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

## Privacy & security

ClickCue has no telemetry, network client, or automatic updates. Profiles stay
on your computer in `~/.clickcue/profiles/` as plain JSON. They contain step
names, coordinates, delays, and the target application's name (or window title
on Windows) and bounds. Window titles and step names can reveal private
information. Review and sanitize profiles and screenshots before sharing them.
The global keyboard listener handles the chosen function keys (F6 and F8 by
default); ClickCue does not save typed text. Machine-local key preferences stay
in `~/.clickcue/hotkeys.json`. Installation and dependency/security checks
contact package services.

Report vulnerabilities through [GitHub's private reporting form](https://github.com/aserenaa/clickcue/security/advisories/new),
not a public issue. See [SECURITY.md](SECURITY.md) for stored data, permissions,
automation limits, and the reporting policy.

## Before publishing

Follow [docs/PUBLISHING.md](docs/PUBLISHING.md) to review Git's upload contents,
run secret and dependency scans, and enable GitHub's repository protections.
CI checks changes and runs weekly; it does not publish packages or releases.

## Disclaimer

Some games detect or ignore synthesized input, and automating clicks may
violate a game's terms of service. Use at your own risk, especially in online
games.

## License

[MIT](LICENSE) © Abraham Serena. Dependencies retain their own licenses;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
