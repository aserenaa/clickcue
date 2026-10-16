from dataclasses import replace
from enum import Enum
from threading import Lock
from unittest.mock import Mock

import pytest
from pynput import keyboard

from clickcue.gui import ClickCueApp
from clickcue.hotkeys import HotkeySettings
from clickcue.models import Step


class Tree:
    """In-memory Treeview surface; sequence operations remain in the app."""

    def __init__(self):
        self.rows = {}
        self.selected = ()
        self.visible = None
        self.next_id = 0

    def get_children(self):
        return tuple(self.rows)

    def insert(self, parent, position, *, values):
        self.next_id += 1
        self.rows[str(self.next_id)] = values

    def delete(self, *items):
        for item in items:
            del self.rows[item]
        self.selected = tuple(item for item in self.selected if item in self.rows)

    def index(self, item):
        return self.get_children().index(item)

    def selection(self):
        return self.selected

    def selection_set(self, items):
        self.selected = (items,) if isinstance(items, str) else tuple(items)

    def see(self, item):
        self.visible = item


def editor(selection):
    app = ClickCueApp.__new__(ClickCueApp)
    app.steps = [Step(name, index, index + 10, index / 2, index % 2 == 0)
                 for index, name in enumerate("ABCDE")]
    app.tree = Tree()
    app.root = Mock()
    app.player = None
    app.capture_armed = False
    app._undo_history = []
    app._redo_history = []
    app._loading = False
    app.dirty = False
    app._update_title = Mock()
    app._refresh_table()
    # Reverse selection order to ensure operations follow row order.
    app.tree.selection_set([app.tree.get_children()[index]
                            for index in reversed(selection)])
    return app


def test_preview_requires_clicks_and_stopped_capture_or_playback(monkeypatch):
    app = editor([])
    notice = Mock()
    monkeypatch.setattr("clickcue.gui.messagebox.showinfo", notice)
    for steps, player, capturing, hint in [
        ([], None, False, "Add"),
        ([Step("Pause", 100, 200, 1, True)], None, False, "wait"),
        ([Step("Click", 1, 2, 0)], Mock(), False, "Stop"),
        ([Step("Click", 1, 2, 0)], None, True, "capture"),
    ]:
        app.steps, app.player, app.capture_armed = steps, player, capturing
        notice.reset_mock()
        app._preview_sequence()
        notice.assert_called_once()
        assert hint in notice.call_args.args[1]


@pytest.mark.parametrize("coordinates", [
    [(-100, -100), (100, 0)],
    [(-200, -200), (-100, -100)],
    [(17, 29), (17, 29)],
])
def test_preview_renders_recorded_positions_without_input_and_releases_hotkeys(
        monkeypatch, coordinates):
    app = editor([])
    app.steps = [Step("First", *coordinates[0], 0),
                 Step("Pause", 9999, 9999, 2, True),
                 Step("Last", *coordinates[1], 1)]
    original = [replace(step) for step in app.steps]
    app.hotkeys = HotkeySettings()
    app._hotkey_lock = Lock()
    app._pressed_keys = set()
    app.events = Mock()
    monkeypatch.setattr(keyboard, "Key", Enum("Key", {f"f{i}": i for i in range(1, 13)}))
    controllers = [Mock(), Mock(), Mock(), Mock()]
    for name, controller in zip(
            ("mouse.Controller", "keyboard.Controller", "Player", "WindowPlayback"),
            controllers, strict=True):
        monkeypatch.setattr(f"clickcue.gui.{name}", controller)
    dialog, canvas, table = Mock(), Mock(), Mock()
    monkeypatch.setattr("clickcue.preview.tk.Toplevel", Mock(return_value=dialog))
    canvas_widget = Mock(return_value=canvas)
    monkeypatch.setattr("clickcue.preview.tk.Canvas", canvas_widget)
    monkeypatch.setattr("clickcue.preview.ttk.Treeview", Mock(return_value=table))
    for widget in ("Frame", "Label", "Scrollbar", "Button"):
        monkeypatch.setattr(f"clickcue.preview.ttk.{widget}", Mock())

    def dismiss():
        # Both queued events and fresh listener callbacks must be inert. Fresh
        # callbacks must not leave a start event to fire after the dialog closes.
        for event in (("hotkey_toggle",), ("hotkey_capture", 50, 60)):
            app._handle_event(event)
        for key in (keyboard.Key.f6, keyboard.Key.f8):
            app._on_key(key)
            app._on_key_release(key)
        dialog.bind.call_args.args[1](None)  # Escape

    dialog.wait_window.side_effect = dismiss
    app._preview_sequence()

    viewport = canvas_widget.call_args.kwargs
    centres = []
    for marker in canvas.create_oval.call_args_list:
        x1, y1, x2, y2 = marker.args
        assert 0 <= x1 < x2 <= viewport["width"]
        assert 0 <= y1 < y2 <= viewport["height"]
        centres.append(((x1 + x2) / 2, (y1 + y2) / 2))
    if coordinates[0] != coordinates[1]:
        dx = centres[1][0] - centres[0][0]
        dy = centres[1][1] - centres[0][1]
        assert dx > 0 and dy > 0
        assert dx / dy == pytest.approx(
            (coordinates[1][0] - coordinates[0][0]) /
            (coordinates[1][1] - coordinates[0][1]))
    labels = [call.kwargs["text"] for call in canvas.create_text.call_args_list]
    assert labels == (["1, 3"] if coordinates[0] == coordinates[1] else ["1", "3"])
    assert [call.kwargs["values"] for call in table.insert.call_args_list] == [
        (1, "First", str(coordinates[0]), 0),
        (2, "Pause", "Wait only — no click", 2),
        (3, "Last", str(coordinates[1]), 1),
    ]
    dialog.destroy.assert_called_once()
    assert app.steps == original
    assert not app.dirty
    app.events.put.assert_not_called()
    for controller in controllers:
        controller.assert_not_called()
    # Closing returns hotkeys to their normal event path.
    app._on_key(keyboard.Key.f6)
    app.events.put.assert_called_once_with(("hotkey_toggle",))


def test_preview_unrenderable_coordinates_destroy_dialog_before_releasing_guard(monkeypatch):
    app = editor([])
    app.steps = [Step("Origin", 0, 0, 0), Step("Large", 10**400, 0, 0)]
    original = [replace(step) for step in app.steps]
    app._toggle_playback = Mock()
    dialog = Mock()
    monkeypatch.setattr("clickcue.preview.tk.Toplevel", Mock(return_value=dialog))
    for widget in ("Frame", "Label"):
        monkeypatch.setattr(f"clickcue.preview.ttk.{widget}", Mock())

    def destroyed():
        assert app._preview_active

    def report_error(*args, **kwargs):
        dialog.destroy.assert_called_once()
        assert app._preview_active
        app._handle_event(("hotkey_toggle",))
        app._toggle_playback.assert_not_called()

    dialog.destroy.side_effect = destroyed
    error = Mock(side_effect=report_error)
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", error)

    app._preview_sequence()

    error.assert_called_once()
    assert "coordinates" in error.call_args.args[1]
    assert "Edit" in error.call_args.args[1]
    assert app.steps == original
    assert not app.dirty
    assert not app._preview_active
    app._handle_event(("hotkey_toggle",))
    app._toggle_playback.assert_called_once()


def assert_rows_match_steps(app):
    assert [row[1] for row in app.tree.rows.values()] == [step.name for step in app.steps]


@pytest.mark.parametrize("selection,remaining", [
    ([1, 3], "ACE"), ([1, 2], "ADE"), ([0, 4], "BCD"), (list(range(5)), ""),
])
def test_remove_selected_steps(selection, remaining):
    app = editor(selection)
    app._remove_step()
    assert "".join(step.name for step in app.steps) == remaining
    assert_rows_match_steps(app)
    assert not app.tree.selection()
    assert app.dirty


@pytest.mark.parametrize("selection,offset,expected,selected", [
    ([1, 3], -1, "BADCE", "BD"),
    ([1, 3], 1, "ACBED", "BD"),
    ([1, 2], -1, "BCADE", "BC"),
    ([1, 2], 1, "ADBCE", "BC"),
    ([0, 1, 3], -1, "ABDCE", "ABD"),
    ([1, 3, 4], 1, "ACBDE", "BDE"),
    ([0, 1], -1, "ABCDE", "AB"),
    ([3, 4], 1, "ABCDE", "DE"),
    (list(range(5)), -1, "ABCDE", "ABCDE"),
    (list(range(5)), 1, "ABCDE", "ABCDE"),
])
def test_move_selected_steps(selection, offset, expected, selected):
    app = editor(selection)
    original = {step.name: replace(step) for step in app.steps}
    app._move_step(offset)
    assert "".join(step.name for step in app.steps) == expected
    assert all(step == original[step.name] for step in app.steps)
    assert_rows_match_steps(app)
    selected_items = app.tree.selection()
    assert "".join(app.tree.rows[item][1] for item in sorted(
        selected_items, key=app.tree.index)) == selected
    assert app.dirty == (expected != "ABCDE")
    if app.dirty:
        assert app.tree.visible in selected_items
        revealed_name = app.tree.rows[app.tree.visible][1]
        assert app.tree.index(app.tree.visible) != list(original).index(revealed_name)


@pytest.mark.parametrize("selection", [[1, 3], [1, 2]])
def test_copy_selects_and_reveals_independent_copies(selection):
    app = editor(selection)
    original = app.steps[:]
    copies_at = selection[-1] + 1
    app._copy_step()
    copies = app.steps[copies_at:copies_at + len(selection)]
    assert copies == [original[index] for index in selection]
    assert all(copy is not original[index]
               for copy, index in zip(copies, selection, strict=True))
    assert app.steps[:copies_at] + app.steps[copies_at + len(selection):] == original
    assert_rows_match_steps(app)
    assert [app.tree.index(item) for item in app.tree.selection()] == list(
        range(copies_at, copies_at + len(selection)))
    assert app.tree.visible in app.tree.selection()
    assert app.dirty


@pytest.mark.parametrize("action,args", [
    ("_remove_step", ()), ("_move_step", (-1,)),
    ("_move_step", (1,)), ("_copy_step", ()),
])
def test_empty_selection_is_noop(action, args):
    app = editor([])
    original = app.steps[:]
    rows = app.tree.rows.copy()
    getattr(app, action)(*args)
    assert app.steps == original
    assert app.tree.rows == rows
    assert not app.dirty
    assert not app.tree.selection()
    assert app.tree.visible is None


@pytest.mark.parametrize("action,args", [
    ("_remove_step", ()), ("_copy_step", ()), ("_move_step", (-1,)),
])
def test_history_round_trip_restores_multiselection(action, args):
    app = editor([1, 3])
    original = [Step(s.name, s.x, s.y, s.delay, s.wait_only) for s in app.steps]
    getattr(app, action)(*args)
    changed = app.steps[:]
    changed_selection = [app.tree.index(item) for item in app.tree.selection()]
    app._undo()
    assert app.steps == original
    assert sorted(app.tree.index(item) for item in app.tree.selection()) == [1, 3]
    app._redo()
    assert app.steps == changed
    assert [app.tree.index(item) for item in app.tree.selection()] == changed_selection
    assert app.dirty


def test_new_edit_invalidates_redo_and_snapshots_do_not_alias_steps():
    app = editor([1])
    app._copy_step()
    app._undo()
    app.steps[1].name = "Changed later"
    app._redo()
    assert app.steps[1].name == "B"
    app._undo()
    app._remove_step()
    after_remove = app.steps[:]
    app._redo()
    assert app.steps == after_remove


def test_unchanged_dialog_and_boundary_move_keep_redo_and_clean_state(monkeypatch):
    app = editor([0])
    app._copy_step()
    app._undo()
    app.dirty = False
    monkeypatch.setattr("clickcue.gui.StepDialog", lambda *args: Mock(result=Step("A", 0, 10, 0, True)))
    app._edit_step()
    app._move_step(-1)
    assert not app.dirty
    app._redo()
    assert [s.name for s in app.steps] == list("AABCDE")


@pytest.mark.parametrize("capturing", [False, True])
def test_history_cannot_change_sequence_during_playback_or_capture(capturing):
    app = editor([1])
    app._remove_step()
    edited = app.steps[:]
    app.capture_armed = capturing
    app.player = None if capturing else Mock()
    app._undo()
    assert app.steps == edited
    app.capture_armed = False
    app.player = None
    app._undo()
    assert [s.name for s in app.steps] == list("ABCDE")


def test_add_capture_and_edit_round_trip(monkeypatch):
    app = editor([1])
    app._refresh_target_bounds = Mock()
    results = iter([Step("Added", 10, 20, 1), None,
                    Step("Edited", 3, 4, 5)])

    def dialog(_parent, _title, prefill=None):
        result = next(results)
        if result is None:
            result = replace(prefill, name="Captured", delay=2)
        return Mock(result=result)

    monkeypatch.setattr("clickcue.gui.StepDialog", dialog)
    app._add_step()
    app.capture_armed = True
    app._handle_event(("hotkey_capture", 30, 40))
    assert app.steps[-1] == Step("Captured", 30, 40, 2)
    app.tree.selection_set(app.tree.get_children()[0])
    app._edit_step()
    assert [s.name for s in app.steps] == ["Edited", "B", "C", "D", "E", "Added", "Captured"]
    for _ in range(3):
        app._undo()
    assert [s.name for s in app.steps] == list("ABCDE")
    for _ in range(3):
        app._redo()
    assert [s.name for s in app.steps] == ["Edited", "B", "C", "D", "E", "Added", "Captured"]
    assert app.steps[-1] == Step("Captured", 30, 40, 2)


@pytest.mark.parametrize("boundary", ["new", "open"])
def test_profile_boundary_discards_both_history_directions(boundary):
    from clickcue.profiles import Profile

    app = editor([1])
    app._copy_step()
    app._copy_step()
    app._undo()
    app._confirm_discard = Mock(return_value=True)
    app.current_profile = "Old"
    app.profile_selection = Mock()
    app.profile_selection.get.return_value = "Other"
    app.store = Mock()
    app.store.load.return_value = Profile("Other", steps=[Step("Other", 1, 2, 0)])
    app.loop_forever = Mock()
    app.repetitions = Mock()
    app.status_text = Mock()
    app._refresh_profile_list = Mock()
    app._update_target_text = Mock()
    (app._new_profile if boundary == "new" else app._open_selected_profile)()
    app._undo()
    app._redo()
    assert [s.name for s in app.steps] == ([] if boundary == "new" else ["Other"])
    assert not app.dirty


@pytest.mark.parametrize("widget_class,dialog,grab,handled", [
    ("Treeview", False, False, True), ("TButton", False, False, True),
    ("Entry", False, False, False), ("TEntry", False, False, False),
    ("Text", False, False, False), ("Spinbox", False, False, False),
    ("TSpinbox", False, False, False), ("TCombobox", False, False, False),
    ("TButton", True, False, False), ("Treeview", False, True, False),
])
def test_history_shortcuts_leave_text_and_dialogs_alone(widget_class, dialog, grab, handled):
    app = editor([1])
    app._remove_step()
    widget = Mock()
    widget.winfo_class.return_value = widget_class
    widget.winfo_toplevel.return_value = Mock() if dialog else app.root
    app.root.grab_current.return_value = Mock() if grab else None
    result = app._history_shortcut(Mock(widget=widget), False)
    assert result == ("break" if handled else None)
    assert [s.name for s in app.steps] == list("ABCDE" if handled else "ACDE")
    if handled:
        app._history_shortcut(Mock(widget=widget), True)
        assert [s.name for s in app.steps] == list("ACDE")


def test_history_retains_only_the_latest_hundred_edits(monkeypatch):
    app = editor([])
    monkeypatch.setattr("clickcue.gui.StepDialog", lambda *args: Mock(result=Step("New", 1, 2, 0)))
    for _ in range(105):
        app._add_step()
    for _ in range(105):
        app._undo()
    assert len(app.steps) == 10  # Original five plus the oldest five additions.
    for _ in range(105):
        app._redo()
    assert len(app.steps) == 110


@pytest.mark.parametrize("outcome", ["success", "invalid", "cancel_file", "cancel_discard"])
@pytest.mark.parametrize("dirty", [False, True])
def test_import_history_boundary_through_editor_callback(tmp_path, monkeypatch, outcome, dirty):
    from clickcue.profiles import Profile, ProfileStore

    app = editor([1])
    app._copy_step()
    app._copy_step()
    app._undo()  # Both undo and redo are available before the import.
    app.current_profile = "Original"
    app.imported_profile_name = None
    app.dirty = dirty
    app.store = ProfileStore(tmp_path / "local")
    app.store.save(Profile("Original", steps=app.steps))
    saved_file = app.store.directory / "Original.json"
    saved_bytes = saved_file.read_bytes()
    app.loop_forever = Mock()
    app.repetitions = Mock()
    app.status_text = Mock()
    app._refresh_profile_list = Mock()
    app._update_target_text = Mock()
    external = tmp_path / "download.json"
    external.write_text(
        '{"name":"Imported","steps":[{"name":"Pause","x":0,"y":0,"delay":2}]}'
        if outcome != "invalid" else '{"name":"Invalid","schema_version":999}',
        encoding="utf-8")
    monkeypatch.setattr("clickcue.gui.filedialog.askopenfilename", Mock(
        return_value="" if outcome == "cancel_file" else str(external)))
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", Mock())
    # A clean editor has no discard prompt, so exercise that cancellation only
    # when the editor has unsaved changes.
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", Mock(
        return_value=outcome != "cancel_discard"))

    app._import_profile()

    replaced = outcome == "success" or (outcome == "cancel_discard" and not dirty)
    assert saved_file.read_bytes() == saved_bytes
    if replaced:
        assert app.current_profile is None
        assert app.imported_profile_name == "Imported"
        assert app.dirty
        app._undo()
        assert app.steps == [Step("Pause", 0, 0, 2)]
        app._redo()
        assert app.steps == [Step("Pause", 0, 0, 2)]
        assert app.dirty
    else:
        assert app.current_profile == "Original"
        assert app.imported_profile_name is None
        assert app.dirty == dirty
        assert [s.name for s in app.steps] == list("ABBCDE")
        app._redo()
        assert [s.name for s in app.steps] == list("ABBBCDE")
        app._undo()
        app._undo()
        app._undo()
        assert [s.name for s in app.steps] == list("ABCDE")
        assert app.dirty  # History restoration remains conservatively unsaved.


@pytest.mark.parametrize("dirty", [False, True])
def test_export_preserves_sequence_history(tmp_path, monkeypatch, dirty):
    from clickcue.profiles import read_profile

    app = editor([1])
    app._copy_step()
    app._copy_step()
    app._undo()
    app.current_profile = "Original"
    app.dirty = dirty
    app.target_window = None
    app.loop_forever = Mock()
    app.loop_forever.get.return_value = True
    app._repetition_count = Mock(return_value=1)
    app.status_text = Mock()
    external = tmp_path / "export.json"
    monkeypatch.setattr("clickcue.gui.filedialog.asksaveasfilename", Mock(
        return_value=str(external)))

    app._export_profile()

    assert [s.name for s in read_profile(external).steps] == list("ABBCDE")
    assert app.current_profile == "Original"
    assert app.dirty == dirty
    app._redo()
    assert [s.name for s in app.steps] == list("ABBBCDE")
    app._undo()
    app._undo()
    app._undo()
    assert [s.name for s in app.steps] == list("ABCDE")
    assert app.dirty
