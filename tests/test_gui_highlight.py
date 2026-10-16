from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp
from clickcue.hotkeys import HotkeySettings
from clickcue.models import Step


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr("clickcue.gui.mouse.Controller", Mock())
    app = ClickCueApp.__new__(ClickCueApp)
    app.player = None
    app._preparation = None
    app._closed = False
    app._closing = False
    app._editing_controls = []
    app.run_selected_button = Mock()
    app.pause_button = Mock()
    app._run_indices = None
    app._pending_control = None
    app.hotkeys = HotkeySettings()
    app.tree = Mock()
    app.tree.get_children.return_value = ("first", "second")
    app.loop_forever = Mock()
    app.loop_forever.get.return_value = True
    app.status_text = Mock()
    app.start_button = Mock()
    app._blocked_status = None
    app._highlighted_step = None
    app.steps = [Step("First", 0, 0, 2), Step("Second", 0, 0, 2)]
    return app


def test_countdown_updates_status_without_touching_same_row(app):
    app._handle_event(("waiting", 1, 0, "First", 2.0))
    app.tree.selection_set.assert_called_once_with("first")
    app.tree.focus.assert_called_once_with("first")
    app.tree.see.assert_called_once_with("first")
    app.tree.reset_mock()

    for remaining in (1.5, 1.0, 0.5):
        app._handle_event(("waiting", 1, 0, "First", remaining))
        assert f"waiting {remaining:.1f}s" in app.status_text.set.call_args.args[0]
    app._handle_event(("playing", 1, 0, "First"))

    app.tree.get_children.assert_not_called()
    app.tree.selection_remove.assert_not_called()
    app.tree.selection_set.assert_not_called()
    app.tree.focus.assert_not_called()
    app.tree.see.assert_not_called()


def test_highlight_tracks_transitions_repeats_and_stop_restart(app):
    for repetition, index, item in ((1, 0, "first"), (1, 1, "second"),
                                    (2, 0, "first")):
        app.tree.reset_mock()
        app._handle_event(("playing", repetition, index, "Step"))
        app.tree.selection_set.assert_called_once_with(item)
        app.tree.focus.assert_called_once_with(item)
        app.tree.see.assert_called_once_with(item)

    app.tree.reset_mock()
    app._handle_event(("stopped",))
    app.tree.selection_remove.assert_called_once_with("first", "second")
    app.tree.selection_set.assert_not_called()
    app._handle_event(("waiting", 1, 0, "First", 2.0))
    app.tree.selection_set.assert_called_once_with("first")


@pytest.mark.parametrize("select", [None, 0, 1])
def test_refresh_allows_same_index_to_highlight_rebuilt_row(app, select):
    app._handle_event(("playing", 1, 0, "First"))
    app._refresh_table(select=select)
    app.tree.get_children.return_value = ("rebuilt-first", "rebuilt-second")
    app.tree.reset_mock()

    app._handle_event(("waiting", 1, 0, "First", 1.0))

    app.tree.selection_set.assert_called_once_with("rebuilt-first")
    app.tree.focus.assert_called_once_with("rebuilt-first")
    app.tree.see.assert_called_once_with("rebuilt-first")
