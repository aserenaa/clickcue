import queue
import threading
import tkinter as tk
from copy import deepcopy
from unittest.mock import Mock

import pytest

from clickcue.gui import EVENT_POLL_MS, ClickCueApp
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
    app._closing = False
    app._preview_active = False
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
    app._mark_dirty = Mock()
    app._update_target_text = Mock()
    for name in ("find_window", "focus_window_at", "focus_window", "window_info_at"):
        monkeypatch.setattr(f"clickcue.preparation.{name}", Mock(return_value=None))
    app.keyboard_listener = None
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", Mock(return_value=True))
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", Mock(side_effect=ui_only))
    def new_player(*args, **kwargs):
        ui_only()
        return Mock()

    monkeypatch.setattr("clickcue.gui.Player", Mock(side_effect=new_player))
    return app


def test_missing_target_blocks_playback_without_changing_saved_state(monkeypatch, app):
    steps = deepcopy(app.steps)
    anchor = deepcopy(app.target_window)
    decision = Mock(return_value=True)
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", decision)
    warning = Mock()
    monkeypatch.setattr("clickcue.gui.messagebox.showwarning", warning)
    player = Mock()
    monkeypatch.setattr("clickcue.gui.Player", player)

    app._toggle_playback()
    completion = app.events.get(timeout=2)
    app.events.put(completion)
    app._poll_events()

    assert app.player is None
    player.assert_not_called()
    decision.assert_not_called()
    warning.assert_called_once()
    assert "Target" in warning.call_args.args[1]
    for action in ("Open", "Choose", "Clear"):
        assert action in warning.call_args.args[1]
    assert "target unavailable" in app.status_text.set.call_args.args[0].lower()
    assert "target unavailable" in app._last_stop.lower()
    assert app.steps == steps
    assert app.target_window == anchor
    app._mark_dirty.assert_not_called()
    app._update_target_text.assert_not_called()
    assert app._preparation is None
    app.start_button.configure.assert_called_with(
        text=f"\u25b6 Start ({app.hotkeys.toggle})", state="normal")


@pytest.mark.parametrize("wait_only", [False, True])
def test_clear_anchor_or_wait_only_sequence_can_start(monkeypatch, app, wait_only):
    if wait_only:
        app.steps = [Step("Pause", 17, 29, 1, wait_only=True)]
    else:
        app._clear_target_window()
    warning = Mock()
    monkeypatch.setattr("clickcue.gui.messagebox.showwarning", warning)
    player = Mock()
    monkeypatch.setattr("clickcue.gui.Player", player)

    app._toggle_playback()
    completion = app.events.get(timeout=2)
    app.events.put(completion)
    app._poll_events()

    warning.assert_not_called()
    assert app.player is player.return_value
    app.player.start.assert_called_once()
    assert player.call_args.args[0] == app.steps
    assert app.status_text.set.call_args.args[0] == (
        "Playing waits…" if wait_only else "Playing…")


@pytest.mark.parametrize("action", ["stop", "close", "error", "snapshot"])
def test_blocked_preparation_keeps_ui_responsive_and_discards_cancelled_results(
        app, monkeypatch, action):
    entered = threading.Event()
    release = threading.Event()
    ui_thread = threading.current_thread()
    calls = []

    def find_window(name):
        assert threading.current_thread() is not ui_thread
        calls.append(name.app)
        entered.set()
        assert release.wait(2), "test did not release blocked OS call"
        if action == "error" and len(calls) == 1:
            raise RuntimeError("OS failure")
        return TargetWindow(name.app)

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
                assert app._last_error == "OS failure"
            app._toggle_playback()
            completion = app.events.get(timeout=2)
            app.events.put(completion)
            app._poll_events()
            player.assert_called_once()
    finally:
        release.set()


@pytest.fixture(scope="module")
def tk_runtime():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def tk_root(app, tk_runtime):
    # Keep one Tcl/Tk interpreter; close each app window independently.
    root = tk.Toplevel(tk_runtime)
    root.withdraw()
    app.root = root
    yield root
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    if not app._closed:
        app.dirty = False
        app._on_close()


def test_stop_during_preparation_error_dialog_does_not_restart(app, monkeypatch, tk_root):
    root = tk_root
    find = Mock(side_effect=[RuntimeError("OS failure"), TargetWindow("Target")])
    monkeypatch.setattr("clickcue.preparation.find_window", find)
    player = Mock()
    monkeypatch.setattr("clickcue.gui.Player", player)
    dialogs = []

    def show_error(*args):
        # Use a real Tk modal loop and enqueue the listener's event at dismissal;
        # production polling and completion handling retain ownership of Stop.
        dialog = tk.Toplevel(root)
        dialogs.append(dialog)
        dialog.grab_set()

        def dismiss():
            app.events.put(("hotkey_toggle",))
            dialog.destroy()

        root.after(3 * EVENT_POLL_MS, dismiss)
        root.wait_window(dialog)

    monkeypatch.setattr("clickcue.gui.messagebox.showerror", show_error)
    app._toggle_playback()
    app._poll_events()
    root.after(1000, root.quit)
    root.mainloop()
    assert len(dialogs) == 1
    assert find.call_count == 1
    player.assert_not_called()
    assert app._preparation is None


@pytest.mark.parametrize("accept_close", [False, True])
def test_dirty_close_cancels_preparation_before_confirmation(
        app, monkeypatch, tk_root, accept_close):
    entered = threading.Event()
    release = threading.Event()

    def find_window(name):
        entered.set()
        assert release.wait(2), "test did not release blocked OS call"
        return TargetWindow(name.app)

    monkeypatch.setattr("clickcue.preparation.find_window", find_window)
    player = Mock()
    monkeypatch.setattr("clickcue.gui.Player", player)
    closed_during_confirmation = []
    cleanup_seen = []

    def confirm(*args):
        dialog = tk.Toplevel(tk_root)
        dialog.grab_set()

        def dismiss():
            # Also cover a toggle queued too late for the modal's final poll.
            app.events.put(("hotkey_toggle",))
            dialog.destroy()

        def after_cleanup():
            if app._preparation is not None:
                tk_root.after(EVENT_POLL_MS, after_cleanup)
                return
            cleanup_seen.append(True)
            app.events.put(("hotkey_toggle",))
            tk_root.after(500, dismiss)

        tk_root.after(0, release.set)
        tk_root.after(EVENT_POLL_MS, after_cleanup)
        deadline = tk_root.after(2000, dialog.destroy)
        tk_root.wait_window(dialog)
        tk_root.after_cancel(deadline)
        closed_during_confirmation.append(app._closed)
        return accept_close

    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", confirm)
    try:
        app.dirty = True
        app._toggle_playback()
        assert entered.wait(1)
        app._poll_events()
        app._on_close()  # the real dirty check and confirmation handler
        assert cleanup_seen == [True]
        assert closed_during_confirmation == [False]
        player.assert_not_called()
        assert app._closed is accept_close
        if not accept_close:
            tk_root.after(500, tk_root.quit)
            tk_root.mainloop()
            player.assert_not_called()  # queued modal toggles must not become Start
            assert not app._closing
            assert app.dirty
            assert app._preparation is None
            app.start_button.configure.assert_called_with(
                text=f"\u25b6 Start ({app.hotkeys.toggle})", state="normal")
            app._toggle_playback()
            tk_root.after(1000, tk_root.quit)
            tk_root.mainloop()
            player.assert_called_once()
            app.player.start.assert_called_once()
    finally:
        release.set()
