import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp
from clickcue.hotkeys import HotkeySettings
from clickcue.models import TargetWindow
from clickcue.profiles import ProfileStore


@pytest.fixture
def app(tmp_path, monkeypatch, tk_runtime):
    root = tk.Toplevel(tk_runtime)
    root.withdraw()
    store = ProfileStore(tmp_path)
    monkeypatch.setattr("clickcue.gui.ProfileStore", lambda: store)
    monkeypatch.setattr("clickcue.gui.load_hotkeys", HotkeySettings)
    app = ClickCueApp(root, start_listener=False)
    # Opening diagnostics must not inspect the desktop, rescan disk, send input,
    # request permissions, or alter target windows.
    forbidden = Mock(side_effect=AssertionError("diagnostics performed a probe"))
    for module, names in (
        ("clickcue.gui", ("find_window", "list_windows", "window_info_at",
                          "accessibility_trusted")),
        ("clickcue.platform_support", ("focus_window", "move_window")),
    ):
        for name in names:
            monkeypatch.setattr(f"{module}.{name}", forbidden)
    monkeypatch.setattr(store, "list_names", forbidden)
    monkeypatch.setattr("clickcue.gui.mouse.Controller.__init__", forbidden)
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", Mock())
    yield app
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    root.destroy()


@pytest.mark.parametrize("platform,target,expected", [
    ("darwin", TargetWindow("Synthetic target", -10, 20, 800, 600),
     "800 x 600 at (-10, 20)"),
    ("win32", TargetWindow("Synthetic target"), "no saved bounds"),
    ("linux", None, "None saved (absolute coordinates)"),
])
def test_snapshot_reports_limits_and_retains_terminal_events(
        app, monkeypatch, platform, target, expected):
    monkeypatch.setattr("clickcue.gui.sys", SimpleNamespace(platform=platform, version="3.12"))
    app.target_window = target
    app.store.list_errors = {"Broken.json": "invalid JSON"}
    shown = Mock()
    monkeypatch.setattr("clickcue.gui.show_diagnostics", shown)
    app._handle_event(("blocked", "Synthetic step", "Covering window", 17, 29))
    app._handle_event(("error", "Synthetic backend failure"))
    app._handle_event(("stopped",))
    app.status_text.set("Loaded another profile")
    app._show_diagnostics()
    details = shown.call_args.args[1]
    assert f"Platform: {platform}" in details
    assert "Keyboard backend: pynput.keyboard." in details
    assert "Permissions: Unknown" in details
    assert expected in details
    assert "Last stop: Stopped — target not found at (17, 29)" in details
    assert "detected: Covering window" in details
    assert "Last playback/preparation error: Synthetic backend failure" in details
    assert "Current status: Loaded another profile" in details
    assert "Broken.json: invalid JSON" in details
    assert str(app.store.directory) in details
    assert ("Unsupported on this platform" in details) == (platform == "linux")
    assert app.target_window == target
    assert not app.dirty


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def test_diagnostics_button_opens_selectable_read_only_keyboard_dismissible_panel(app):
    button = next(widget for widget in descendants(app.root)
                  if widget.winfo_class() == "TButton"
                  and widget.cget("text") == "Diagnostics…")
    button.invoke()
    panel = next(widget for widget in app.root.winfo_children()
                 if isinstance(widget, tk.Toplevel))
    text = next(widget for widget in descendants(panel) if isinstance(widget, tk.Text))
    original = text.get("1.0", "end-1c")
    assert "None recorded this session" in original
    assert "Mouse backend:" in original
    text.insert("1.0", "unwanted edit")
    text.delete("1.0", "end")
    assert text.get("1.0", "end-1c") == original
    assert app.root.grab_current() is None
    # Exercise Tk bindings without changing OS focus on the hidden test window.
    text.tk.call("catch", text.bind("<Control-a>").replace("%#", "0"))
    assert text.get("sel.first", "sel.last") == original
    panel.tk.call("catch", panel.bind("<Escape>").replace("%#", "0"))
    assert not panel.winfo_exists()
