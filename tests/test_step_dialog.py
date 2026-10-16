import tkinter as tk
from tkinter import font
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
        mode=Mock(get=Mock(return_value="Click")), result=None, destroy=Mock(),
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
        mode=Mock(get=Mock(return_value="Fixed wait")), result=None, destroy=Mock(),
    )
    StepDialog._accept(dialog)
    assert dialog.result == Step("Pause", 0, 0, 0, True)
    dialog.destroy.assert_called_once()


@pytest.mark.parametrize("target_available", [False, True])
@pytest.mark.parametrize("font_size", [None, 13])
def test_target_wait_editor_labels_timeout_and_requires_target(
        tk_runtime, monkeypatch, target_available, font_size):
    default_font = font.nametofont("TkDefaultFont", root=tk_runtime)
    text_font = font.nametofont("TkTextFont", root=tk_runtime)
    original_font = default_font.actual()
    original_text_font = text_font.actual()
    if font_size is not None:
        default_font.configure(size=font_size)
        text_font.configure(size=font_size)
    parent = tk.Toplevel(tk_runtime)
    parent.update()
    errors = []
    monkeypatch.setattr("clickcue.gui.messagebox.showerror", lambda *args, **kw: errors.append(args))

    def edit(dialog):
        dialog.mode.set("Wait for target window")
        chooser = next(widget for widget in dialog.winfo_children()[0].winfo_children()
                       if widget.winfo_class() == "TCombobox")
        chooser.event_generate("<<ComboboxSelected>>")
        dialog.update()
        assert dialog.timing_label.cget("text") == "Timeout (s)"
        assert all(entry.instate(["disabled"]) for entry in dialog.coordinate_entries)
        for widget in dialog.winfo_children()[0].winfo_children():
            assert widget.winfo_x() >= 0 and widget.winfo_y() >= 0
            assert widget.winfo_rootx() + widget.winfo_width() <= (
                dialog.winfo_rootx() + dialog.winfo_width())
            assert widget.winfo_rooty() + widget.winfo_height() <= (
                dialog.winfo_rooty() + dialog.winfo_height())
        assert dialog.timing_label.winfo_width() >= default_font.measure("Timeout (s)")
        chooser_font = font.Font(root=dialog, font=chooser.cget("font"))
        assert chooser.winfo_width() >= chooser_font.measure("Wait for target window") + 20
        dialog.fields["name"].set("Available")
        dialog.fields["delay"].set("0")
        dialog._accept()
        assert dialog.result is None
        dialog.fields["delay"].set("2.5")
        dialog._accept()
        if not target_available:
            assert dialog.result is None
            assert "target" in errors[-1][1].lower()
            dialog.destroy()

    monkeypatch.setattr(tk.Toplevel, "wait_window", edit)
    try:
        dialog = StepDialog(parent, "Add step", target_available=target_available)
    finally:
        parent.destroy()
        default_font.configure(**original_font)
        text_font.configure(**original_text_font)
    if target_available:
        assert dialog.result == Step("Available", 0, 0, 0, True,
                                     "target_window_ready", 2.5)
        assert len(errors) == 1
