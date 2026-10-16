"""Read-only view of recorded click coordinates; no platform or input access."""

import tkinter as tk
from tkinter import ttk

from .models import Step


def show_preview(parent: tk.Tk, steps: list[Step]) -> None:
    """Show a scaled map and exact coordinates for a sequence with clicks."""
    dialog = tk.Toplevel(parent)
    dialog.title("Preview only — no input sent")
    dialog.resizable(False, False)
    body = ttk.Frame(dialog, padding=12)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Preview only — no clicks, pointer movement, or window changes.\n"
              "Recorded absolute coordinates, scaled to fit; not a screen overlay.\n"
              "X increases right; Y increases down. Playback may adjust coordinates."
              ).pack(anchor="w")

    # Use the recorded extent, not Tk's primary screen size: negative monitor
    # coordinates and points outside the current desktop remain visible.
    points: dict[tuple[int, int], list[str]] = {}
    for number, step in enumerate(steps, 1):
        if not step.wait_only:
            points.setdefault((step.x, step.y), []).append(str(number))
    left = min(x for x, y in points)
    top = min(y for x, y in points)
    right = max(x for x, y in points)
    bottom = max(y for x, y in points)
    width, height, margin = 560, 260, 30
    scale = min((width - 2 * margin) / max(1, right - left),
                (height - 2 * margin) / max(1, bottom - top))
    offset_x = (width - (right - left) * scale) / 2
    offset_y = (height - (bottom - top) * scale) / 2
    canvas = tk.Canvas(body, width=width, height=height, background="white")
    canvas.pack(pady=(8, 0))
    for (x, y), numbers in points.items():
        px = offset_x + (x - left) * scale
        py = offset_y + (y - top) * scale
        canvas.create_oval(px - 5, py - 5, px + 5, py + 5,
                           fill="#2457a7", outline="#2457a7")
        canvas.create_text(px, py - 14, text=", ".join(numbers),
                           fill="#163967")
    ttk.Label(body, text=f"Recorded extent: X {left} to {right}; Y {top} to {bottom}.\n"
              "Numbers match sequence steps; shared positions share a marker."
              ).pack(anchor="w", pady=(4, 8))

    table_frame = ttk.Frame(body)
    table_frame.pack(fill="x")
    table = ttk.Treeview(table_frame, columns=("step", "name", "position", "delay"),
                         show="headings", height=6)
    for column, heading, size in [("step", "#", 40), ("name", "Name", 180),
                                  ("position", "Recorded position", 210),
                                  ("delay", "Delay (s)", 100)]:
        table.heading(column, text=heading)
        table.column(column, width=size, anchor="w")
    scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=scrollbar.set)
    table.pack(side="left", fill="x", expand=True)
    scrollbar.pack(side="right", fill="y")
    for number, step in enumerate(steps, 1):
        position = "Wait only — no click" if step.wait_only else f"({step.x}, {step.y})"
        table.insert("", "end", values=(number, step.name, position, step.delay))
    close = ttk.Button(body, text="Close preview (Esc)", command=dialog.destroy)
    close.pack(pady=(10, 0))
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.transient(parent)
    dialog.grab_set()
    close.focus_set()
    dialog.wait_window()
