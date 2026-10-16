import queue
import threading
from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp
from clickcue.hotkeys import HotkeySettings
from clickcue.models import Step, TargetWindow


@pytest.fixture
def app(monkeypatch):
    ui_thread = threading.current_thread()

    def ui_only(*args, **kwargs):
        assert threading.current_thread() is ui_thread

    app = ClickCueApp.__new__(ClickCueApp)
    app.hotkeys = HotkeySettings("F2", "F10")
    app.player = None
    app._preparation = None
    app._closed = False
    app.steps = [Step("Click", 17, 29, 0)]
    app.target_window = TargetWindow("Target")
    app.loop_forever = Mock()
    app.loop_forever.get.return_value = False
    app._repetition_count = Mock(return_value=1)
    app.loop_forever.get.side_effect = lambda: (ui_only(), app.loop_forever.get.return_value)[1]
    app._repetition_count.side_effect = lambda: (ui_only(), app._repetition_count.return_value)[1]
    app.events = queue.Queue()
    app.root = Mock()
    app.root.after.side_effect = ui_only
    app.start_button = Mock()
    app.start_button.configure.side_effect = ui_only
    app.status_text = Mock()
    app.status_text.set.side_effect = ui_only
    app.dirty = False
    app.keyboard_listener = None
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", Mock(return_value=True))
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", Mock(side_effect=ui_only))
    def new_player(*args, **kwargs):
        ui_only()
        return Mock()

    monkeypatch.setattr("clickcue.gui.Player", Mock(side_effect=new_player))
    return app


@pytest.mark.parametrize("accept", [False, True])
def test_missing_target_requires_gui_decision_before_start(app, monkeypatch, accept):
    monkeypatch.setattr("clickcue.preparation.find_window", Mock(return_value=None))
    ui_thread = threading.current_thread()

    def decide(*args):
        assert threading.current_thread() is ui_thread
        return accept

    decision = Mock(side_effect=decide)
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", decision)
    app._toggle_playback()
    app._handle_event(app.events.get(timeout=2))

    decision.assert_called_once()
    assert "saved absolute coordinates" in decision.call_args.args[1]
    if accept:
        app.player.start.assert_called_once()
        app.status_text.set.assert_called_with("Playing (target window not found)…")
    else:
        assert app.player is None
        app.status_text.set.assert_called_with("Stopped")


@pytest.mark.parametrize("action", ["stop", "close", "error", "snapshot"])
def test_blocked_preparation_keeps_ui_responsive_and_discards_cancelled_results(
        app, monkeypatch, action):
    entered = threading.Event()
    release = threading.Event()
    ui_thread = threading.current_thread()
    calls = []

    def find_window(name):
        assert threading.current_thread() is not ui_thread
        calls.append(name)
        entered.set()
        assert release.wait(2), "test did not release blocked OS call"
        if action == "error" and len(calls) == 1:
            raise RuntimeError("OS failure")
        return None

    monkeypatch.setattr("clickcue.preparation.find_window", find_window)
    player = Mock()
    monkeypatch.setattr("clickcue.gui.Player", player)
    try:
        app._toggle_playback()
        assert entered.wait(1)
        assert app.player is None
        app.start_button.configure.assert_called_with(text="■ Stop (F2)")
        app._poll_events()  # returns and reschedules while the OS call is blocked
        app.root.after.assert_called()
        if action == "close":
            app._on_close()
            app.root.destroy.assert_called_once()
        elif action == "stop":
            app.events.put(("hotkey_toggle",))
            app._poll_events()
            app._toggle_playback()  # cannot overlap the outstanding OS call
            assert calls == ["Target"]
        elif action == "snapshot":
            app.steps[0].x = 999
            app.steps.clear()
            app.target_window.app = "Changed"
            app.loop_forever.get.return_value = True
            app._repetition_count.return_value = 99
        release.set()
        completion = app.events.get(timeout=2)
        if action == "close":
            app._handle_event(completion)
        else:
            app.events.put(completion)
            app.root.after.reset_mock()
            app._poll_events()
            app.root.after.assert_called_once()
        if action == "snapshot":
            args = player.call_args.args
            assert args[:3] == ([Step("Click", 17, 29, 0)], False, 1)
            assert player.call_args.kwargs["guard"].__self__.anchor.app == "Target"
        else:
            player.assert_not_called()
        if action in ("stop", "error"):
            if action == "error":
                from clickcue.gui import messagebox
                messagebox.showerror.assert_called_once()
            app._toggle_playback()
            app._handle_event(app.events.get(timeout=2))
            player.assert_called_once()
    finally:
        release.set()
