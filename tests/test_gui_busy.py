import tkinter as tk
from copy import deepcopy
from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp
from clickcue.hotkeys import HotkeySettings
from clickcue.models import Step, TargetWindow
from clickcue.preparation import PreparationResult
from clickcue.profiles import Profile, ProfileStore


@pytest.fixture(scope="session")
def runtime():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def editor(runtime, tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profiles")
    store.save(Profile("Original", [Step("First", 1, 2, 0), Step("Second", 3, 4, 0)]))
    monkeypatch.setattr("clickcue.gui.ProfileStore", lambda: store)
    monkeypatch.setattr("clickcue.gui.load_hotkeys", HotkeySettings)
    monkeypatch.setattr("clickcue.gui.PlaybackPreparation.start", Mock())
    monkeypatch.setattr("clickcue.gui.Player", Mock())
    monkeypatch.setattr("clickcue.gui.window_info_at", Mock(return_value=None))
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", Mock())
    monkeypatch.setattr("clickcue.gui.messagebox.showwarning", Mock())
    monkeypatch.setattr("clickcue.gui.messagebox.showinfo", Mock())
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", Mock(return_value=True))
    root = tk.Toplevel(runtime)
    root.withdraw()
    app = ClickCueApp(root, start_listener=False)
    app.profile_selection.set("Original")
    app._open_selected_profile()
    app.target_window = TargetWindow("Original target")
    app.tree.selection_set(app.tree.get_children()[0])
    yield app
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    if not app._closed:
        app.dirty = False
        app._on_close()


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


def button(app, prefix):
    return next(w for w in widgets(app.root)
                if w.winfo_class() == "TButton" and str(w.cget("text")).startswith(prefix))


@pytest.mark.parametrize("phase", ["preparing", "playing", "paused"])
def test_busy_callbacks_preserve_editor_and_profile_files(editor, monkeypatch, tmp_path, phase):
    app = editor
    app._copy_step()
    app._copy_step()
    app._undo()  # Both history directions have an actual mutation to restore.
    app.capture_armed = True  # Starting must disarm any outstanding capture.
    app._toggle_playback()
    if phase != "preparing":
        app._preparation.result = PreparationResult(app.steps[:], "Playing")
        app._finish_preparation(app._preparation)
        app.player.paused = False
        if phase == "paused":
            app._toggle_pause()
            app.player.pause.assert_called_once()
    before = deepcopy(app.steps)
    files = {p.name: p.read_bytes() for p in app.store.directory.iterdir()}
    changed = Step("Changed", 40, 50, 0)
    monkeypatch.setattr("clickcue.gui.StepDialog", lambda *args: Mock(result=changed))
    monkeypatch.setattr("clickcue.gui.WindowPickerDialog", lambda *args: Mock(
        result=TargetWindow("Changed target")))
    monkeypatch.setattr("clickcue.gui.simpledialog.askstring", lambda *a, **k: "Changed")
    incoming = tmp_path / "import.json"
    incoming.write_text('{"name":"Changed","steps":[]}', encoding="utf-8")
    monkeypatch.setattr("clickcue.gui.filedialog.askopenfilename", lambda **k: str(incoming))
    app.profile_selection.set("Changed")
    for action in (app._add_step, app._edit_step, app._copy_step, app._remove_step,
                   lambda: app._move_step(1), app._undo, app._redo, app._arm_capture,
                   app._choose_target_window, app._clear_target_window, app._new_profile,
                   app._open_selected_profile, app._save_profile, app._save_profile_as,
                   app._delete_profile, app._import_profile,
                   lambda: app._write_profile("Changed")):
        action()
        assert app.steps == before
        assert app.target_window.app == "Original target"
        assert app.current_profile == "Original"
        assert not app.capture_armed
        assert {p.name: p.read_bytes() for p in app.store.directory.iterdir()} == files
    app._handle_event(("hotkey_capture", 90, 91))
    app.loop_forever.set(False)
    app.repetitions.set("42")
    assert app.steps == before
    assert app.target_window.app == "Original target"
    assert app.current_profile == "Original"
    assert app.profile_selection.get() == "Original"
    assert app.loop_forever.get() is True
    assert app.repetitions.get() == "1"
    assert not app.capture_armed
    # Export remains a read-only view of the stable editor during either phase.
    exported = tmp_path / "export.json"
    monkeypatch.setattr("clickcue.gui.filedialog.asksaveasfilename", lambda **k: str(exported))
    app._export_profile()
    from clickcue.profiles import read_profile
    assert read_profile(exported).steps == before
    diagnostics = button(app, "Diagnostics")
    assert diagnostics.instate(["!disabled"])
    diagnostics.invoke()
    panel = next(w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel))
    assert app.root.grab_current() is None
    assert app.steps == before
    assert app.target_window.app == "Original target"
    panel.destroy()
    assert {p.name: p.read_bytes() for p in app.store.directory.iterdir()} == files


@pytest.mark.parametrize("outcome", ["stop", "error", "missing", "finished", "play_stop", "play_error", "pause_stop", "cancel_close"])
def test_controls_stay_locked_until_completion_and_restore(editor, outcome):
    app = editor
    controls = [button(app, prefix) for prefix in
                ("New", "Save", "Delete", "Import", "Choose", "Clear", "Capture",
                 "Add", "Edit", "Copy", "Remove", "Undo", "Redo", "Apply")]
    controls += [w for w in widgets(app.root) if w.winfo_class() in
                 ("TCombobox", "TRadiobutton", "TSpinbox")]
    app._toggle_playback()
    preparation = app._preparation
    assert all(w.instate(["disabled"]) for w in controls)
    assert app.start_button.instate(["!disabled"])
    assert app.pause_button.instate(["disabled"])
    assert app.run_selected_button.instate(["disabled"])
    assert button(app, "Export").instate(["!disabled"])
    if outcome in ("finished", "play_stop", "play_error", "pause_stop"):
        preparation.result = PreparationResult(app.steps[:], "Playing")
        app._handle_event(("prepared", preparation))
        assert all(w.instate(["disabled"]) for w in controls)
        assert app.pause_button.instate(["!disabled"])
        if outcome == "pause_stop":
            app.player.paused = False
            app._toggle_pause()
            app.player.pause.assert_called_once()
            assert app.pause_button.cget("text") == "Resume"
            app.player.paused = True
            app._handle_event(("waiting", 1, 0, "First", 1.0))
            assert app.status_text.get().startswith("Paused")
            app._toggle_pause()
            app.player.resume.assert_called_once()
            assert app.pause_button.cget("text") == "Pause"
        if outcome == "play_error":
            app._handle_event(("error", "synthetic failure"))
        if outcome in ("play_stop", "pause_stop"):
            app._toggle_playback()
            app.player.stop.assert_called_once()
        assert all(w.instate(["disabled"]) for w in controls)
        app._handle_event(("stopped",))
    else:
        if outcome == "stop":
            app._toggle_playback()
            assert preparation.windows.cancelled.is_set()
        elif outcome == "error":
            preparation.error = RuntimeError("synthetic failure")
        elif outcome == "missing":
            preparation.result = PreparationResult(app.steps[:], "Missing", True)
        else:
            app.dirty = True
            from clickcue.gui import messagebox
            messagebox.askyesno.return_value = False
            app._on_close()
        app._handle_event(("prepared", preparation))
        assert all(w.instate(["disabled"]) for w in controls)
        app._poll_events()
    assert all(w.instate(["!disabled"]) for w in controls)
    assert app.profile_combo.instate(["readonly"])
    assert app.pause_button.instate(["disabled"])
    assert app.pause_button.cget("text") == "Pause"
    assert app.run_selected_button.instate(["!disabled"])
    app.tree.selection_set(app.tree.get_children()[0])
    app._copy_step()
    assert len(app.steps) == 3


@pytest.mark.parametrize("action", ["add", "edit", "target", "new", "open", "import", "save_as", "delete"])
def test_dialog_completion_cannot_mutate_after_start(editor, monkeypatch, tmp_path, action):
    app = editor
    original = deepcopy(app.steps)
    files = {p.name: p.read_bytes() for p in app.store.directory.iterdir()}

    def start(result):
        app._toggle_playback()
        return result

    monkeypatch.setattr("clickcue.gui.StepDialog", lambda *args: start(Mock(result=Step("Changed", 4, 5, 0))))
    monkeypatch.setattr("clickcue.gui.WindowPickerDialog", lambda *args: start(Mock(result=TargetWindow("Changed"))))
    monkeypatch.setattr("clickcue.gui.simpledialog.askstring", lambda *a, **k: start("Changed"))
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", lambda *a, **k: start(True))
    incoming = tmp_path / "incoming.json"
    incoming.write_text('{"name":"Imported","steps":[]}', encoding="utf-8")
    monkeypatch.setattr("clickcue.gui.filedialog.askopenfilename", lambda **k: start(str(incoming)))
    app.dirty = True
    if action == "open":
        app.store.save(Profile("Other"))
        files = {p.name: p.read_bytes() for p in app.store.directory.iterdir()}
        app.profile_selection.set("Other")
    callbacks = {"add": app._add_step, "edit": app._edit_step,
                 "target": app._choose_target_window, "new": app._new_profile,
                 "open": app._open_selected_profile, "import": app._import_profile,
                 "save_as": app._save_profile_as, "delete": app._delete_profile}
    callbacks[action]()
    assert app._preparation is not None
    assert app.steps == original
    assert app.current_profile == "Original"
    assert app.target_window.app == "Original target"
    assert {p.name: p.read_bytes() for p in app.store.directory.iterdir()} == files


def test_modal_grab_blocks_start_but_not_active_stop(editor):
    app = editor
    dialog = tk.Toplevel(app.root)
    try:
        dialog.grab_set()
        app._toggle_playback()
        assert app._preparation is None
        dialog.grab_release()
        app._toggle_playback()
        preparation = app._preparation
        dialog.grab_set()
        app._toggle_playback()
        assert preparation.windows.cancelled.is_set()
    finally:
        dialog.destroy()


@pytest.mark.parametrize("loop_forever", [False, True])
def test_selected_run_snapshots_order_settings_and_original_progress_rows(editor, loop_forever):
    app = editor
    app.steps.insert(1, Step("Skip", 9, 9, 0))
    app._refresh_table()
    rows = app.tree.get_children()
    app.tree.selection_set(rows[2], rows[0])
    app.loop_forever.set(loop_forever)
    app.repetitions.set("3")
    before = app._sequence_snapshot()
    history = (deepcopy(app._undo_history), deepcopy(app._redo_history))
    app.run_selected_button.invoke()
    preparation = app._preparation
    assert preparation.steps == [before[0][0], before[0][2]]
    assert preparation.loop_forever is loop_forever
    assert preparation.repetitions == 3
    assert preparation.windows.anchor == app.target_window
    app.tree.selection_set(rows[1])  # Selection changes cannot change this run.
    preparation.result = PreparationResult(preparation.steps, "Playing selected")
    app._handle_event(("prepared", preparation))
    from clickcue.gui import Player
    assert Player.call_args.args[:3] == (preparation.steps, loop_forever, 3)
    assert Player.call_args.kwargs["guard"].__self__ is preparation.windows
    app.player.paused = False
    for repetition, index, row in ((1, 0, rows[0]), (1, 1, rows[2]), (2, 0, rows[0])):
        app._handle_event(("playing", repetition, index, "Selected"))
        assert app.tree.selection() == (row,)
        assert app.tree.focus() == row
        assert f"step {app.tree.index(row) + 1}:" in app.status_text.get()
    app._handle_event(("stopped",))
    assert app.steps == before[0]
    assert (app._undo_history, app._redo_history) == history


def test_run_selected_without_selection_explains_how_to_start(editor):
    app = editor
    app.tree.selection_remove(*app.tree.get_children())
    app.run_selected_button.invoke()
    assert app._preparation is None
    from clickcue.gui import messagebox
    messagebox.showinfo.assert_called_once()
    assert "Select one or more steps" in messagebox.showinfo.call_args.args[1]
