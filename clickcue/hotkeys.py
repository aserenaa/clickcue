"""Machine-local function key preferences, separate from shared profiles."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pynput import keyboard

FUNCTION_KEYS = tuple(f"F{number}" for number in range(1, 13))
DEFAULT_PATH = Path.home() / ".clickcue" / "hotkeys.json"


def create_hotkey_listener(on_press, on_release):
    # pynput's public stubs omit the backend-specific event dispatch methods.
    listener_type: Any = keyboard.Listener
    if listener_type.__module__ != "pynput.keyboard._xorg":
        return listener_type(on_press=on_press, on_release=on_release)

    class XorgHotkeyListener(listener_type):
        # RECORD reports repeat release/press pairs even though XKB offers
        # detectable repeat for per-client delivered events. Defer only function
        # key releases until the next device event, including across RECORD replies.
        _pending_release = None

        def _handle_message(self, display, event, *args):
            press, release = self._EVENTS
            if self._pending_release is not None:
                previous_display, previous, previous_args = self._pending_release
                self._pending_release = None
                if not (event.type == press and event.detail == previous.detail
                        and event.time == previous.time):
                    super()._handle_message(previous_display, previous, *previous_args)

            if event.type == release:
                try:
                    key = self._event_to_key(display, event)
                except IndexError:
                    key = None
                if key in (getattr(keyboard.Key, name.lower()) for name in FUNCTION_KEYS):
                    self._pending_release = (display, event, args)
                    return
            super()._handle_message(display, event, *args)

    return XorgHotkeyListener(on_press=on_press, on_release=on_release)


@dataclass(frozen=True)
class HotkeySettings:
    toggle: str = "F6"
    capture: str = "F8"

    def __post_init__(self) -> None:
        if self.toggle not in FUNCTION_KEYS or self.capture not in FUNCTION_KEYS:
            raise ValueError("Choose function keys from F1 to F12.")
        if self.toggle == self.capture:
            raise ValueError("Start/stop and capture must use different keys.")


def load_hotkeys(path: Path = DEFAULT_PATH) -> HotkeySettings:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return HotkeySettings(data["toggle"], data["capture"])
    except (OSError, ValueError, KeyError, TypeError):
        return HotkeySettings()


def save_hotkeys(settings: HotkeySettings, path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".hotkeys-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(asdict(settings), stream, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
