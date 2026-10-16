from dataclasses import replace
from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp
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
    app._preparation = None
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


@pytest.mark.parametrize("busy", ["playing", "capturing", "preparing"])
def test_history_cannot_change_sequence_while_busy(busy):
    app = editor([1])
    app._remove_step()
    edited = app.steps[:]
    app.capture_armed = busy == "capturing"
    app.player = Mock() if busy == "playing" else None
    app._preparation = Mock() if busy == "preparing" else None
    app._undo()
    assert app.steps == edited
    app.capture_armed = False
    app.player = None
    app._preparation = None
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
