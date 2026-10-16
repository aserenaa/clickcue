from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp


@pytest.mark.parametrize("action", ["save", "save_as", "delete"])
def test_profile_io_error_keeps_editor_state(action, monkeypatch):
    app = ClickCueApp.__new__(ClickCueApp)
    app.root = Mock()
    app.store = Mock()
    app.store.exists.return_value = False
    app.store.save.side_effect = PermissionError("Access denied")
    app.store.delete.side_effect = PermissionError("Access denied")
    app.current_profile = "Original"
    app.dirty = True
    app.steps = []
    app.target_window = None
    app.loop_forever = Mock()
    app.loop_forever.get.return_value = True
    app._repetition_count = Mock(return_value=1)
    app.profile_selection = Mock()
    app.profile_selection.get.return_value = "Original"
    app.status_text = Mock()
    app._refresh_profile_list = Mock()
    app._update_title = Mock()
    app._update_target_text = Mock()
    error = Mock()
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", error)
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", lambda *args: True)
    monkeypatch.setattr("clickcue.gui.simpledialog.askstring", lambda *args, **kwargs: "Copy")

    if action == "save":
        app._save_profile()
    elif action == "save_as":
        app._save_profile_as()
    else:
        app._delete_profile()

    assert app.current_profile == "Original"
    assert app.dirty is True
    app.profile_selection.set.assert_not_called()
    app._refresh_profile_list.assert_not_called()
    app.status_text.set.assert_not_called()
    assert error.call_count == 1
    assert "Access denied" in error.call_args.args[1]
    assert "permission" in error.call_args.args[1].lower()
