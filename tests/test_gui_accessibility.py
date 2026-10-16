"""Real Tk contracts, without OS input, listeners, or personal profiles."""

import tkinter as tk
from tkinter import font
from unittest.mock import Mock

import pytest

from clickcue.gui import ClickCueApp, StepDialog, WindowPickerDialog
from clickcue.hotkeys import HotkeySettings
from clickcue.models import Step, TargetWindow
from clickcue.profiles import ProfileStore


@pytest.fixture
def app(monkeypatch, tmp_path, tk_runtime):
    root = tk.Toplevel(tk_runtime)
    root.withdraw()
    monkeypatch.setattr("clickcue.gui.ProfileStore", lambda: ProfileStore(tmp_path))
    monkeypatch.setattr("clickcue.gui.load_hotkeys", HotkeySettings)
    # These must never be reached, even if a keyboard check regresses.
    for name in ("mouse.Controller", "keyboard.Controller", "Player",
                 "PlaybackPreparation", "create_hotkey_listener"):
        monkeypatch.setattr(f"clickcue.gui.{name}", Mock(side_effect=AssertionError(name)))
    instance = ClickCueApp(root, start_listener=False)
    yield instance
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    root.destroy()


@pytest.mark.parametrize("operation,expected", [
    ("load", "B"), ("edit", "C"), ("copy", "C"),
    ("move", "A"), ("undo", "C"),
])
def test_arrow_navigation_survives_table_rebuild(app, operation, expected):
    app.steps = [Step(name, 0, 0, 1) for name in "ABC"]
    app._refresh_table(select=1)
    if operation == "load":
        app._refresh_table()
    elif operation == "edit":
        app._refresh_table(select=1)
    elif operation == "copy":
        app._copy_step()
    elif operation == "move":
        app._move_step(-1)
    else:
        app._copy_step()
        app._undo()
    # Dispatch Tk's actual Down binding without focusing any native window.
    binding = app.tree.bind_class("Treeview", "<Key-Down>")
    app.tree.tk.eval(binding.replace("%W", str(app.tree)))
    selected = app.tree.selection()
    assert len(selected) == 1
    assert app.tree.item(selected[0], "values")[1] == expected


def test_keyboard_multiselection_can_remove_separate_rows(app):
    app.steps = [Step(name, 0, 0, 1) for name in "ABCDE"]
    app._refresh_table(select=0)

    def press(key):
        script = app.tree.bind(f"<Control-{key}>")
        for code in tk.Misc._subst_format:
            value = str(app.tree) if code == "%W" else key if code == "%K" else "0"
            script = script.replace(code, value)
        assert app.tree.tk.call("catch", script) == 3  # handled, stop propagation

    press("Down")
    press("Down")
    press("space")
    assert [app.tree.index(item) for item in app.tree.selection()] == [0, 2]
    app._remove_step()
    assert [step.name for step in app.steps] == list("BDE")
    press("a")
    app._copy_step()
    assert [step.name for step in app.steps] == list("BDEBDE")


@pytest.mark.parametrize("kind", ["step", "picker"])
def test_dialog_initial_focus_is_in_dialog_and_cancel_return_does_not_accept(
        app, monkeypatch, kind):
    monkeypatch.setattr("clickcue.gui.list_windows", lambda: [TargetWindow("Example")])
    app.root.deiconify()
    app.root.update()
    observed = []

    def inspect(dialog):
        dialog.update()
        focus = dialog.tk.call("focus", "-lastfor", str(dialog))
        observed.append(dialog.nametowidget(focus).winfo_class())
        buttons = dialog.winfo_children()[0].winfo_children()[-1].winfo_children()
        cancel = next(button for button in buttons if button.cget("text") == "Cancel")
        # Evaluate the binding path for Return on Cancel, without OS key events.
        for tag in cancel.bindtags():
            script = cancel.tk.call("bind", tag, "<Return>")
            if script:
                script = str(script)
                for code in tk.Misc._subst_format:
                    script = script.replace(code, str(cancel) if code == "%W" else "0")
                result = cancel.tk.call("catch", script)
                assert result in (0, 3)
                if result == 3 or not dialog.winfo_exists():
                    break

    monkeypatch.setattr(tk.Toplevel, "wait_window", inspect)
    dialog = (StepDialog(app.root, "Edit", Step("Example", 1, 2, 0))
              if kind == "step" else WindowPickerDialog(app.root))
    assert observed == ["TEntry" if kind == "step" else "Listbox"]
    assert dialog.result is None
    assert not dialog.winfo_exists()


def test_large_font_rows_fit_text_and_window_can_grow(app, request):
    default_font = font.nametofont("TkDefaultFont", root=app.root)
    original = default_font.actual()
    request.addfinalizer(lambda: default_font.configure(**original))
    app.steps = [Step("Readable row", 0, 0, 1)]
    app._refresh_table(select=0)
    app.root.deiconify()
    app.root.update()
    # Resize between the minimum and an actual mapped size accepted by the WM.
    # Do this before the large-font rebuild can change the window constraints.
    width, expanded_height = app.root.winfo_width(), app.root.winfo_height()
    minimum_height = app.root.minsize()[1]
    assert minimum_height < expanded_height
    table_heights = []
    for target_height in (minimum_height, expanded_height):
        settled = tk.BooleanVar(master=app.root)

        def configured(event, target_height=target_height, settled=settled):
            if event.widget == app.root and (event.width, event.height) == (width, target_height):
                settled.set(True)

        binding = app.root.bind("<Configure>", configured, add="+")
        deadline = app.root.after(2000, settled.set, True)
        app.root.geometry(f"{width}x{target_height}")
        if (app.root.winfo_width(), app.root.winfo_height()) != (width, target_height):
            app.root.wait_variable(settled)
        app.root.after_cancel(deadline)
        app.root.unbind("<Configure>", binding)
        app.root.update_idletasks()
        assert (app.root.winfo_width(), app.root.winfo_height()) == (width, target_height)
        row = app.tree.bbox(app.tree.get_children()[0])
        assert row and row[1] + row[3] <= app.tree.winfo_height()
        table_heights.append(app.tree.winfo_height())
    assert table_heights[1] > table_heights[0]
    assert app.start_button.winfo_rooty() + app.start_button.winfo_height() <= (
        app.root.winfo_rooty() + app.root.winfo_height())

    default_font.configure(size=18)
    # Rebuild after choosing a system font size, as at application startup.
    for child in app.root.winfo_children():
        child.destroy()
    app._build_ui()
    app._refresh_table(select=0)
    app.root.geometry("")
    app.root.update()
    row = app.tree.bbox(app.tree.get_children()[0])
    assert row and row[3] >= default_font.metrics("linespace") + 4
