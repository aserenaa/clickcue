"""On-demand, read-only troubleshooting snapshot."""

import tkinter as tk
from tkinter import ttk


def show_diagnostics(parent: tk.Tk, details: str) -> None:
    window = tk.Toplevel(parent)
    window.title("ClickCue diagnostics")
    window.transient(parent)
    body = ttk.Frame(window, padding=12)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Snapshot at opening. Close and reopen to refresh.\n"
              "May contain private window names, paths and error details; "
              "review before sharing.", wraplength=640).pack(anchor="w")
    text = tk.Text(body, width=80, height=24, wrap="word", takefocus=True)
    scrollbar = ttk.Scrollbar(body, command=text.yview)
    text.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y", pady=(8, 0))
    text.pack(fill="both", expand=True, pady=(8, 0))
    text.insert("1.0", details)
    text.configure(state="disabled")

    def select_all(_event):
        text.tag_add("sel", "1.0", "end-1c")
        return "break"

    text.bind("<Control-a>", select_all)
    text.bind("<Command-a>", select_all)
    ttk.Button(window, text="Close", command=window.destroy).pack(pady=(0, 12))
    window.bind("<Escape>", lambda _event: window.destroy())
