from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from clickcue.gui import StepDialog
from clickcue.models import Step


@pytest.mark.parametrize(("field", "value", "valid"), [
    ("delay", "nan", False), ("delay", "inf", False),
    ("delay", "-inf", False), ("delay", "-1", False),
    ("delay", "bad", False), ("delay", "0", True), ("delay", "1.5", True),
    ("x", "1.5", False), ("y", "bad", False), ("name", " ", False),
])
def test_dialog_validates_fields_before_accepting(field, value, valid):
    values = {"name": "Click", "x": "-10", "y": "20", "delay": "1"}
    values[field] = value
    dialog = SimpleNamespace(
        fields={key: Mock(get=Mock(return_value=text)) for key, text in values.items()},
        wait_only=Mock(get=Mock(return_value=False)), result=None, destroy=Mock(),
    )
    with patch("clickcue.gui.messagebox.showerror") as error:
        StepDialog._accept(dialog)
    if valid:
        assert dialog.result == Step("Click", -10, 20, float(values["delay"]))
        dialog.destroy.assert_called_once()
        error.assert_not_called()
    else:
        assert dialog.result is None
        dialog.destroy.assert_not_called()
        assert field in error.call_args.args[1].lower()


def test_wait_only_dialog_ignores_disabled_coordinates():
    dialog = SimpleNamespace(
        fields={key: Mock(get=Mock(return_value=text)) for key, text in
                {"name": " Pause ", "x": "unused", "y": "unused", "delay": "0"}.items()},
        wait_only=Mock(get=Mock(return_value=True)), result=None, destroy=Mock(),
    )
    StepDialog._accept(dialog)
    assert dialog.result == Step("Pause", 0, 0, 0, True)
    dialog.destroy.assert_called_once()
