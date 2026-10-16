from copy import deepcopy
from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp
from clickcue.models import Step, TargetWindow


@pytest.fixture
def app(monkeypatch):
    app = ClickCueApp.__new__(ClickCueApp)
    app.player = None
    app.steps = [Step("Click", 17, 29, 0)]
    app.target_window = TargetWindow("Target")
    app.loop_forever = Mock()
    app.loop_forever.get.return_value = False
    app._repetition_count = Mock(return_value=1)
    app.events = Mock()
    app.start_button = Mock()
    app.status_text = Mock()
    monkeypatch.setattr("clickcue.preparation.find_window", Mock(return_value=None))
    monkeypatch.setattr("clickcue.preparation.focus_window_at", Mock(return_value=None))
    app._mark_dirty = Mock()
    app._update_target_text = Mock()
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

    assert app.player is None
    player.assert_not_called()
    decision.assert_not_called()
    warning.assert_called_once()
    assert "Target" in warning.call_args.args[1]
    for action in ("Open", "Choose", "Clear"):
        assert action in warning.call_args.args[1]
    assert "target unavailable" in app.status_text.set.call_args.args[0].lower()
    assert app.steps == steps
    assert app.target_window == anchor
    app._mark_dirty.assert_not_called()
    app._update_target_text.assert_not_called()
    app.start_button.configure.assert_not_called()


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

    warning.assert_not_called()
    assert app.player is player.return_value
    app.player.start.assert_called_once()
    assert player.call_args.args[0] == app.steps
    assert app.status_text.set.call_args.args[0] == (
        "Playing waits…" if wait_only else "Playing…")
