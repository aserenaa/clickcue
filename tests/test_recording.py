"""Recording's primary contract owner: synthetic hooks, real worker/lifecycle.

Authoring gates: these tests observe emitted Steps and session termination;
credible failures are timing/order loss, accepting unsupported input/targets,
late callbacks, and missing listener cleanup. Existing playback tests cannot
exercise the new input boundary. Only native hooks/lookups/time are replaced;
the production worker owns admission, cutoff, buffer, and completion.
The real-Tk groups independently own Keep/Discard history, competing editor
callbacks, close/modals, keyboard routing, blocked-worker responsiveness and
review transport. None is a second timing/native-identity matrix, and no
production API exists solely to support these tests. Footer/font geometry
remains owned by test_gui_accessibility's existing layout contract.
"""

import gc
import os
import queue
import tkinter as tk
from copy import deepcopy
from dataclasses import replace
from enum import Enum
from threading import Event, Thread
from unittest.mock import Mock

import pytest
from pynput import keyboard, mouse

from clickcue.gui import ClickCueApp
from clickcue.hotkeys import HotkeySettings
from clickcue.models import Step, TargetWindow
from clickcue.profiles import ProfileStore, read_profile
from clickcue.recording import ClickRecording


class SyntheticListener(Thread):
    instances = None

    def __init__(self, **callbacks):
        super().__init__(daemon=True)
        self.callbacks = callbacks
        self.stopped = Event()
        self.failure = None
        self.instances.append(self)

    def _mark_ready(self):
        pass

    def run(self):
        self._mark_ready()
        self.stopped.wait(5)

    def stop(self):
        self.stopped.set()

    def join(self, timeout=None):
        super().join(timeout)
        if self.failure is not None:
            raise self.failure


@pytest.fixture
def hooks(monkeypatch):
    target = TargetWindow("Harmless", 0, 0, 200, 200, native_id=10, pid=123456)
    listeners = []
    monkeypatch.setattr(SyntheticListener, "instances", listeners)
    monkeypatch.setattr("clickcue.recording.mouse.Listener", SyntheticListener)
    monkeypatch.setattr("clickcue.recording.sys.platform", "win32")
    monkeypatch.setattr("clickcue.recording.find_window", lambda _: replace(target))
    monkeypatch.setattr("clickcue.recording.window_info_at", lambda *a, **kw: replace(target))
    sessions = []

    def start():
        events = queue.Queue()
        recording = ClickRecording(target, events)
        sessions.append(recording)
        recording.start()
        recording.begin()
        assert events.get(timeout=2) == ("record_started", recording)
        return recording, listeners[-1], events

    yield target, start
    for session in sessions:
        session.stop()
        session.join(2)
        assert not session.is_alive()


def click(listener, x=20, y=30):
    listener.callbacks["on_click"](x, y, mouse.Button.left, True)
    listener.callbacks["on_click"](x, y, mouse.Button.left, False)


@pytest.mark.parametrize("ending", ["normal", "drag", "release_drag", "right", "scroll"])
def test_completed_prefix_timing_and_unsupported_actions(hooks, monkeypatch, ending):
    _, start = hooks
    recording, listener, events = start()
    times = iter([10, 12, 15.5, 16, 20, 21])
    monkeypatch.setattr("clickcue.recording.monotonic", lambda: next(times))
    click(listener)
    listener.callbacks["on_click"](40, 50, mouse.Button.left, True)
    listener.callbacks["on_move"](42, 52)  # normal jitter, no extra event/Step
    listener.callbacks["on_click"](41, 51, mouse.Button.left, False)
    if ending == "normal":
        recording.stop()
    elif ending in ("drag", "release_drag"):
        listener.callbacks["on_click"](60, 70, mouse.Button.left, True)
        if ending == "drag":
            listener.callbacks["on_move"](65, 70)
            listener.callbacks["on_move"](60, 70)  # returning does not undo a drag
        listener.callbacks["on_click"](65, 70, mouse.Button.left, False)
    elif ending == "right":
        listener.callbacks["on_click"](60, 70, mouse.Button.right, True)
    elif ending == "scroll":
        listener.callbacks["on_scroll"](60, 70, 0, 1)
    assert recording.finished.wait(2)
    assert events.get(timeout=2) == ("record_finished", recording)
    assert recording.steps == [Step("Recorded click 1", 20, 30, 0),
                               Step("Recorded click 2", 40, 50, 3.5)]
    assert (recording.reason is None) == (ending == "normal")
    assert listener.stopped.is_set()


@pytest.mark.parametrize("bad", ["covered", "own", "moved", "replacement", "missing"])
def test_observation_rejects_wrong_own_or_changed_target(hooks, monkeypatch, bad):
    target, start = hooks
    recording, listener, _ = start()
    if bad in ("covered", "own"):
        covering = replace(target, native_id=20, pid=os.getpid() if bad == "own" else 654321)

        def at_point(x, y, *, include_own=False):
            # Model the macOS default: looking through own windows is unsafe.
            return covering if include_own else target

        monkeypatch.setattr("clickcue.recording.window_info_at", at_point)
    else:
        changed = (None if bad == "missing" else replace(target, x=1) if bad == "moved"
                   else replace(target, native_id=99))
        monkeypatch.setattr("clickcue.recording.find_window", lambda _: changed)
    click(listener)
    assert recording.finished.wait(2)
    assert recording.steps == []
    assert recording.reason
    assert ("ClickCue" in recording.reason) == (bad == "own")


@pytest.mark.parametrize("bad", ["platform", "missing", "identity", "own", "bounds"])
def test_arm_requires_supported_unique_live_identity_and_saved_bounds(hooks, monkeypatch, bad):
    target, _ = hooks
    if bad == "platform":
        monkeypatch.setattr("clickcue.recording.sys.platform", "linux")
    else:
        current = {"missing": None,
                   "identity": replace(target, native_id=None),
                   "own": replace(target, pid=os.getpid()), "bounds": replace(target, width=201)}[bad]
        monkeypatch.setattr("clickcue.recording.find_window", lambda _: current)
    recording = ClickRecording(target, queue.Queue())
    recording.start()
    recording.join(2)
    assert recording.finished.is_set()
    assert recording.reason
    assert recording.steps == []
    assert SyntheticListener.instances == []


@pytest.mark.parametrize("failure", [False, True])
def test_stop_cutoff_during_lookup_and_listener_failure_cleanup(hooks, monkeypatch, failure):
    target, start = hooks
    recording, listener, _ = start()
    entered, release = Event(), Event()

    def observe(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return replace(target)

    monkeypatch.setattr("clickcue.recording.window_info_at", observe)
    click(listener)
    try:
        assert entered.wait(2)
        recording.stop()
        # Late callbacks, including a complete click and scroll, cannot add data.
        click(listener, 80, 90)
        listener.callbacks["on_scroll"](80, 90, 0, 1)
        if failure:
            listener.failure = RuntimeError("synthetic hook failure")
        release.set()
        assert recording.finished.wait(2)
        assert recording.steps == [Step("Recorded click 1", 20, 30, 0)]
        assert listener.stopped.is_set()
        if failure:
            assert "synthetic hook failure" in recording.reason
        else:
            assert recording.reason is None
    finally:
        release.set()
    second, fresh, _ = start()
    assert fresh is not listener
    click(listener, 90, 90)
    click(fresh, 10, 10)
    second.stop()
    assert second.finished.wait(2)
    assert second.steps == [Step("Recorded click 1", 10, 10, 0)]


@pytest.mark.parametrize("problem", ["startup", "unexpected_exit", "queue_limit", "click_limit"])
def test_listener_failure_and_resource_limits_keep_completed_prefix(hooks, monkeypatch, problem):
    target, start = hooks
    if problem == "startup":
        monkeypatch.setattr(SyntheticListener, "run", lambda self: None)
        recording = ClickRecording(target, queue.Queue())
        recording.start()
        recording.begin()
        recording.join(2)
        assert recording.finished.is_set()
        assert "ready" in recording.reason
        assert recording.steps == []
        assert SyntheticListener.instances[0].stopped.is_set()
        return
    if problem == "queue_limit":
        monkeypatch.setattr("clickcue.recording.MAX_PENDING_EVENTS", 2)
    if problem == "click_limit":
        monkeypatch.setattr("clickcue.recording.MAX_RECORDED_CLICKS", 1)
    recording, listener, _ = start()
    entered, release = Event(), Event()

    def observe(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return target

    monkeypatch.setattr("clickcue.recording.window_info_at", observe)
    click(listener)
    try:
        assert entered.wait(2)
        if problem == "unexpected_exit":
            listener.stop()
        elif problem == "queue_limit":
            for _ in range(4):
                click(listener)
        release.set()
        assert recording.finished.wait(2)
        assert recording.steps[0] == Step("Recorded click 1", 20, 30, 0)
        assert recording.reason
        assert len(recording.steps) <= 2
        assert listener.stopped.is_set()
    finally:
        release.set()


@pytest.fixture
def editor(hooks, monkeypatch, tmp_path, tk_runtime):
    target, _ = hooks
    monkeypatch.setattr(keyboard, "Key", Enum("Key", {**{f"f{i}": i for i in range(1, 13)},
                                                     "shift": 20}))
    monkeypatch.setattr("clickcue.gui.ProfileStore", lambda: ProfileStore(tmp_path))
    monkeypatch.setattr("clickcue.gui.load_hotkeys", lambda: HotkeySettings("F2", "F9"))
    for name in ("mouse.Controller", "Player", "PlaybackPreparation", "create_hotkey_listener"):
        monkeypatch.setattr(f"clickcue.gui.{name}", Mock(side_effect=AssertionError(name)))
    for name in ("showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(f"clickcue.gui.messagebox.{name}", Mock())
    root = tk.Toplevel(tk_runtime)
    root.withdraw()
    app = ClickCueApp(root, start_listener=False)
    app.target_window = target
    app.steps = [Step("Original", 1, 2, 0)]
    app._refresh_table(select=0)
    app._copy_step()
    app._copy_step()
    app._undo()  # Both histories contain real edits.
    app.dirty = False
    try:
        yield app
    finally:
        try:
            if app._recording is not None:
                app._recording.stop()
                app._recording.join(2)
                assert not app._recording.is_alive()
        finally:
            for timer in root.tk.call("after", "info"):
                root.after_cancel(timer)
            if root.winfo_exists():
                root.destroy()


def wait_ui(app, predicate):
    done = tk.BooleanVar(master=app.root)
    pending = None

    def check():
        nonlocal pending
        if predicate():
            done.set(True)
        else:
            pending = app.root.after(5, check)

    deadline = app.root.after(2000, done.set, False)
    pending = app.root.after(0, check)
    try:
        app.root.wait_variable(done)
        assert done.get(), app.status_text.get()
    finally:
        app.root.after_cancel(deadline)
        app.root.after_cancel(pending)
        # The recursive callback otherwise forms check -> closure -> check,
        # retaining done until GC on an arbitrary (possibly recording) thread.
        # Dispose the Tk variable here, including on timeout or assertion failure.
        check = None


def begin_ui(app):
    app.record_button.invoke()
    assert "F9 begins" in app.status_text.get() and "F2 stops" in app.status_text.get()
    app._on_key(keyboard.Key.f9)
    wait_ui(app, lambda: "Recording plain" in app.status_text.get())
    return app._recording, SyntheticListener.instances[-1]


@pytest.mark.parametrize("decision", ["keep", "discard", "empty"])
def test_tk_keep_is_one_edit_and_discard_preserves_both_histories(editor, decision):
    # UI owns the transaction/selection contract; worker tests cannot prove it.
    app = editor
    before = app._sequence_snapshot()
    histories = deepcopy((app._undo_history, app._redo_history))
    anchor = replace(app.target_window)
    recording, listener = begin_ui(app)
    if decision != "empty":
        click(listener)
        click(listener, 40, 50)
    app._on_key(keyboard.Key.f2)
    app._on_key(keyboard.Key.f2)  # Held Stop must never become playback Start.
    wait_ui(app, lambda: app.keep_recording_button.instate(["!disabled"]))
    assert app._sequence_snapshot() == before
    assert (app._undo_history, app._redo_history) == histories
    assert not app.dirty
    assert app.start_button.instate(["disabled"])
    expected_count = 0 if decision == "empty" else 2
    assert f"{expected_count} completed" in app.status_text.get()
    status = app.status_text.get()
    app._handle_event(("record_stopping", recording))
    assert app.status_text.get() == status
    if decision == "discard":
        app.discard_recording_button.invoke()
    else:
        app.keep_recording_button.invoke()
    assert app._recording is None
    assert app.target_window == anchor
    if decision == "keep":
        assert [s.name for s in app.steps[len(before[0]):]] == ["Recorded click 1", "Recorded click 2"]
        assert [app.tree.index(row) for row in app.tree.selection()] == [len(before[0]), len(before[0]) + 1]
        assert len(app._undo_history) == len(histories[0]) + 1
        assert app._redo_history == []
        assert app.dirty
        app._undo()
        assert app._sequence_snapshot() == before
    else:
        assert app._sequence_snapshot() == before
        assert (app._undo_history, app._redo_history) == histories
        assert not app.dirty
    # A queued event from the old generation cannot alter current status/state.
    status = app.status_text.get()
    app._handle_event(("record_finished", recording))
    assert app.status_text.get() == status
    assert app.start_button.instate(["!disabled"])


@pytest.mark.parametrize("phase", ["armed", "active", "review"])
def test_tk_recording_lifecycle_blocks_competing_mutations(editor, phase):
    app = editor
    before = app._sequence_snapshot()
    histories = deepcopy((app._undo_history, app._redo_history))
    settings = (app.loop_forever.get(), app.repetitions.get())
    if phase == "armed":
        app.record_button.invoke()
    else:
        _, listener = begin_ui(app)
        click(listener)
        if phase == "review":
            app._on_key(keyboard.Key.f2)
            wait_ui(app, lambda: app.keep_recording_button.instate(["!disabled"]))
    for callback in (app._arm_capture, app._add_step, app._edit_step, app._copy_step,
                     app._remove_step, app._undo, app._redo, app._new_profile,
                     app._open_selected_profile, app._save_profile, app._save_profile_as,
                     app._import_profile, app._delete_profile, app._choose_target_window,
                     app._clear_target_window, app._run_selected, app._apply_hotkeys,
                     app._preview_sequence, app._arm_recording):
        callback()
    app._move_step(1)
    app.loop_forever.set(not settings[0])
    app.repetitions.set("87")
    assert (app.loop_forever.get(), app.repetitions.get()) == settings
    assert app._sequence_snapshot() == before
    assert (app._undo_history, app._redo_history) == histories
    assert not app.capture_armed and not app.dirty
    assert app.player is None and app._preparation is None
    assert app.record_button.instate(["disabled"])
    assert all(w.instate(["disabled"]) for w in app._editing_controls)
    # Read-only Diagnostics stays available through every recording phase.
    app._show_diagnostics()
    panels = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]
    assert len(panels) == 1 and app.root.grab_current() is None
    panels[0].destroy()


@pytest.mark.parametrize("cancel_at", ["recording", "unsaved"])
def test_tk_close_stops_before_confirmation_and_cancel_never_resumes(editor, monkeypatch, cancel_at):
    app = editor
    before = app._sequence_snapshot()
    recording, listener = begin_ui(app)
    click(listener)
    app.dirty = cancel_at == "unsaved"
    confirmations = []

    def confirm(title, *_args, **_kwargs):
        confirmations.append(title)
        # Actual callback admission has already closed before either modal.
        click(listener, 90, 90)
        app._on_key_release(keyboard.Key.f9)
        app._on_key(keyboard.Key.f9)
        app._on_key(keyboard.Key.f2)
        app._handle_event(("hotkey_toggle",))
        return title == "Discard recording?" and cancel_at == "unsaved"

    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", confirm)
    app._on_close()
    wait_ui(app, lambda: not app._closing and app.keep_recording_button.instate(["!disabled"]))
    assert confirmations == (["Discard recording?"] if cancel_at == "recording"
                             else ["Discard recording?", "Unsaved changes"])
    assert not app._closed
    assert app._sequence_snapshot() == before
    assert recording.steps == [Step("Recorded click 1", 20, 30, 0)]
    assert app._recording is recording


@pytest.mark.parametrize("input_kind", ["text", "other_function", "release", "held_modifier"])
def test_tk_keyboard_stops_without_retaining_text_or_accepting_modified_clicks(editor, input_kind):
    app = editor
    if input_kind == "held_modifier":
        app._on_key(keyboard.Key.shift)
        app.record_button.invoke()
        assert app._recording is None
        app._on_key_release(keyboard.Key.shift)
    recording, listener = begin_ui(app)
    click(listener)
    key = keyboard.Key.f3 if input_kind == "other_function" else keyboard.KeyCode.from_char("q")
    if input_kind == "release":
        app._on_key_release(key)
    else:
        app._on_key(key)
    click(listener, 90, 90)
    wait_ui(app, lambda: app.keep_recording_button.instate(["!disabled"]))
    assert recording.steps == [Step("Recorded click 1", 20, 30, 0)]
    assert "Partial recording" in app.status_text.get()
    assert "Keyboard input" in recording.reason
    assert app._pressed_keys == {keyboard.Key.f9}


@pytest.mark.parametrize("blocked_at", ["lookup", "cleanup", "readiness"])
def test_tk_stop_is_responsive_while_native_recording_work_is_blocked(editor, monkeypatch, blocked_at):
    app = editor
    entered, release = Event(), Event()
    if blocked_at == "readiness":
        # Isolate this helper's ownership: a later worker allocation/collection
        # must not inherit its Tk variable destructor. No global GC policy change.
        gc.collect()
        wait_ui(app, lambda: True)
        original = SyntheticListener._mark_ready

        def ready(listener):
            gc.collect()
            entered.set()
            assert release.wait(2)
            original(listener)

        monkeypatch.setattr(SyntheticListener, "_mark_ready", ready)
        app.record_button.invoke()
        app._on_key(keyboard.Key.f9)
        # Process the queued begin on Tk, without waiting on listener readiness.
        app._poll_events()
        recording = app._recording
    else:
        recording, listener = begin_ui(app)
        if blocked_at == "lookup":
            target = replace(app.target_window)

            def lookup(*args, **kwargs):
                entered.set()
                assert release.wait(2)
                return target

            monkeypatch.setattr("clickcue.recording.window_info_at", lookup)
            click(listener)
        else:
            original = listener.stop

            def stop():
                entered.set()
                assert release.wait(2)
                original()

            monkeypatch.setattr(listener, "stop", stop)
            app.start_button.invoke()
    try:
        assert entered.wait(1)
        app.start_button.invoke()
        heartbeat = []
        app.root.after(0, lambda: heartbeat.append(True))
        app.root.update()
        assert heartbeat == [True]
        assert not recording.finished.is_set()
        assert app.record_button.instate(["disabled"])
        app._show_diagnostics()
        panel = next(w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel))
        assert app.root.grab_current() is None
        panel.destroy()
        release.set()
        wait_ui(app, lambda: app.keep_recording_button.instate(["!disabled"]))
        assert recording.finished.is_set()
    finally:
        release.set()


@pytest.mark.parametrize("operation", ["export", "profile_errors"])
def test_tk_readonly_modals_preserve_recording_and_consume_start_hotkeys(editor, monkeypatch, tmp_path, operation):
    app = editor
    before = app._sequence_snapshot()
    histories = deepcopy((app._undo_history, app._redo_history))
    app.record_button.invoke()
    recording = app._recording
    destination = tmp_path / "export.json"
    observed = []
    app._on_key(keyboard.Key.f9)  # callback already queued before modal opens
    app._on_key_release(keyboard.Key.f9)

    def modal(*args, **kwargs):
        observed.append(True)
        dialog = tk.Toplevel(app.root)
        dialog.grab_set()
        app._on_key(keyboard.Key.f9)
        app._poll_events()
        app._on_key_release(keyboard.Key.f9)
        app._on_key(keyboard.Key.f9)  # pending at dismissal
        app.root.after(0, dialog.destroy)
        app.root.wait_window(dialog)
        return str(destination)

    if operation == "export":
        monkeypatch.setattr("clickcue.gui.filedialog.asksaveasfilename", modal)
        export = next(w for w in app.import_button.master.winfo_children()
                      if str(w.cget("text")).startswith("Export"))
        export.invoke()
        assert read_profile(destination).steps == before[0]
    else:
        invalid = app.store.directory / "invalid.json"
        invalid.write_text("{", encoding="utf-8")
        monkeypatch.setattr("clickcue.gui.messagebox.showwarning", modal)
        app._show_profile_errors()
        assert invalid.read_text(encoding="utf-8") == "{"
    app._poll_events()
    assert observed == [True]
    assert SyntheticListener.instances == []
    assert app._recording is recording
    assert app._sequence_snapshot() == before
    assert (app._undo_history, app._redo_history) == histories
    assert not app.dirty
    app._on_key_release(keyboard.Key.f9)
    app._on_key(keyboard.Key.f9)
    wait_ui(app, lambda: "Recording plain" in app.status_text.get())


def test_tk_review_uses_inputfree_preview_without_resuming_or_changing_pending_buffer(editor, monkeypatch):
    app = editor
    recording, listener = begin_ui(app)
    click(listener)
    app._on_key(keyboard.Key.f2)
    wait_ui(app, lambda: app.keep_recording_button.instate(["!disabled"]))
    before = app._sequence_snapshot()
    histories = deepcopy((app._undo_history, app._redo_history))
    pending = deepcopy(recording.steps)
    inspected = []

    def inspect(dialog):
        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)

        table = next(w for w in descendants(dialog) if w.winfo_class() == "Treeview")
        inspected.extend(table.item(row, "values")[1] for row in table.get_children())
        app._keep_recording()
        app._discard_recording()
        for key in (keyboard.Key.f2, keyboard.Key.f9):
            app._on_key_release(key)
            app._on_key(key)
        app.events.put(("record_begin", recording))
        app._poll_events()
        dialog.destroy()

    # Only replace the modal wait, inspecting the actual preview widgets.
    monkeypatch.setattr(tk.Toplevel, "wait_window", inspect)
    app.review_recording_button.invoke()
    app._poll_events()
    click(listener, 90, 90)
    assert inspected == ["Recorded click 1"]
    assert recording.finished.is_set() and listener.stopped.is_set()
    assert len(SyntheticListener.instances) == 1
    assert app._recording is recording and recording.steps == pending
    assert app._sequence_snapshot() == before
    assert (app._undo_history, app._redo_history) == histories
    assert not app.dirty
