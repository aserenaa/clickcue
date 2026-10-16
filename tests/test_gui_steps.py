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
    original = {step.name: step for step in app.steps}
    app._move_step(offset)
    assert "".join(step.name for step in app.steps) == expected
    assert all(step is original[step.name] for step in app.steps)
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
