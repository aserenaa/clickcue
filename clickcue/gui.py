"""Tkinter GUI for ClickCue."""

import queue
import sys
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from threading import Lock
from tkinter import filedialog, messagebox, simpledialog, ttk

from pynput import keyboard, mouse

from .events import EventQueue
from .hotkeys import (FUNCTION_KEYS, HotkeySettings, create_hotkey_listener,
                      load_hotkeys, save_hotkeys)
from .models import Step, TargetWindow
from .platform_support import (accessibility_trusted, find_window,
                               list_windows, window_info_at)
from .playback import Player
from .preparation import PlaybackPreparation
from .profiles import Profile, ProfileStore, read_profile, write_profile

APP_NAME = "ClickCue"
EVENT_POLL_MS = 80
MAX_EVENTS_PER_POLL = 100
HISTORY_LIMIT = 100
SequenceSnapshot = tuple[list[Step], list[int]]


class StepDialog(tk.Toplevel):
    """Modal dialog to create or edit a step."""

    def __init__(self, parent: tk.Tk, title: str, step: Step | None = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: Step | None = None

        body = ttk.Frame(self, padding=12)
        body.grid()
        self.fields = {
            "name": tk.StringVar(value=step.name if step else ""),
            "x": tk.StringVar(value=str(step.x) if step else "0"),
            "y": tk.StringVar(value=str(step.y) if step else "0"),
            "delay": tk.StringVar(value=str(step.delay) if step else "1.0"),
        }
        rows = [("Name", "name"), ("X", "x"), ("Y", "y"),
                ("Delay (s)", "delay")]
        self.coordinate_entries = []
        for row, (label, key) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(body, textvariable=self.fields[key], width=22)
            entry.grid(row=row, column=1, pady=2, padx=(8, 0))
            if key in ("x", "y"):
                self.coordinate_entries.append(entry)
            if row == 0:
                entry.focus_set()

        self.wait_only = tk.BooleanVar(value=step.wait_only if step else False)
        ttk.Checkbutton(body, text="Wait only (no click)",
                        variable=self.wait_only,
                        command=self._update_coordinates).grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._update_coordinates()

        buttons = ttk.Frame(body)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(buttons, text="OK", command=self._accept).grid(
            row=0, column=0, padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(
            row=0, column=1, padx=4)

        self.bind("<Return>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.grab_set()
        self.wait_window()

    def _update_coordinates(self) -> None:
        for entry in self.coordinate_entries:
            entry.configure(state="disabled" if self.wait_only.get() else "normal")

    def _accept(self) -> None:
        try:
            step = Step.from_fields(
                *(self.fields[field].get() for field in ("name", "x", "y", "delay")),
                wait_only=self.wait_only.get())
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self)
            return
        self.result = step
        self.destroy()


class WindowPickerDialog(tk.Toplevel):
    """Modal dialog listing open windows to anchor a profile to."""

    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("Choose target window")
        self.resizable(False, False)
        self.result: TargetWindow | None = None
        self.windows = list_windows()

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Clicks will be anchored to this window's "
                             "position and size:").pack(anchor="w")

        self.listbox = tk.Listbox(body, width=48, height=12)
        for window in self.windows:
            self.listbox.insert(
                "end", f"{window.app}  ({window.width}×{window.height})")
        self.listbox.pack(pady=(8, 0))
        self.listbox.bind("<Double-1>", lambda _event: self._accept())
        if self.windows:
            self.listbox.selection_set(0)

        buttons = ttk.Frame(body)
        buttons.pack(pady=(10, 0))
        ttk.Button(buttons, text="OK", command=self._accept).grid(
            row=0, column=0, padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(
            row=0, column=1, padx=4)

        self.bind("<Return>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.grab_set()
        self.wait_window()

    def _accept(self) -> None:
        selection = self.listbox.curselection()
        if selection:
            self.result = self.windows[selection[0]]
        self.destroy()


class ClickCueApp:
    def __init__(self, root: tk.Tk, start_listener: bool = True):
        self.root = root
        self.root.minsize(620, 540)

        self.steps: list[Step] = []
        self._undo_history: list[SequenceSnapshot] = []
        self._redo_history: list[SequenceSnapshot] = []
        self.store = ProfileStore()
        self.player: Player | None = None
        self._preparation: PlaybackPreparation | None = None
        self._closed = False
        self.capture_armed = False
        self.current_profile: str | None = None
        self.imported_profile_name: str | None = None
        self.target_window: TargetWindow | None = None
        self.dirty = False
        self._loading = False
        self._blocked_status: str | None = None
        self._highlighted_step: int | None = None
        self.events: queue.Queue = EventQueue()
        self.hotkeys = load_hotkeys()
        self._hotkey_lock = Lock()
        self._pressed_keys: set = set()

        self.loop_forever = tk.BooleanVar(value=True)
        self.repetitions = tk.StringVar(value="10")
        self.status_text = tk.StringVar(value="Stopped")
        self.profile_selection = tk.StringVar()
        self.target_text = tk.StringVar()

        self._build_ui()
        shortcuts = ((("<Command-z>", False), ("<Command-Shift-Z>", True))
                     if sys.platform == "darwin" else
                     (("<Control-z>", False), ("<Control-y>", True)))
        for shortcut, redo in shortcuts:
            self.root.bind(shortcut, lambda event, redo=redo:
                           self._history_shortcut(event, redo))
        self._refresh_profile_list()
        self._update_title()
        self._update_target_text()

        self.loop_forever.trace_add("write", self._on_settings_changed)
        self.repetitions.trace_add("write", self._on_settings_changed)

        self.keyboard_listener = None
        if start_listener:
            # Listener callbacks run on the listener thread; they only enqueue
            # events, which the Tk main loop consumes in _poll_events.
            self.keyboard_listener = create_hotkey_listener(
                on_press=self._on_key, on_release=self._on_key_release)
            self.keyboard_listener.start()
            self.root.after(400, self._check_permissions)

        self.root.after(EVENT_POLL_MS, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        profile_bar = ttk.LabelFrame(main, text="Profile", padding=8)
        profile_bar.pack(fill="x")
        profile_controls = ttk.Frame(profile_bar)
        profile_controls.pack(fill="x")
        self.profile_combo = ttk.Combobox(
            profile_controls, textvariable=self.profile_selection,
            state="readonly", width=24)
        self.profile_combo.pack(side="left")
        self.profile_combo.bind("<<ComboboxSelected>>",
                                lambda _event: self._open_selected_profile())
        for label, command in [("New", self._new_profile),
                               ("Save", self._save_profile),
                               ("Save As…", self._save_profile_as),
                               ("Delete", self._delete_profile)]:
            ttk.Button(profile_controls, text=label, command=command).pack(
                side="left", padx=(6, 0))
        transfer_controls = ttk.Frame(profile_bar)
        transfer_controls.pack(fill="x", pady=(6, 0))
        for label, command in [("Import…", self._import_profile),
                               ("Export…", self._export_profile)]:
            ttk.Button(transfer_controls, text=label, command=command).pack(
                side="left", padx=(0, 6))
        self.profile_errors_button = ttk.Button(
            profile_bar, command=self._show_profile_errors)

        target_bar = ttk.LabelFrame(main, text="Target window", padding=8)
        target_bar.pack(fill="x", pady=(10, 0))
        ttk.Label(target_bar, textvariable=self.target_text).pack(side="left")
        ttk.Button(target_bar, text="Clear",
                   command=self._clear_target_window).pack(side="right")
        ttk.Button(target_bar, text="Choose…",
                   command=self._choose_target_window).pack(
            side="right", padx=(0, 4))

        table_frame = ttk.Frame(main)
        table_frame.pack(fill="both", expand=True, pady=(10, 0))
        columns = ("index", "name", "action", "x", "y", "delay")
        self.tree = ttk.Treeview(table_frame, columns=columns,
                                 show="headings", selectmode="extended")
        for column, heading, width in [("index", "#", 36),
                                       ("name", "Name", 220),
                                       ("action", "Action", 60),
                                       ("x", "X", 70), ("y", "Y", 70),
                                       ("delay", "Delay (s)", 80)]:
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width,
                             anchor="w" if column == "name" else "center")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical",
                                  command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self._edit_step())

        step_bar = ttk.Frame(main)
        step_bar.pack(fill="x", pady=(8, 0))
        self.capture_button = ttk.Button(
            step_bar, text=f"Capture ({self.hotkeys.capture})",
            command=self._arm_capture)
        self.capture_button.pack(side="left", padx=(0, 4))
        for label, command in [("Add", self._add_step),
                               ("Edit", self._edit_step),
                               ("Copy", self._copy_step),
                               ("Remove", self._remove_step),
                               ("▲", lambda: self._move_step(-1)),
                               ("▼", lambda: self._move_step(1))]:
            ttk.Button(step_bar, text=label, command=command).pack(
                side="left", padx=(0, 4))

        history_bar = ttk.Frame(main)
        history_bar.pack(fill="x", pady=(4, 0))
        for label, command in [("Undo", self._undo), ("Redo", self._redo)]:
            ttk.Button(history_bar, text=label, command=command).pack(
                side="left", padx=(0, 4))

        loop_bar = ttk.LabelFrame(main, text="Playback", padding=8)
        loop_bar.pack(fill="x", pady=(10, 0))
        ttk.Radiobutton(loop_bar, text="Loop forever",
                        variable=self.loop_forever, value=True).pack(side="left")
        ttk.Radiobutton(loop_bar, text="Repeat", variable=self.loop_forever,
                        value=False).pack(side="left", padx=(16, 4))
        ttk.Spinbox(loop_bar, from_=1, to=99999, textvariable=self.repetitions,
                    width=6).pack(side="left")
        ttk.Label(loop_bar, text="times").pack(side="left", padx=(4, 0))

        control_bar = ttk.Frame(main)
        control_bar.pack(fill="x", pady=(10, 0))
        self.start_button = ttk.Button(control_bar, text=f"▶ Start ({self.hotkeys.toggle})",
                                       command=self._toggle_playback)
        self.start_button.pack(side="left")
        ttk.Label(control_bar, textvariable=self.status_text).pack(
            side="left", padx=(12, 0))

        hotkey_bar = ttk.LabelFrame(main, text="Global hotkeys (this machine)", padding=8)
        hotkey_bar.pack(fill="x", pady=(10, 0))
        self.toggle_key = tk.StringVar(value=self.hotkeys.toggle)
        self.capture_key = tk.StringVar(value=self.hotkeys.capture)
        for label, variable in (("Start/stop", self.toggle_key),
                                ("Capture", self.capture_key)):
            ttk.Label(hotkey_bar, text=label).pack(side="left", padx=(0, 4))
            ttk.Combobox(hotkey_bar, textvariable=variable, values=FUNCTION_KEYS,
                         state="readonly", width=5).pack(side="left", padx=(0, 12))
        self.hotkey_apply_button = ttk.Button(
            hotkey_bar, text="Apply", command=self._apply_hotkeys)
        self.hotkey_apply_button.pack(side="left")
        self.hotkey_hint = ttk.Label(main, text=self._hint_text(), wraplength=580,
                                     foreground="gray")
        self.hotkey_hint.pack(fill="x", pady=(10, 0))

    def _hint_text(self) -> str:
        hotkeys = (f"Global hotkeys: {self.hotkeys.toggle} start/stop, "
                   f"{self.hotkeys.capture} capture cursor position. "
                   "Apply changes while stopped and capture is not armed.")
        if sys.platform == "darwin":
            return (f"{hotkeys} Requires Accessibility and Input Monitoring "
                    "permissions (System Settings → Privacy & Security) for "
                    f"the app running Python. On Mac keyboards use Fn+{self.hotkeys.toggle}/"
                    f"Fn+{self.hotkeys.capture} "
                    "if the F-keys control brightness or volume.")
        return hotkeys

    def _update_title(self) -> None:
        name = self.current_profile or "unsaved sequence"
        marker = " *" if self.dirty else ""
        self.root.title(f"{APP_NAME} — {name}{marker}")

    def _update_target_text(self) -> None:
        window = self.target_window
        if window is None:
            self.target_text.set("None — detected from the first step on save")
        elif window.has_bounds:
            self.target_text.set(
                f"{window.app} — {window.width}×{window.height} "
                f"at ({window.x}, {window.y})")
        else:
            self.target_text.set(f"{window.app} (no saved bounds)")

    # ------------------------------------------------------- background events

    def _apply_hotkeys(self) -> None:
        if self.player is not None or self.capture_armed or self._preparation is not None:
            messagebox.showinfo("Hotkeys", "Stop playback and finish capture before "
                                "changing hotkeys.", parent=self.root)
            return
        try:
            settings = HotkeySettings(self.toggle_key.get(), self.capture_key.get())
            # Serialize configuration with callbacks, retaining held keys until
            # release so changing bindings cannot turn a repeat into a new press.
            with self._hotkey_lock:
                save_hotkeys(settings)
                self.hotkeys = settings
        except (ValueError, OSError) as exc:
            messagebox.showerror("Hotkeys not changed", str(exc), parent=self.root)
            return
        self.start_button.configure(text=f"▶ Start ({settings.toggle})")
        self.capture_button.configure(text=f"Capture ({settings.capture})")
        self.hotkey_hint.configure(text=self._hint_text())
        self.status_text.set("Hotkeys saved for this machine")

    def _on_key(self, key) -> None:
        with self._hotkey_lock:
            if key not in (getattr(keyboard.Key, name.lower()) for name in FUNCTION_KEYS):
                return
            if key in self._pressed_keys:
                return
            self._pressed_keys.add(key)
            if key == getattr(keyboard.Key, self.hotkeys.toggle.lower()):
                self.events.put(("hotkey_toggle",))
            elif key == getattr(keyboard.Key, self.hotkeys.capture.lower()):
                position = mouse.Controller().position
                self.events.put(("hotkey_capture", int(position[0]), int(position[1])))

    def _on_key_release(self, key) -> None:
        with self._hotkey_lock:
            self._pressed_keys.discard(key)

    def _poll_events(self) -> None:
        try:
            for _ in range(MAX_EVENTS_PER_POLL):
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(EVENT_POLL_MS, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "prepared":
            self._finish_preparation(event[1])
        elif kind == "hotkey_toggle":
            self._toggle_playback()
        elif kind == "hotkey_capture":
            if self.capture_armed:
                self.capture_armed = False
                self._refresh_target_bounds()
                self._add_step(prefill=Step("New step", event[1], event[2], 1.0))
        elif kind in ("waiting", "playing"):
            _, repetition, index, name = event[:4]
            total = "∞" if self.loop_forever.get() else self.repetitions.get()
            countdown = f" — waiting {event[4]:.1f}s" if kind == "waiting" else ""
            self.status_text.set(
                f"Playing — run {repetition}/{total}, "
                f"step {index + 1}: {name}{countdown}")
            self._highlight_step(index)
        elif kind == "stopped":
            self.player = None
            self.start_button.configure(text=f"▶ Start ({self.hotkeys.toggle})")
            self.status_text.set(self._blocked_status or "Stopped")
            self._blocked_status = None
            self._highlight_step(None)
        elif kind == "blocked":
            _, step_name, covering, x, y = event
            self._blocked_status = (
                f"Stopped — target not found at ({x}, {y}) for “{step_name}”; "
                f"detected: {covering}")
        elif kind == "error":
            hint = ""
            if sys.platform == "darwin":
                hint = ("\n\nCheck that the app running Python has the "
                        "Accessibility permission in System Settings → "
                        "Privacy & Security.")
            messagebox.showerror("Click failed", f"{event[1]}{hint}")

    def _check_permissions(self) -> None:
        if accessibility_trusted(prompt=True):
            return
        self.status_text.set("⚠ Accessibility permission missing — "
                             "clicks will not be delivered")
        messagebox.showwarning(
            "Accessibility permission required",
            "macOS is silently blocking synthesized clicks.\n\n"
            "1. Open System Settings → Privacy & Security → Accessibility\n"
            "2. Enable the app you run Python from (Terminal, iTerm, …)\n"
            "3. Quit this app and start it again\n\n"
            "For global hotkeys, also enable that app under "
            "Input Monitoring.",
            parent=self.root)

    # -------------------------------------------------------------- target

    def _choose_target_window(self) -> None:
        dialog = WindowPickerDialog(self.root)
        if dialog.result:
            self.target_window = dialog.result
            self._mark_dirty()
            self._update_target_text()
            self.status_text.set(
                f"Anchored to “{dialog.result.app}” — capture your steps "
                "without moving that window")

    def _clear_target_window(self) -> None:
        if self.target_window is None:
            return
        self.target_window = None
        self._mark_dirty()
        self._update_target_text()

    def _refresh_target_bounds(self) -> None:
        """Keep stored bounds in sync with the window at capture time."""
        if self.target_window is None:
            return
        current = find_window(self.target_window.app)
        if current and not current.same_bounds(self.target_window):
            self.target_window = current
            self._mark_dirty()
            self._update_target_text()

    # ------------------------------------------------------------- playback

    def _toggle_playback(self) -> None:
        if self._closed:
            return
        if self._preparation is not None:
            self._preparation.windows.cancelled.set()
            self.start_button.configure(text="Cancelling…", state="disabled")
            self.status_text.set("Cancelling preparation…")
            return
        if self.player is not None:
            self.player.stop()
            return
        if not self.steps:
            messagebox.showinfo("No steps",
                                "Add at least one step to the sequence.")
            return
        self._preparation = PlaybackPreparation(
            self.steps, self.target_window, self.loop_forever.get(),
            self._repetition_count(), self.events)
        self.start_button.configure(text=f"■ Stop ({self.hotkeys.toggle})")
        self.status_text.set("Preparing…")
        self._preparation.start()

    def _finish_preparation(self, preparation: PlaybackPreparation) -> None:
        if self._closed or preparation is not self._preparation:
            return
        windows = preparation.windows
        prepared = preparation.result
        accepted = not windows.cancelled.is_set()
        if accepted and preparation.error is not None:
            accepted = False
            messagebox.showerror("Preparation failed", str(preparation.error))
        if accepted and prepared is not None and prepared.missing_target:
            accepted = messagebox.askyesno(
                "Target window not found",
                f"No window of “{windows.anchor.app}” is open.\n\n"
                "Play with the saved absolute coordinates anyway?")
        # Dialogs run a nested Tk loop; Stop or Close may have occurred there.
        if self._closed or preparation is not self._preparation:
            return
        self._preparation = None
        self.start_button.configure(text=f"▶ Start ({self.hotkeys.toggle})", state="normal")
        if not accepted or windows.cancelled.is_set() or prepared is None:
            self.status_text.set("Stopped")
            return
        self._blocked_status = None
        self.player = Player(prepared.steps, preparation.loop_forever,
                             preparation.repetitions, self.events,
                             guard=windows.guard)
        self.player.start()
        self.start_button.configure(text=f"■ Stop ({self.hotkeys.toggle})")
        self.status_text.set(prepared.status)

    def _repetition_count(self) -> int:
        try:
            return max(1, int(self.repetitions.get()))
        except ValueError:
            return 1

    # ----------------------------------------------------------------- steps

    def _sequence_snapshot(self) -> SequenceSnapshot:
        return ([replace(step) for step in self.steps],
                sorted(self.tree.index(item) for item in self.tree.selection()))

    def _record_edit(self, before: SequenceSnapshot) -> None:
        if before[0] == self.steps:
            return
        self._undo_history.append(before)
        del self._undo_history[:-HISTORY_LIMIT]
        self._redo_history.clear()
        self._mark_dirty()

    def _reset_history(self) -> None:
        self._undo_history.clear()
        self._redo_history.clear()

    def _undo(self) -> None:
        self._restore_history(self._undo_history, self._redo_history)

    def _redo(self) -> None:
        self._restore_history(self._redo_history, self._undo_history)

    def _restore_history(self, source: list[SequenceSnapshot],
                         destination: list[SequenceSnapshot]) -> None:
        if not source or self.player is not None or self.capture_armed:
            return
        destination.append(self._sequence_snapshot())
        steps, selection = source.pop()
        self.steps = [replace(step) for step in steps]
        self._refresh_table()
        children = self.tree.get_children()
        self.tree.selection_set([children[index] for index in selection])
        if selection:
            self.tree.see(children[selection[0]])
        # Settings/target edits are outside history: never infer a clean profile.
        self._mark_dirty()

    def _history_shortcut(self, event, redo: bool) -> str | None:
        widget = event.widget
        if (widget.winfo_toplevel() != self.root
                or self.root.grab_current() is not None
                or widget.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox",
                                            "TSpinbox", "TCombobox")):
            return None
        (self._redo if redo else self._undo)()
        return "break"

    def _arm_capture(self) -> None:
        self.capture_armed = True
        self.status_text.set("Capture armed: move the cursor to the target "
                             f"point and press {self.hotkeys.capture}")

    def _add_step(self, prefill: Step | None = None) -> None:
        dialog = StepDialog(self.root, "Add step", prefill)
        if dialog.result:
            before = self._sequence_snapshot()
            self.steps.append(dialog.result)
            self._record_edit(before)
            self._refresh_table()

    def _edit_step(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        dialog = StepDialog(self.root, "Edit step", self.steps[index])
        if dialog.result and dialog.result != self.steps[index]:
            before = self._sequence_snapshot()
            self.steps[index] = dialog.result
            self._record_edit(before)
            self._refresh_table(select=index)

    def _remove_step(self) -> None:
        indices = sorted(self.tree.index(item) for item in self.tree.selection())
        if not indices:
            return
        before = self._sequence_snapshot()
        for index in reversed(indices):
            del self.steps[index]
        self._record_edit(before)
        self._refresh_table()

    def _copy_step(self) -> None:
        indices = sorted(self.tree.index(item) for item in self.tree.selection())
        if not indices:
            return
        before = self._sequence_snapshot()
        copies = [replace(self.steps[index]) for index in indices]
        insert_at = indices[-1] + 1
        self.steps[insert_at:insert_at] = copies
        self._record_edit(before)
        self._refresh_table()
        children = self.tree.get_children()
        self.tree.selection_set(children[insert_at:insert_at + len(copies)])
        self.tree.see(children[insert_at])

    def _move_step(self, offset: int) -> None:
        indices = {self.tree.index(item) for item in self.tree.selection()}
        if not indices:
            return
        before = self._sequence_snapshot()
        moved_to = None
        # Work from the leading edge so adjacent selected rows move together.
        for index in sorted(indices, reverse=offset > 0):
            target = index + offset
            if not 0 <= target < len(self.steps) or target in indices:
                continue
            self.steps[index], self.steps[target] = (self.steps[target],
                                                     self.steps[index])
            indices.remove(index)
            indices.add(target)
            moved_to = target
        if moved_to is None:
            return
        self._record_edit(before)
        self._refresh_table()
        children = self.tree.get_children()
        self.tree.selection_set([children[index] for index in sorted(indices)])
        self.tree.see(children[moved_to])

    def _selected_index(self) -> int | None:
        selection = self.tree.selection()
        return self.tree.index(selection[0]) if selection else None

    def _refresh_table(self, select: int | None = None) -> None:
        # Rebuilt rows must be highlighted again, even at the same index.
        self._highlighted_step = None
        self.tree.delete(*self.tree.get_children())
        for index, step in enumerate(self.steps):
            self.tree.insert("", "end", values=(index + 1, step.name,
                                                "Wait" if step.wait_only else "Click",
                                                "—" if step.wait_only else step.x,
                                                "—" if step.wait_only else step.y,
                                                step.delay))
        if select is not None and 0 <= select < len(self.steps):
            self.tree.selection_set(self.tree.get_children()[select])

    def _highlight_step(self, index: int | None) -> None:
        if index is not None and index == self._highlighted_step:
            return
        self._highlighted_step = None
        children = self.tree.get_children()
        self.tree.selection_remove(*children)
        if index is not None and 0 <= index < len(children):
            item = children[index]
            self.tree.selection_set(item)
            self.tree.focus(item)
            self.tree.see(item)
            self._highlighted_step = index

    def _mark_dirty(self) -> None:
        if not self._loading:
            self.dirty = True
            self._update_title()

    def _on_settings_changed(self, *_args) -> None:
        self._mark_dirty()

    # -------------------------------------------------------------- profiles

    def _refresh_profile_list(self) -> None:
        self.profile_combo.configure(values=self.store.list_names())
        self.profile_selection.set(self.current_profile or "")
        if self.store.list_errors:
            self.profile_errors_button.configure(
                text=f"Profile errors ({len(self.store.list_errors)})")
            self.profile_errors_button.pack(anchor="w", pady=(6, 0))
        else:
            self.profile_errors_button.pack_forget()

    def _show_profile_errors(self) -> None:
        self._refresh_profile_list()
        if not self.store.list_errors:
            return
        details = "\n".join(f"{name}: {reason}"
                            for name, reason in self.store.list_errors.items())
        messagebox.showwarning(
            "Profile errors",
            f"Some files in {self.store.directory} could not be listed:\n\n"
            f"{details}\n\n"
            "Files were left unchanged. Check their JSON, filenames and read "
            "permissions. Correct them, then click Profile errors again to "
            "refresh the list.",
            parent=self.root)

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        return messagebox.askyesno(
            "Unsaved changes",
            "The current sequence has unsaved changes. Discard them?")

    def _open_selected_profile(self) -> None:
        name = self.profile_selection.get()
        if not name or name == self.current_profile:
            return
        if not self._confirm_discard():
            self.profile_selection.set(self.current_profile or "")
            return
        try:
            profile = self.store.load(name)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            messagebox.showerror("Load failed",
                                 f"Could not load profile: {exc}")
            return
        self._loading = True
        try:
            self._reset_history()
            self.steps = profile.steps
            self.loop_forever.set(profile.loop_forever)
            self.repetitions.set(str(profile.repetitions))
            self.target_window = profile.target_window
            self.current_profile = profile.name
            self.imported_profile_name = None
            self.dirty = False
        finally:
            self._loading = False
        self._refresh_table()
        self._update_title()
        self._update_target_text()
        self.status_text.set(f"Loaded profile “{name}”")

    def _new_profile(self) -> None:
        if not self._confirm_discard():
            return
        self._loading = True
        try:
            self._reset_history()
            self.steps = []
            self.loop_forever.set(True)
            self.repetitions.set("10")
            self.target_window = None
            self.current_profile = None
            self.imported_profile_name = None
            self.dirty = False
        finally:
            self._loading = False
        self._refresh_table()
        self._refresh_profile_list()
        self._update_title()
        self._update_target_text()
        self.status_text.set("New empty sequence")

    def _save_profile(self) -> None:
        if self.current_profile is None:
            self._save_profile_as()
        else:
            self._write_profile(self.current_profile)

    def _save_profile_as(self) -> None:
        name = simpledialog.askstring("Save profile", "Profile name:",
                                      parent=self.root,
                                      initialvalue=self.imported_profile_name or "")
        if not name or not name.strip():
            return
        name = name.strip()
        if name != self.current_profile and self.store.exists(name):
            if not messagebox.askyesno(
                    "Overwrite profile",
                    f"A profile named “{name}” already exists. Overwrite it?"):
                return
        self._write_profile(name)

    def _write_profile(self, name: str) -> None:
        first = next((step for step in self.steps if not step.wait_only), None)
        target_window = self.target_window
        if target_window is None and first is not None:
            target_window = window_info_at(first.x, first.y)
        profile = Profile(name=name, steps=list(self.steps),
                          loop_forever=self.loop_forever.get(),
                          repetitions=self._repetition_count(),
                          target_window=target_window)
        try:
            self.store.save(profile)
        except OSError as exc:
            messagebox.showerror(
                "Save failed",
                f"Could not save profile “{name}”: {exc}\n\n"
                "Your changes are still unsaved. Check the profile folder's "
                "permissions and available disk space, then try again.")
            return
        self.target_window = target_window
        self.current_profile = name
        self.imported_profile_name = None
        self.dirty = False
        self._refresh_profile_list()
        self._update_title()
        self._update_target_text()
        suffix = (f" (target: {self.target_window.app})"
                  if self.target_window else "")
        self.status_text.set(f"Saved profile “{name}”{suffix}")

    def _import_profile(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root, title="Import profile",
            filetypes=[("JSON profiles", "*.json"), ("All files", "*.*")])
        if not filename:
            return
        try:
            profile = read_profile(Path(filename))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Import failed", f"Could not import profile: {exc}",
                                 parent=self.root)
            return
        if not self._confirm_discard():
            return
        self._loading = True
        try:
            self.steps = profile.steps
            self._reset_history()
            self.loop_forever.set(profile.loop_forever)
            self.repetitions.set(str(profile.repetitions))
            self.target_window = profile.target_window
            self.current_profile = None
            self.imported_profile_name = profile.name
            self.dirty = True
        finally:
            self._loading = False
        self._refresh_table()
        self._refresh_profile_list()
        self._update_title()
        self._update_target_text()
        self.status_text.set(f"Imported “{profile.name}”; use Save to keep a local copy")

    def _export_profile(self) -> None:
        name = self.current_profile or self.imported_profile_name or "Unsaved sequence"
        filename = filedialog.asksaveasfilename(
            parent=self.root, title="Export profile", defaultextension=".json",
            initialfile="profile.json",
            filetypes=[("JSON profiles", "*.json"), ("All files", "*.*")])
        if not filename:
            return
        try:
            profile = Profile(name, list(self.steps), self.loop_forever.get(),
                              self._repetition_count(), self.target_window)
            write_profile(Path(filename), profile)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Export failed", f"Could not export profile: {exc}",
                                 parent=self.root)
            return
        self.status_text.set(f"Exported profile to {filename}")

    def _delete_profile(self) -> None:
        name = self.profile_selection.get() or self.current_profile
        if not name:
            return
        if not messagebox.askyesno("Delete profile",
                                   f"Delete profile “{name}”?"):
            return
        try:
            self.store.delete(name)
        except OSError as exc:
            messagebox.showerror(
                "Delete failed",
                f"Could not delete profile “{name}”: {exc}\n\n"
                "Check the profile folder's permissions, then try again.")
            return
        if name == self.current_profile:
            self.current_profile = None
            self.dirty = bool(self.steps)
        self._refresh_profile_list()
        self._update_title()
        self.status_text.set(f"Deleted profile “{name}”")

    # ------------------------------------------------------------------ close

    def _on_close(self) -> None:
        if self.dirty and not self._confirm_discard():
            return
        self._closed = True
        if self._preparation is not None:
            self._preparation.windows.cancelled.set()
            self._preparation = None
        if self.player is not None:
            self.player.stop()
        if self.keyboard_listener is not None:
            self.keyboard_listener.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ClickCueApp(root)
    root.mainloop()
