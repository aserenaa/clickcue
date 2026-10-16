from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp
from clickcue.models import Step, TargetWindow
from clickcue.profiles import Profile, ProfileStore


def test_profile_errors_are_available_on_demand_and_clear_after_repair(tmp_path, monkeypatch):
    app = ClickCueApp.__new__(ClickCueApp)
    app.root = Mock()
    app.store = ProfileStore(tmp_path)
    app.store.save(Profile("Good"))
    broken = tmp_path / "Broken.json"
    broken.write_text('{"name": "Broken", "repetitions": 0}', encoding="utf-8")
    app.current_profile = "Good"
    app.profile_combo = Mock()
    app.profile_selection = Mock()
    app.profile_errors_button = Mock()
    warning = Mock()
    monkeypatch.setattr("clickcue.gui.messagebox.showwarning", warning)

    app._refresh_profile_list()
    app._refresh_profile_list()

    app.profile_combo.configure.assert_called_with(values=["Good"])
    app.profile_selection.set.assert_called_with("Good")
    app.profile_errors_button.pack.assert_called()
    warning.assert_not_called()

    app._show_profile_errors()
    message = warning.call_args.args[1]
    assert str(tmp_path) in message
    assert "Broken.json" in message
    assert "repetitions" in message
    assert "permissions" in message
    assert "unchanged" in message

    app.store.save(Profile("Broken"))
    app._show_profile_errors()
    app.profile_combo.configure.assert_called_with(values=["Broken", "Good"])
    app.profile_errors_button.pack_forget.assert_called()
    assert warning.call_count == 1


@pytest.mark.parametrize("action", ["save", "save_as", "delete"])
def test_profile_io_error_keeps_editor_state(action, monkeypatch):
    inferred_target = TargetWindow("Synthetic Game", 0, 0, 800, 600)
    original_target = inferred_target if action == "delete" else None
    app = ClickCueApp.__new__(ClickCueApp)
    app.player = None
    app._preparation = None
    app._closed = False
    app._closing = False
    app.root = Mock()
    app.store = Mock()
    app.store.exists.return_value = False
    app.store.save.side_effect = PermissionError("Access denied")
    app.store.delete.side_effect = PermissionError("Access denied")
    app.current_profile = "Original"
    app.imported_profile_name = None
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


@pytest.fixture
def transfer_editor(tmp_path, monkeypatch):
    app = ClickCueApp.__new__(ClickCueApp)
    app.player = None
    app._preparation = None
    app._closed = False
    app._closing = False
    app._undo_history = []
    app._redo_history = []
    app.root = Mock()
    app.store = ProfileStore(tmp_path / "local")
    app.current_profile = "Original"
    app.imported_profile_name = None
    app.dirty = True
    app._loading = False
    app.steps = [Step("Edited", 10, 20, 0.5)]
    app.target_window = TargetWindow("Edited target", 1, 2, 300, 200)
    app.loop_forever = Mock()
    app.loop_forever.get.return_value = False
    app.repetitions = Mock()
    app._repetition_count = Mock(return_value=7)
    for name in ("profile_selection", "status_text", "_refresh_profile_list",
                 "_refresh_table", "_update_title", "_update_target_text"):
        setattr(app, name, Mock())
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", Mock())
    return app


@pytest.mark.parametrize("version", [None, 0, 1])
def test_import_external_name_opens_unsaved_copy_and_save_confirms_collision(
        transfer_editor, tmp_path, monkeypatch, version):
    import json

    app = transfer_editor
    payload = {"name": "Shared", "target_app": "Legacy target",
               "steps": [{"name": "Pause", "x": 0, "y": 0,
                          "delay": 2, "wait_only": True}]}
    if version is not None:
        payload["schema_version"] = version
    external = tmp_path / "unrelated-download.json"
    external.write_text(json.dumps(payload), encoding="utf-8")
    app.store.save(Profile("Shared"))
    original = (app.store.directory / "Shared.json").read_bytes()
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", Mock(return_value=str(external)))
    confirm = Mock(return_value=True)
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", confirm)

    app._import_profile()

    assert app.current_profile is None
    assert app.dirty is True
    assert app.steps == [Step("Pause", 0, 0, 2, True)]
    assert app.target_window == TargetWindow("Legacy target")
    app.loop_forever.set.assert_called_with(True)
    app.repetitions.set.assert_called_with("1")
    assert confirm.call_count == 1
    assert (app.store.directory / "Shared.json").read_bytes() == original

    monkeypatch.setattr("clickcue.gui.simpledialog.askstring", Mock(return_value="Shared"))
    confirm.return_value = False
    app._save_profile()
    assert confirm.call_args.args[0] == "Overwrite profile"
    assert (app.store.directory / "Shared.json").read_bytes() == original
    assert app.current_profile is None
    confirm.return_value = True
    app._save_profile()
    assert app.store.load("Shared").steps == app.steps
    assert app.current_profile == "Shared"
    assert app.dirty is False


@pytest.mark.parametrize("payload,discard", [
    ('{"name":"Bad","schema_version":3}', True),
    ('{"name":"Bad","steps":[{"name":"Valid","x":0,"y":0,"delay":0},null]}', True),
    ('{"name":"Valid"}', False),
])
def test_import_rejection_preserves_editor(transfer_editor, tmp_path, monkeypatch, payload, discard):
    app = transfer_editor
    external = tmp_path / "download.json"
    external.write_text(payload, encoding="utf-8")
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", Mock(return_value=str(external)))
    confirm = Mock(return_value=discard)
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", confirm)
    error = Mock()
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", error)
    before = (app.steps, app.target_window, app.current_profile, app.dirty)

    app._import_profile()

    assert (app.steps, app.target_window, app.current_profile, app.dirty) == before
    app.loop_forever.set.assert_not_called()
    app.repetitions.set.assert_not_called()
    app.profile_selection.set.assert_not_called()
    assert list(app.store.directory.iterdir()) == []
    if discard:
        confirm.assert_not_called()  # Validation precedes any discard prompt.
        assert error.call_count == 1
        assert ("unsupported version 3" if "schema_version" in payload
                else "steps[2]") in error.call_args.args[1]
    else:
        error.assert_not_called()


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("saved", [False, True])
def test_export_editor_snapshot_without_saving(transfer_editor, tmp_path, monkeypatch, fail, saved):
    import json
    import os

    app = transfer_editor
    if not saved:
        app.current_profile = None
        app.target_window = None
    external = tmp_path / "shared-copy.json"
    external.write_bytes(b"previous file")
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", Mock(return_value=str(external)))
    before = (app.steps, app.target_window, app.current_profile, app.dirty)
    if fail:
        monkeypatch.setattr(os, "replace", Mock(side_effect=PermissionError("Read only")))

    app._export_profile()

    assert (app.steps, app.target_window, app.current_profile, app.dirty) == before
    app.profile_selection.set.assert_not_called()
    assert list(app.store.directory.iterdir()) == []
    assert list(tmp_path.glob(".clickcue-*.tmp")) == []
    if fail:
        assert external.read_bytes() == b"previous file"
    else:
        payload = json.loads(external.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 2
        assert Profile.from_dict(payload) == Profile(
            "Original" if saved else "Unsaved sequence",
            [Step("Edited", 10, 20, 0.5)], False, 7,
            TargetWindow("Edited target", 1, 2, 300, 200) if saved else None)
