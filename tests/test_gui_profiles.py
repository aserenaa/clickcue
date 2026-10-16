from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp
from clickcue.models import Step, TargetWindow


@pytest.mark.parametrize("action", ["save", "save_as", "delete"])
def test_profile_io_error_keeps_editor_state(action, monkeypatch):
    inferred_target = TargetWindow("Synthetic Game", 0, 0, 800, 600)
    original_target = inferred_target if action == "delete" else None
    app = ClickCueApp.__new__(ClickCueApp)
    app.root = Mock()
    app.store = Mock()
    app.store.exists.return_value = False
    app.store.save.side_effect = PermissionError("Access denied")
    app.store.delete.side_effect = PermissionError("Access denied")
    app.current_profile = "Original"
    app.dirty = True
    app.steps = [Step("Button", 100, 200, 1.5)]
    app.target_window = original_target
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
    monkeypatch.setattr("clickcue.gui.window_info_at", Mock(return_value=inferred_target))
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", error)
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", lambda *args: True)
    monkeypatch.setattr("clickcue.gui.simpledialog.askstring", lambda *args, **kwargs: "Copy")

    callback = {
        "save": app._save_profile,
        "save_as": app._save_profile_as,
        "delete": app._delete_profile,
    }[action]
    callback()

    assert app.current_profile == "Original"
    assert app.dirty is True
    assert app.steps == [Step("Button", 100, 200, 1.5)]
    assert app.target_window == original_target
    app.profile_selection.set.assert_not_called()
    app._refresh_profile_list.assert_not_called()
    app.status_text.set.assert_not_called()
    assert error.call_count == 1
    assert "Access denied" in error.call_args.args[1]
    assert "permission" in error.call_args.args[1].lower()

    app.store.save.side_effect = None
    app.store.delete.side_effect = None
    callback()

    assert app.steps == [Step("Button", 100, 200, 1.5)]
    assert error.call_count == 1
    if action == "delete":
        assert app.current_profile is None
        assert app.dirty is True
        assert app.target_window == original_target
    else:
        assert app.current_profile == ("Copy" if action == "save_as" else "Original")
        assert app.dirty is False
        assert app.target_window == inferred_target
        assert app.store.save.call_args.args[0].target_window == inferred_target
