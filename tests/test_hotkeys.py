import json
import queue
from enum import Enum
from threading import Lock, Thread
from unittest.mock import Mock

import pytest
from pynput import keyboard

from clickcue.gui import ClickCueApp
from clickcue.hotkeys import HotkeySettings, load_hotkeys, save_hotkeys


@pytest.fixture
def app(monkeypatch):
    # The dummy backend aliases every Key member; synthetic keys distinguish
    # physical keys without ever connecting to an OS input backend.
    monkeypatch.setattr(keyboard, "Key", Enum("Key", {f"f{i}": i for i in range(1, 13)}))
    controller = Mock()
    controller.position = (12, 34)
    monkeypatch.setattr("clickcue.gui.mouse.Controller", Mock(return_value=controller))
    app = ClickCueApp.__new__(ClickCueApp)
    app.events = queue.Queue()
    app.hotkeys = HotkeySettings()
    app._hotkey_lock = Lock()
    app._pressed_keys = set()
    return app


@pytest.mark.parametrize("key, event", [
    ("f6", ("hotkey_toggle",)), ("f8", ("hotkey_capture", 12, 34)),
])
def test_held_key_only_fires_once_until_released(app, key, event):
    key = getattr(keyboard.Key, key)

    def hold():
        for _ in range(20):
            app._on_key(key)

    listener = Thread(target=hold)
    listener.start()
    listener.join()
    assert list(app.events.queue) == [event]
    app._on_key_release(key)
    app._on_key(key)
    assert list(app.events.queue) == [event, event]


@pytest.mark.parametrize("payload", [
    None, "{", "[]", "null", '{}',
    '{"toggle": "F6", "capture": "F6"}',
    '{"toggle": "F13", "capture": "F8"}',
    '{"toggle": null, "capture": "F8"}',
])
def test_missing_or_invalid_preferences_use_backward_compatible_defaults(tmp_path, payload):
    path = tmp_path / "hotkeys.json"
    if payload is not None:
        path.write_text(payload, encoding="utf-8")
    assert load_hotkeys(path) == HotkeySettings("F6", "F8")


def test_preferences_round_trip_and_failed_replace_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "local" / "hotkeys.json"
    save_hotkeys(HotkeySettings("F1", "F12"), path)
    assert load_hotkeys(path) == HotkeySettings("F1", "F12")
    monkeypatch.setattr("clickcue.hotkeys.os.replace", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(PermissionError):
        save_hotkeys(HotkeySettings(), path)
    assert json.loads(path.read_text()) == {"toggle": "F1", "capture": "F12"}
    assert list(path.parent.iterdir()) == [path]


@pytest.fixture
def settings_app(app, monkeypatch, tmp_path):
    app.root = Mock()
    app.player = None
    app.capture_armed = False
    app.toggle_key = Mock(get=Mock(return_value="F2"))
    app.capture_key = Mock(get=Mock(return_value="F10"))
    app.start_button = Mock()
    app.capture_button = Mock()
    app.hotkey_hint = Mock()
    app.status_text = Mock()
    monkeypatch.setattr("clickcue.gui.save_hotkeys", lambda settings: save_hotkeys(
        settings, tmp_path / "hotkeys.json"))
    return app


def test_apply_persists_bindings_updates_labels_and_retains_held_state(settings_app, tmp_path):
    app = settings_app
    app._on_key(keyboard.Key.f2)  # held before assigning it
    app._apply_hotkeys()
    assert load_hotkeys(tmp_path / "hotkeys.json") == HotkeySettings("F2", "F10")
    app.start_button.configure.assert_called_once_with(text="▶ Start (F2)")
    app.capture_button.configure.assert_called_once_with(text="Capture (F10)")
    hint = app.hotkey_hint.configure.call_args.kwargs["text"]
    assert "F2 start/stop" in hint and "F10 capture" in hint
    app._on_key(keyboard.Key.f2)
    app._on_key(keyboard.Key.f6)
    assert app.events.empty()
    app._on_key_release(keyboard.Key.f2)
    app._on_key(keyboard.Key.f2)
    app._on_key(keyboard.Key.f10)
    assert list(app.events.queue) == [("hotkey_toggle",), ("hotkey_capture", 12, 34)]


@pytest.mark.parametrize("reason", ["conflict", "playing", "capturing", "save_error"])
def test_apply_rejection_keeps_active_bindings_and_preferences(settings_app, monkeypatch, tmp_path, reason):
    app = settings_app
    error = Mock()
    info = Mock()
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", error)
    monkeypatch.setattr("clickcue.gui.messagebox.showinfo", info)
    if reason == "conflict":
        app.capture_key.get.return_value = "F2"
    elif reason == "playing":
        app.player = Mock()
    elif reason == "capturing":
        app.capture_armed = True
    else:
        monkeypatch.setattr("clickcue.gui.save_hotkeys", Mock(side_effect=PermissionError("denied")))
    app._apply_hotkeys()
    assert app.hotkeys == HotkeySettings()
    assert not (tmp_path / "hotkeys.json").exists()
    app.start_button.configure.assert_not_called()
    assert error.call_count + info.call_count == 1
