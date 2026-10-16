"""Tkinter GUI for ClickCue."""

import queue
import sys
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from threading import Lock
from tkinter import filedialog, font, messagebox, simpledialog, ttk

from pynput import keyboard, mouse

from .diagnostics import show_diagnostics
from .events import EventQueue
from .hotkeys import (FUNCTION_KEYS, HotkeySettings, create_hotkey_listener,
                      load_hotkeys, save_hotkeys)
from .models import Step, TargetWindow
from .platform_support import (accessibility_trusted, configure_dpi_awareness,
                               find_window, list_windows, window_info_at)
from .playback import Player
from .preparation import PlaybackPreparation
from .preview import show_preview
from .profiles import Profile, ProfileStore, read_profile, write_profile
from .recording import ClickRecording

APP_NAME = "ClickCue"
EVENT_POLL_MS = 80
MAX_EVENTS_PER_POLL = 100
HISTORY_LIMIT = 100
SequenceSnapshot = tuple[list[Step], list[int]]


class StepDialog(tk.Toplevel):
    """Modal dialog to create or edit a step."""

    def __init__(self, parent: tk.Tk, title: str, step: Step | None = None,
                 *, target_available: bool = False):
        super().__init__(parent)
        self.title(title)
        self.result: Step | None = None
        self.target_available = target_available

        body = ttk.Frame(self, padding=12)
        body.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        self.fields = {
            "name": tk.StringVar(value=step.name if step else ""),
            "x": tk.StringVar(value=str(step.x) if step else "0"),
            "y": tk.StringVar(value=str(step.y) if step else "0"),
            "delay": tk.StringVar(value=str(step.timeout if step.wait_condition else step.delay)
                                  if step else "1.0"),
        }
        rows = [("Name", "name"), ("X", "x"), ("Y", "y"),
                ("Delay (s)", "delay")]
        self.coordinate_entries = []
        for row, (label, key) in enumerate(rows):
            label_widget = ttk.Label(body, text=label)
            label_widget.grid(row=row, column=0, sticky="w", pady=2)
            if key == "delay":
                self.timing_label = label_widget
            entry = ttk.Entry(body, textvariable=self.fields[key], width=22)
            entry.grid(row=row, column=1, sticky="ew", pady=2, padx=(8, 0))
            if key in ("x", "y"):
                self.coordinate_entries.append(entry)
            if row == 0:
                first_entry = entry

        mode = ("Wait for target window" if step and step.wait_condition else
                "Fixed wait" if step and step.wait_only else "Click")
        self.mode = tk.StringVar(value=mode)
        ttk.Label(body, text="Action").grid(row=len(rows), column=0, sticky="w", pady=(6, 0))
        chooser = ttk.Combobox(body, textvariable=self.mode, state="readonly",
                               values=("Click", "Fixed wait", "Wait for target window"),
                               width=24)
        chooser.grid(row=len(rows), column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        chooser.bind("<<ComboboxSelected>>", lambda _event: self._update_coordinates())
        self._update_coordinates()

        buttons = ttk.Frame(body)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(buttons, text="OK", command=self._accept).grid(
            row=0, column=0, padx=4)
        cancel = ttk.Button(buttons, text="Cancel", command=self.destroy)
        cancel.grid(row=0, column=1, padx=4)
        cancel.bind("<Return>", lambda _event: self.destroy() or "break")

        self.bind("<Return>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.wait_visibility()
        first_entry.focus_set()
        self.grab_set()
        self.wait_window()

    def _update_coordinates(self) -> None:
        for entry in self.coordinate_entries:
            entry.configure(state="normal" if self.mode.get() == "Click" else "disabled")
        self.timing_label.configure(text="Timeout (s)" if self.mode.get() == "Wait for target window"
                                    else "Delay (s)")

    def _accept(self) -> None:
        try:
            condition = ("target_window_ready" if self.mode.get() == "Wait for target window"
                         else None)
            step = Step.from_fields(
                *(self.fields[field].get() for field in ("name", "x", "y", "delay")),
                wait_only=self.mode.get() != "Click", wait_condition=condition)
            if condition and not self.target_available:
                raise ValueError("target_window: choose a target window before adding this wait")
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
        self.result: TargetWindow | None = None
        self.windows = list_windows()

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Clicks will be anchored to this window's "
                             "position and size:").pack(anchor="w")

        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.listbox = tk.Listbox(list_frame, width=48, height=12,
                                 exportselection=False)
        for window in self.windows:
            self.listbox.insert(
                "end", f"{window.app} - {window.title or window.native_id or 'window'}  "
                       f"({window.x}, {window.y}; {window.width}×{window.height})")
        vertical = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal",
                                   command=self.listbox.xview)
        self.listbox.configure(yscrollcommand=vertical.set,
                               xscrollcommand=horizontal.set)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.listbox.bind("<Double-1>", lambda _event: self._accept())
        if self.windows:
            self.listbox.selection_set(0)

        buttons = ttk.Frame(body)
        buttons.pack(pady=(10, 0))
        ttk.Button(buttons, text="OK", command=self._accept).grid(
            row=0, column=0, padx=4)
        cancel = ttk.Button(buttons, text="Cancel", command=self.destroy)
        cancel.grid(row=0, column=1, padx=4)
        cancel.bind("<Return>", lambda _event: self.destroy() or "break")

        self.bind("<Return>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.wait_visibility()
        self.listbox.focus_set()
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

        self.steps: list[Step] = []
        self._undo_history: list[SequenceSnapshot] = []
        self._redo_history: list[SequenceSnapshot] = []
        self.store = ProfileStore()
        self.player: Player | None = None
        self._preparation: PlaybackPreparation | None = None
        self._run_indices: list[int] | None = None
        self._pending_control: str | None = None
        self._closed = False
        self._closing = False
        self.capture_armed = False
        self._recording: ClickRecording | None = None
        # Read-only modal dialogs must consume queued recording-start hotkeys
        # before releasing this token. Stop bypasses it on the callback thread.
        self._recording_modal = False
        self._preview_active = False
        self.current_profile: str | None = None
        self.imported_profile_name: str | None = None
        self.target_window: TargetWindow | None = None
        self.dirty = False
        self._loading = False
        self._blocked_status: str | None = None
        self._last_stop = "None recorded this session"
        self._last_error = "None recorded this session"
        self._highlighted_step: int | None = None
        self.events: queue.Queue = EventQueue()
        self.hotkeys = load_hotkeys()
        self._hotkey_lock = Lock()
        self._pressed_keys: set = set()
        self._held_modifiers: set[str] = set()

        self.loop_forever = tk.BooleanVar(value=True)
        self.repetitions = tk.StringVar(value="10")
        self.status_text = tk.StringVar(value="Stopped")
        self.profile_selection = tk.StringVar()
        self.target_text = tk.StringVar()

        self._run_settings: tuple[bool, str] | None = None
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
            button = ttk.Button(transfer_controls, text=label, command=command)
            button.pack(side="left", padx=(0, 6))
            if command == self._import_profile:
                self.import_button = button
        self.profile_errors_button = ttk.Button(
            profile_bar, command=self._show_profile_errors)

        target_bar = ttk.LabelFrame(main, text="Target window", padding=8)
        target_bar.pack(fill="x", pady=(10, 0))
        target_label = ttk.Label(target_bar, textvariable=self.target_text,
                                 wraplength=300)
        target_label.pack(side="left", fill="x", expand=True)
        target_label.bind("<Configure>", lambda event:
                          target_label.configure(wraplength=max(1, event.width)))
        ttk.Button(target_bar, text="Clear",
                   command=self._clear_target_window).pack(side="right")
        ttk.Button(target_bar, text="Choose…",
                   command=self._choose_target_window).pack(
            side="right", padx=(0, 4))

        table_frame = ttk.Frame(main)
        table_frame.pack(fill="both", expand=True, pady=(10, 0))
        columns = ("index", "name", "action", "x", "y", "delay")
        self.tree = ttk.Treeview(table_frame, columns=columns,
                                 show="headings", selectmode="extended", height=6)
        style = ttk.Style(self.root)
        tree_font = font.Font(root=self.root,
                              font=style.lookup("Treeview", "font") or "TkDefaultFont")
        row_height = tree_font.metrics("linespace") + 8
        style.configure("Treeview", rowheight=row_height)
        for column, heading, width in [("index", "#", 36),
                                       ("name", "Name", 220),
                                       ("action", "Action", max(60, tree_font.measure("Wait for target") + 16)),
                                       ("x", "X", 70), ("y", "Y", 70),
                                       ("delay", "Timing (s)", max(80, tree_font.measure("Timeout 999.9s") + 16))]:
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width,
                             anchor="w" if column == "name" else "center")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical",
                                  command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self._edit_step())
        for key in ("Up", "Down", "space", "a"):
            self.tree.bind(f"<Control-{key}>", self._step_selection_shortcut)

        # Allocate fixed controls first, retaining the table's earlier Tab order.
        footer = ttk.Frame(main)
        footer.pack(side="bottom", fill="x", before=table_frame)
        step_bar = ttk.Frame(footer)
        step_bar.pack(fill="x", pady=(4, 0))
        self.capture_button = ttk.Button(
            step_bar, text=f"Capture ({self.hotkeys.capture})",
            command=self._arm_capture)
        self.capture_button.pack(side="left", padx=(0, 4))
        for label, command in [("Add", self._add_step),
                               ("Edit", self._edit_step),
                               ("Copy", self._copy_step),
                               ("Remove", self._remove_step)]:
            ttk.Button(step_bar, text=label, command=command).pack(
                side="left", padx=(0, 4))

        history_bar = ttk.Frame(footer)
        history_bar.pack(fill="x", pady=(4, 0))
        for label, command in [("Move up", lambda: self._move_step(-1)),
                               ("Move down", lambda: self._move_step(1)),
                               ("Undo", self._undo), ("Redo", self._redo)]:
            ttk.Button(history_bar, text=label, command=command).pack(
                side="left", padx=(0, 4))

        record_bar = ttk.Frame(footer)
        record_bar.pack(fill="x", pady=(4, 0))
        self.record_button = ttk.Button(record_bar, text="Record clicks", command=self._arm_recording)
        self.record_button.pack(side="left", padx=(0, 4))
        self.review_recording_button = ttk.Button(
            record_bar, text="Review recording…", command=self._review_recording, state="disabled")
        self.review_recording_button.pack(side="left", padx=(0, 4))
        self.keep_recording_button = ttk.Button(
            record_bar, text="Keep recording", command=self._keep_recording, state="disabled")
        self.keep_recording_button.pack(side="left", padx=(0, 4))
        self.discard_recording_button = ttk.Button(
            record_bar, text="Discard", command=self._discard_recording, state="disabled")
        self.discard_recording_button.pack(side="left")

        loop_bar = ttk.LabelFrame(footer, text="Playback", padding=(8, 4))
        loop_bar.pack(fill="x", pady=(6, 0))
        ttk.Radiobutton(loop_bar, text="Loop forever",
                        variable=self.loop_forever, value=True).pack(side="left")
        ttk.Radiobutton(loop_bar, text="Repeat", variable=self.loop_forever,
                        value=False).pack(side="left", padx=(16, 4))
        ttk.Spinbox(loop_bar, from_=1, to=99999, textvariable=self.repetitions,
                    width=6).pack(side="left")
        ttk.Label(loop_bar, text="times").pack(side="left", padx=(4, 0))

        control_bar = ttk.Frame(footer)
        control_bar.pack(fill="x", pady=(6, 0))
        self.start_button = ttk.Button(control_bar, text=f"▶ Start ({self.hotkeys.toggle})",
                                       command=self._toggle_playback)
        self.start_button.pack(side="left")
        self.run_selected_button = ttk.Button(
            control_bar, text="Run selected steps", command=self._run_selected)
        self.run_selected_button.pack(side="left", padx=(6, 0))
        self.pause_button = ttk.Button(
            control_bar, text="Pause", command=self._toggle_pause, state="disabled")
        self.pause_button.pack(side="left", padx=(6, 0))

        ttk.Button(control_bar, text="Preview only…",
                   command=self._preview_sequence).pack(side="left", padx=(6, 0))
        ttk.Button(control_bar, text="Diagnostics…",
                   command=self._show_diagnostics).pack(side="left", padx=(6, 0))
        status_label = ttk.Label(footer, textvariable=self.status_text, wraplength=580)
        status_label.pack(fill="x", pady=(6, 0))
        status_label.bind("<Configure>", lambda event:
                          status_label.configure(wraplength=max(1, event.width)))

        hotkey_bar = ttk.LabelFrame(footer, text="Global hotkeys (this machine)", padding=(8, 4))
        hotkey_bar.pack(fill="x", pady=(6, 0))
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
        self.hotkey_hint = ttk.Label(footer, text=self._hint_text(), wraplength=580)
        self.hotkey_hint.pack(fill="x", pady=(4, 0))
        self.hotkey_hint.bind("<Configure>", lambda event:
                              self.hotkey_hint.configure(wraplength=max(1, event.width)))
        self.root.update_idletasks()
        # Keep controls and a small table viewport available on smaller displays.
        spare_rows = int(self.tree.cget("height")) - 1
        self.root.minsize(max(620, main.winfo_reqwidth()),
                          main.winfo_reqheight() - spare_rows * row_height)

        # Keep read-only export, profile diagnostics, and the control bar usable.
        editing_bars = (profile_controls, target_bar, step_bar, history_bar,
                        loop_bar, hotkey_bar)
        self._editing_controls = [self.import_button] + [
            widget for bar in editing_bars for widget in bar.winfo_children()
            if isinstance(widget, (ttk.Button, ttk.Combobox, ttk.Radiobutton,
                                   ttk.Spinbox))]

    def _editing_blocked(self) -> bool:
        return (self._closed or self._closing or self.player is not None
                or self._preparation is not None or getattr(self, "_recording", None) is not None)

    def _update_editing_controls(self) -> None:
        state = "disabled" if self._editing_blocked() else "!disabled"
        self.run_selected_button.state([state])
        self.pause_button.state(["!disabled" if self.player is not None
                                 and self._pending_control is None
                                 and not self._closing else "disabled"])
        if self.player is None:
            self.pause_button.configure(text="Pause")
        for widget in self._editing_controls:
            # Toggle only disabled, preserving readonly combobox state.
            widget.state([state])
        if hasattr(self, "record_button"):
            recording = self._recording
            self.record_button.state(["disabled" if self._editing_blocked()
                                      or self.capture_armed or self._preview_active else "!disabled"])
            review = (recording is not None and recording.finished.is_set()
                      and not self._closed and not self._closing and not self._preview_active)
            for widget in (self.review_recording_button, self.keep_recording_button,
                           self.discard_recording_button):
                widget.state(["!disabled" if review else "disabled"])
            if recording is not None:
                self.start_button.configure(
                    text=f"■ Stop ({self.hotkeys.toggle})",
                    state="disabled" if recording.finished.is_set() else "normal")

    def _hint_text(self) -> str:
        hotkeys = (f"{self.hotkeys.toggle} start/stop, "
                   f"{self.hotkeys.capture} capture cursor position. "
                   f"Record clicks: {self.hotkeys.capture} begins, "
                   f"{self.hotkeys.toggle} stops for review. "
                   "Apply while stopped; finish capture/recording first.")
        if sys.platform == "darwin":
            return (f"{hotkeys} macOS: enable Accessibility and Input Monitoring "
                    "for your Python launcher in System Settings → Privacy & Security. "
                    f"Use Fn+{self.hotkeys.toggle}/Fn+{self.hotkeys.capture} if needed.")
        return hotkeys

    def _show_diagnostics(self) -> None:
        window_support = ("Available on this platform; live target not checked"
                          if sys.platform in ("darwin", "win32") else
                          "Unsupported on this platform")
        target = self.target_window
        if target is None:
            target_details = "None saved (absolute coordinates)"
        else:
            bounds = (f"{target.width} x {target.height} at ({target.x}, {target.y})"
                      if target.has_bounds else "no saved bounds")
            target_details = f"{target.app}; {bounds}; live availability not checked"
        profile_errors = "\n".join(
            f"  {name}: {reason}" for name, reason in self.store.list_errors.items())
        show_diagnostics(self.root, "\n".join([
            f"Platform: {sys.platform}; Python {sys.version.split()[0]}",
            f"Mouse backend: {mouse.Controller.__module__}",
            f"Keyboard backend: {keyboard.Listener.__module__}",
            "Permissions: Unknown — diagnostics does not probe permissions. "
            "Backend selection does not prove input delivery or hotkey access.",
            ("macOS: Check Accessibility for input and Input Monitoring for "
             "global hotkeys in System Settings > Privacy & Security."
             if sys.platform == "darwin" else
             "Input delivery and global hotkey permissions are not verified."),
            f"Window lookup/guard support: {window_support}",
            f"Saved target: {target_details}",
            f"Current status: {self.status_text.get()}",
            f"Last stop: {self._last_stop}",
            f"Last playback/preparation error: {self._last_error}",
            f"Profile folder: {self.store.directory}",
            f"Profile listing errors (last refresh):\n{profile_errors or '  None recorded'}",
            "Use Profile errors to refresh listing failures. Diagnostics does not "
            "rescan files, send input, focus or move target windows.",
        ]))

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
        if self._editing_blocked() or self.capture_armed:
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
            # Retain only fixed modifier names, never typed characters. A
            # modifier already held when arming cannot create a plain click.
            for name in ("shift", "shift_l", "shift_r", "ctrl", "ctrl_l", "ctrl_r",
                         "alt", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r"):
                if key == getattr(keyboard.Key, name, None):
                    self._held_modifiers.add(name)
            recording = getattr(self, "_recording", None)
            if recording is not None:
                # Cut off at the global callback, not a later Tk poll. Never
                # enqueue a playback toggle or retain non-control key values.
                if key not in (getattr(keyboard.Key, self.hotkeys.toggle.lower()),
                               getattr(keyboard.Key, self.hotkeys.capture.lower())):
                    recording.stop("Keyboard input detected; only plain left clicks are supported")
                    return
            if key not in (getattr(keyboard.Key, name.lower()) for name in FUNCTION_KEYS):
                return
            if key in self._pressed_keys:
                return
            self._pressed_keys.add(key)
            if getattr(self, "_preview_active", False):
                return
            if recording is not None:
                if key == getattr(keyboard.Key, self.hotkeys.toggle.lower()):
                    recording.stop()
                    self.events.put(("record_stopping", recording))
                else:
                    if not self._recording_modal:
                        self.events.put(("record_begin", recording))
                return
            if key == getattr(keyboard.Key, self.hotkeys.toggle.lower()):
                self.events.put(("hotkey_toggle",))
            elif key == getattr(keyboard.Key, self.hotkeys.capture.lower()):
                position = mouse.Controller().position
                self.events.put(("hotkey_capture", int(position[0]), int(position[1])))

    def _on_key_release(self, key) -> None:
        with self._hotkey_lock:
            self._pressed_keys.discard(key)
            recording = getattr(self, "_recording", None)
            if (recording is not None
                    and key not in (getattr(keyboard.Key, self.hotkeys.toggle.lower()),
                                    getattr(keyboard.Key, self.hotkeys.capture.lower()))):
                recording.stop("Keyboard input detected; only plain left clicks are supported")
            if hasattr(self, "_held_modifiers"):
                for name in tuple(self._held_modifiers):
                    if key == getattr(keyboard.Key, name, None):
                        self._held_modifiers.discard(name)

    def _poll_events(self) -> None:
        if self._closed:
            return
        # Dialogs run a nested Tk loop. Keep polling there so a queued Stop
        # cancels the active preparation before its completion handler returns.
        self.root.after(EVENT_POLL_MS, self._poll_events)
        try:
            for _ in range(MAX_EVENTS_PER_POLL):
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if (getattr(self, "_preview_active", False)
                and kind in ("hotkey_toggle", "hotkey_capture")):
            return
        if kind == "prepared":
            self._finish_preparation(event[1])
        elif kind == "preview_closed":
            self._preview_active = False
            self._update_editing_controls()
        elif kind in ("record_started", "record_stopping", "record_finished"):
            recording = event[1]
            if self._closed or recording is not self._recording:
                return
            if kind == "record_finished":
                prefix = "Partial recording" if recording.reason else "Recording stopped"
                self.status_text.set(
                    f"{prefix}: {len(recording.steps)} completed clicks. "
                    f"{recording.reason + '. ' if recording.reason else ''}"
                    "Review, then Keep recording or Discard; nothing appended yet.")
                self._last_stop = self.status_text.get()
            elif kind == "record_started" and not recording.finished.is_set():
                self.status_text.set(f"Recording plain left clicks — {self.hotkeys.toggle} stops. "
                                     "Other actions stop with a partial recording.")
            elif kind == "record_stopping" and not recording.finished.is_set():
                self.status_text.set("Stopping recording; waiting for observation/listener cleanup…")
            self._update_editing_controls()
        elif kind == "record_begin":
            if (event[1] is self._recording and not self._closed and not self._closing
                    and not self._recording.finished.is_set()
                    and not self._preview_active and not self._recording_modal
                    and not self.root.tk.call("grab", "current", self.root)):
                self._recording.begin()
        elif kind == "record_modal_closed":
            if event[1] is self._recording_modal:
                self._recording_modal = False
        elif kind == "close_cancelled":
            self._closing = False
            self._update_editing_controls()
        elif kind == "preparation_stopped":
            _, preparation, status = event
            if self._closed or preparation is not self._preparation:
                return
            self._preparation = None
            self._run_indices = None
            self._run_settings = None
            self._update_editing_controls()
            self.start_button.configure(
                text=f"▶ Start ({self.hotkeys.toggle})", state="normal")
            self._last_stop = "Stopped" if preparation.windows.cancelled.is_set() else status
            self.status_text.set(self._last_stop)
        elif kind == "hotkey_toggle":
            self._toggle_playback()
        elif kind == "controlled":
            _, player, action = event
            if (self._closed or player is not self.player
                    or action != self._pending_control or action == "stop"):
                return
            # A cancelled close still needs this ack; _closing keeps controls locked.
            self._pending_control = None
            self._update_editing_controls()
            self.pause_button.configure(text="Resume" if player.paused else "Pause")
            self.status_text.set("Paused — Resume to continue or Stop to cancel"
                                 if player.paused else "Resuming…")
        elif kind == "hotkey_capture":
            if self.capture_armed and not self._editing_blocked():
                self.capture_armed = False
                self._update_editing_controls()
                self._refresh_target_bounds()
                self._add_step(prefill=Step("New step", event[1], event[2], 1.0))
        elif kind in ("waiting", "playing", "condition_waiting"):
            if self._pending_control is not None or (self.player is not None and self.player.paused):
                return
            _, repetition, index, name = event[:4]
            if self._run_indices is not None:
                index = self._run_indices[index]
            total = "∞" if self.loop_forever.get() else self.repetitions.get()
            countdown = f" — waiting {event[4]:.1f}s" if kind == "waiting" else ""
            if kind == "condition_waiting":
                countdown = f" — waiting for target window; timeout in {event[4]:.1f}s"
            self.status_text.set(
                f"Playing — run {repetition}/{total}, "
                f"step {index + 1}: {name}{countdown}")
            self._highlight_step(index)
        elif kind == "stopped":
            self.player = None
            self._pending_control = None
            self._run_indices = None
            self._run_settings = None
            self._update_editing_controls()
            self.start_button.configure(text=f"▶ Start ({self.hotkeys.toggle})", state="normal")
            self.status_text.set(self._blocked_status or "Stopped")
            self._last_stop = self._blocked_status or "Stopped (completion or cancellation)"
            self._blocked_status = None
            self._highlight_step(None)
        elif kind == "blocked":
            _, step_name, covering, x, y = event
            self._blocked_status = (
                f"Stopped — target not found at ({x}, {y}) for “{step_name}”; "
                f"detected: {covering}")
        elif kind == "error":
            self._last_error = str(event[1])
            self._blocked_status = self._blocked_status or f"Stopped — {event[1]}"
            hint = ""
            if sys.platform == "darwin":
                hint = ("\n\nCheck that the app running Python has the "
                        "Accessibility permission in System Settings → "
                        "Privacy & Security.")
            messagebox.showerror("Playback failed", f"{event[1]}{hint}")

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
        if self._editing_blocked():
            return
        dialog = WindowPickerDialog(self.root)
        if dialog.result and not self._editing_blocked():
            self.target_window = dialog.result
            self._mark_dirty()
            self._update_target_text()
            self.status_text.set(
                f"Anchored to “{dialog.result.app}” — capture your steps "
                "without moving that window")

    def _clear_target_window(self) -> None:
        if self._editing_blocked():
            return
        if self.target_window is None:
            return
        self.target_window = None
        self._mark_dirty()
        self._update_target_text()

    def _refresh_target_bounds(self) -> None:
        """Keep stored bounds in sync with the window at capture time."""
        if self._editing_blocked():
            return
        if self.target_window is None:
            return
        current = find_window(self.target_window)
        if current and not current.same_bounds(self.target_window):
            self.target_window = current
            self._mark_dirty()
            self._update_target_text()

    # ------------------------------------------------------------- playback

    def _preview_sequence(self) -> None:
        if self._closed or self._closing:
            return
        if (getattr(self, "_recording", None) is not None or self.player is not None or self.capture_armed
                or self._preparation is not None or self._preview_active):
            messagebox.showinfo("Preview only", "Stop playback and finish capture "
                                "before previewing.", parent=self.root)
            return
        if not any(not step.wait_only for step in self.steps):
            messagebox.showinfo("No click positions", "Add a click step to preview "
                                "its position. Empty and wait-only sequences "
                                "have no click positions.", parent=self.root)
            return
        self._preview_active = True
        try:
            show_preview(self.root, [replace(step) for step in self.steps])
        except (OverflowError, ValueError):
            messagebox.showerror(
                "Preview unavailable", "The recorded coordinates are too large to "
                "display. Edit the click coordinates and try previewing again. "
                "No input was sent.", parent=self.root)
        finally:
            # Retain the guard until pending modal hotkeys have been consumed.
            self.events.put(("preview_closed",))

    def _toggle_playback(self) -> None:
        if self._closed:
            return
        if getattr(self, "_recording", None) is not None:
            self._recording.stop()
            if not self._recording.finished.is_set():
                self._handle_event(("record_stopping", self._recording))
            return
        if self._preparation is not None:
            self._preparation.windows.cancelled.set()
            self.start_button.configure(text="Cancelling…", state="disabled")
            self.status_text.set("Cancelling preparation…")
            return
        if self.player is not None:
            self._request_player_control("stop")
            return
        self._start_playback()

    def _run_selected(self) -> None:
        if not self._editing_blocked():
            self._start_playback(selected_only=True)

    def _toggle_pause(self) -> None:
        if (self._closed or self._closing or self.player is None
                or self._pending_control is not None):
            return
        self._request_player_control("resume" if self.player.paused else "pause")

    def _request_player_control(self, action: str) -> None:
        if self._pending_control == "stop":
            return
        self._pending_control = action
        self.pause_button.state(["disabled"])
        if action == "stop":
            self.start_button.configure(text="Stopping…", state="disabled")
        self.status_text.set({"pause": "Pausing…", "resume": "Resuming…",
                              "stop": "Stopping…"}[action])
        self.player.request_control(action)

    def _start_playback(self, selected_only: bool = False) -> None:
        if (getattr(self, "_recording", None) is not None or self._closing or self._preview_active
                or self.root.tk.call("grab", "current", self.root)):
            return
        indices = list(range(len(self.steps)))
        if selected_only:
            selected = set(self.tree.selection())
            indices = [i for i, row in enumerate(self.tree.get_children()) if row in selected]
            if not indices:
                messagebox.showinfo("No selected steps", "Select one or more steps in the "
                                    "list, then choose Run selected steps.", parent=self.root)
                return
        if not self.steps:
            messagebox.showinfo("No steps",
                                "Add at least one step to the sequence.")
            return
        self.capture_armed = False
        self._run_indices = indices
        self._run_settings = (self.loop_forever.get(), self.repetitions.get())
        self._preparation = PlaybackPreparation(
            [self.steps[i] for i in indices], self.target_window, self.loop_forever.get(),
            self._repetition_count(), self.events)
        self.start_button.configure(text=f"■ Stop ({self.hotkeys.toggle})")
        self.status_text.set("Preparing…")
        self._update_editing_controls()
        self._preparation.start()

    def _finish_preparation(self, preparation: PlaybackPreparation) -> None:
        if self._closed or preparation is not self._preparation:
            return
        windows = preparation.windows
        prepared = preparation.result
        accepted = not windows.cancelled.is_set()
        if accepted and preparation.error is not None:
            accepted = False
            self._last_error = str(preparation.error)
            messagebox.showerror("Preparation failed", str(preparation.error))
        if accepted and prepared is not None and prepared.missing_target:
            accepted = False
            self.status_text.set(prepared.status)
            messagebox.showwarning(
                "Target window unavailable",
                f"“{windows.anchor.app}” is missing or matches multiple windows.\n\n"
                "Open the target app and try again, use Target window → "
                "Choose… to reselect it, or Clear the target to play with "
                "saved absolute coordinates.")
        # Dialogs run a nested Tk loop; Stop or Close may have occurred there.
        if self._closed or preparation is not self._preparation:
            return
        if not accepted or windows.cancelled.is_set() or prepared is None:
            status = (prepared.status if prepared is not None and prepared.missing_target
                      else "Stopped")
            # Keep the run active until hotkeys queued during the dialog have
            # been consumed, including Stop queued just before dismissal.
            self.events.put(("preparation_stopped", preparation, status))
            return
        self._preparation = None
        self._blocked_status = None
        self.player = Player(prepared.steps, preparation.loop_forever,
                             preparation.repetitions, self.events,
                             guard=windows.guard, target_probe=windows.probe_target,
                             prepare_clicks=(lambda identity, cancelled, admit: windows.prepare_deferred(
                                 prepared.steps, identity, cancelled, admit)) if preparation.deferred else None)
        self._update_editing_controls()
        self.player.start()
        self.start_button.configure(text=f"■ Stop ({self.hotkeys.toggle})")
        self.status_text.set(prepared.status)

    def _repetition_count(self) -> int:
        try:
            return max(1, int(self.repetitions.get()))
        except ValueError:
            return 1

    # ----------------------------------------------------------------- steps

    def _step_selection_shortcut(self, event) -> str:
        children = self.tree.get_children()
        if not children:
            return "break"
        item = self.tree.focus() or children[0]
        if event.keysym == "a":
            self.tree.selection_set(children)
        elif event.keysym == "space":
            self.tree.selection_toggle(item)
        else:
            index = children.index(item) + (-1 if event.keysym == "Up" else 1)
            item = children[max(0, min(len(children) - 1, index))]
        self.tree.focus(item)
        self.tree.see(item)
        return "break"

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
        if not source or self._editing_blocked() or self.capture_armed:
            return
        destination.append(self._sequence_snapshot())
        steps, selection = source.pop()
        self.steps = [replace(step) for step in steps]
        self._refresh_table()
        children = self.tree.get_children()
        self.tree.selection_set([children[index] for index in selection])
        if selection:
            self.tree.focus(children[selection[0]])
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
        if self._editing_blocked():
            return
        self.capture_armed = True
        self._update_editing_controls()
        self.status_text.set("Capture armed: move the cursor to the target "
                             f"point and press {self.hotkeys.capture}")

    # -------------------------------------------------------------- recording

    def _arm_recording(self) -> None:
        if (self._editing_blocked() or self.capture_armed or self._preview_active
                or self.root.tk.call("grab", "current", self.root)):
            return
        if sys.platform not in ("win32", "darwin"):
            messagebox.showinfo("Record clicks", "Anchored recording is unavailable on this platform.",
                                parent=self.root)
            return
        if self.target_window is None or not self.target_window.has_bounds:
            messagebox.showinfo("Record clicks", "Choose a target window with saved bounds first.",
                                parent=self.root)
            return
        with self._hotkey_lock:
            modifiers_held = bool(self._held_modifiers)
            if not modifiers_held:
                self._recording = ClickRecording(self.target_window, self.events)
        if modifiers_held:
            messagebox.showinfo("Record clicks", "Release modifier keys before arming recording.",
                                parent=self.root)
            return
        self._run_settings = (self.loop_forever.get(), self.repetitions.get())
        self.status_text.set(f"Recording armed — switch to your target; {self.hotkeys.capture} begins, "
                             f"{self.hotkeys.toggle} stops. Keep the target at its saved bounds.")
        self._update_editing_controls()
        self._recording.start()

    def _recording_review_available(self) -> bool:
        return (self._recording is not None and self._recording.finished.is_set()
                and not self._closed and not self._closing and not self._preview_active
                and not self.root.tk.call("grab", "current", self.root))

    def _review_recording(self) -> None:
        if not self._recording_review_available():
            return
        if not self._recording.steps:
            messagebox.showinfo("Recording empty", "No completed clicks to review. Choose Discard.",
                                parent=self.root)
            return
        self._preview_active = True
        self._update_editing_controls()
        try:
            show_preview(self.root, [replace(step) for step in self._recording.steps])
        finally:
            self.events.put(("preview_closed",))

    def _keep_recording(self) -> None:
        if not self._recording_review_available():
            return
        recorded = self._recording.steps
        if recorded:
            before = self._sequence_snapshot()
            first = len(self.steps)
            self.steps.extend(replace(step) for step in recorded)
            self._record_edit(before)
            self._refresh_table()
            rows = self.tree.get_children()[first:]
            self.tree.selection_set(rows)
            self.tree.focus(rows[0])
            self.tree.see(rows[0])
        self._discard_recording()
        self.status_text.set(f"Kept {len(recorded)} recorded clicks; review/edit before playback")

    def _discard_recording(self) -> None:
        if not self._recording_review_available():
            return
        with self._hotkey_lock:
            self._recording = None
        self._run_settings = None
        self.start_button.configure(text=f"▶ Start ({self.hotkeys.toggle})", state="normal")
        self.status_text.set("Recording discarded; sequence unchanged")
        self._update_editing_controls()

    def _add_step(self, prefill: Step | None = None) -> None:
        if self._editing_blocked():
            return
        dialog = StepDialog(self.root, "Add step", prefill,
                            target_available=self.target_window is not None)
        if dialog.result and not self._editing_blocked():
            before = self._sequence_snapshot()
            self.steps.append(dialog.result)
            self._record_edit(before)
            self._refresh_table()

    def _edit_step(self) -> None:
        if self._editing_blocked():
            return
        index = self._selected_index()
        if index is None:
            return
        dialog = StepDialog(self.root, "Edit step", self.steps[index],
                            target_available=self.target_window is not None)
        if (not self._editing_blocked() and dialog.result
                and dialog.result != self.steps[index]):
            before = self._sequence_snapshot()
            self.steps[index] = dialog.result
            self._record_edit(before)
            self._refresh_table(select=index)

    def _remove_step(self) -> None:
        if self._editing_blocked():
            return
        indices = sorted(self.tree.index(item) for item in self.tree.selection())
        if not indices:
            return
        before = self._sequence_snapshot()
        for index in reversed(indices):
            del self.steps[index]
        self._record_edit(before)
        self._refresh_table()

    def _copy_step(self) -> None:
        if self._editing_blocked():
            return
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
        self.tree.focus(children[insert_at])
        self.tree.see(children[insert_at])

    def _move_step(self, offset: int) -> None:
        if self._editing_blocked():
            return
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
        self.tree.focus(children[min(indices)])
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
                                                "Wait for target" if step.wait_condition else
                                                "Wait" if step.wait_only else "Click",
                                                "—" if step.wait_only else step.x,
                                                "—" if step.wait_only else step.y,
                                                f"Timeout {step.timeout:g}s" if step.wait_condition
                                                else step.delay))
        if select is not None and 0 <= select < len(self.steps):
            item = self.tree.get_children()[select]
            self.tree.selection_set(item)
            self.tree.focus(item)
        elif self.steps:
            self.tree.focus(self.tree.get_children()[0])

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
        if self._editing_blocked():
            if self._run_settings is not None:
                loop_forever, repetitions = self._run_settings
                if self.loop_forever.get() != loop_forever:
                    self.loop_forever.set(loop_forever)
                if self.repetitions.get() != repetitions:
                    self.repetitions.set(repetitions)
            return
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
        recording = getattr(self, "_recording", None)
        modal = object()
        if recording is not None:
            self._recording_modal = modal
        try:
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
        finally:
            if recording is not None:
                self.events.put(("record_modal_closed", modal))

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        return messagebox.askyesno(
            "Unsaved changes",
            "The current sequence has unsaved changes. Discard them?")

    def _open_selected_profile(self) -> None:
        if self._editing_blocked():
            self.profile_selection.set(self.current_profile or "")
            return
        name = self.profile_selection.get()
        if not name or name == self.current_profile:
            return
        if not self._confirm_discard() or self._editing_blocked():
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
        if self._editing_blocked():
            return
        if not self._confirm_discard() or self._editing_blocked():
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
        if self._editing_blocked():
            return
        if self.current_profile is None:
            self._save_profile_as()
        else:
            self._write_profile(self.current_profile)

    def _save_profile_as(self) -> None:
        if self._editing_blocked():
            return
        name = simpledialog.askstring("Save profile", "Profile name:",
                                      parent=self.root,
                                      initialvalue=self.imported_profile_name or "")
        if self._editing_blocked() or not name or not name.strip():
            return
        name = name.strip()
        if name != self.current_profile and self.store.exists(name):
            if not messagebox.askyesno(
                    "Overwrite profile",
                    f"A profile named “{name}” already exists. Overwrite it?"):
                return
        self._write_profile(name)

    def _write_profile(self, name: str) -> None:
        if self._editing_blocked():
            return
        first = next((step for step in self.steps if not step.wait_only), None)
        target_window = self.target_window
        if target_window is None and first is not None:
            target_window = window_info_at(first.x, first.y)
        try:
            profile = Profile(name=name, steps=list(self.steps),
                              loop_forever=self.loop_forever.get(),
                              repetitions=self._repetition_count(),
                              target_window=target_window)
            self.store.save(profile)
        except (OSError, ValueError) as exc:
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
        if self._editing_blocked():
            return
        filename = filedialog.askopenfilename(
            parent=self.root, title="Import profile",
            filetypes=[("JSON profiles", "*.json"), ("All files", "*.*")])
        if self._editing_blocked() or not filename:
            return
        try:
            profile = read_profile(Path(filename))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Import failed", f"Could not import profile: {exc}",
                                 parent=self.root)
            return
        if not self._confirm_discard() or self._editing_blocked():
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
        recording = getattr(self, "_recording", None)
        modal = object()
        if recording is not None:
            self._recording_modal = modal
        try:
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
            if recording is None:
                self.status_text.set(f"Exported profile to {filename}")
        finally:
            if recording is not None:
                self.events.put(("record_modal_closed", modal))

    def _delete_profile(self) -> None:
        if self._editing_blocked():
            return
        name = self.profile_selection.get() or self.current_profile
        if not name:
            return
        if not messagebox.askyesno("Delete profile",
                                   f"Delete profile “{name}”?"):
            return
        if self._editing_blocked():
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
        if self._closed or self._closing:
            return
        self._closing = True
        recording = getattr(self, "_recording", None)
        if recording is not None:
            recording.stop()
        self._update_editing_controls()
        # Confirmation runs a nested Tk loop that can deliver worker completion.
        if self._preparation is not None:
            self._preparation.windows.cancelled.set()
        if recording is not None and not messagebox.askyesno(
                "Discard recording?", "Close and discard the pending recording? "
                "Cancel to review it with Keep recording or Discard.", parent=self.root):
            self.events.put(("close_cancelled",))
            return
        if self.dirty and not self._confirm_discard():
            # Consume modal hotkeys before allowing a new Start, including a
            # toggle queued immediately before the confirmation was dismissed.
            self.events.put(("close_cancelled",))
            return
        self._closed = True
        self._closing = False
        if self._preparation is not None:
            self._preparation.windows.cancelled.set()
            self._preparation = None
        if self.player is not None:
            self.player.request_control("stop")
        if self.keyboard_listener is not None:
            self.keyboard_listener.stop()
        self.root.destroy()


def main() -> None:
    configure_dpi_awareness()
    root = tk.Tk()
    ClickCueApp(root)
    root.mainloop()
