"""Keep the test suite isolated from the operating system's input devices."""

import os
import tkinter as tk

import pytest

# Set before test collection imports pynput, including on headless CI hosts.
os.environ["PYNPUT_BACKEND"] = "dummy"


@pytest.fixture(scope="session")
def tk_runtime():
    """Share one hidden interpreter; tests own their Toplevels and timers."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()
