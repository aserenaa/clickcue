from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp
from clickcue.hotkeys import HotkeySettings
from clickcue.models import Step, TargetWindow


@pytest.mark.parametrize("accept", [False, True])
def test_missing_target_requires_gui_decision_before_start(monkeypatch, accept):
    app = ClickCueApp.__new__(ClickCueApp)
    app.hotkeys = HotkeySettings()
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
    decision = Mock(return_value=accept)
    monkeypatch.setattr("clickcue.gui.messagebox.askyesno", decision)
    player = Mock()
    monkeypatch.setattr("clickcue.gui.Player", player)

    app._toggle_playback()

    decision.assert_called_once()
    assert "saved absolute coordinates" in decision.call_args.args[1]
    if accept:
        assert app.player is player.return_value
        app.player.start.assert_called_once()
        app.status_text.set.assert_called_once_with("Playing (target window not found)…")
    else:
        assert app.player is None
        player.assert_not_called()
        app.status_text.set.assert_not_called()
