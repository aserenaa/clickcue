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


def pytest_addoption(parser):
    parser.addoption("--fail-on-skip", action="store_true",
                     help="Fail skipped tests when CI promises a working Tk display")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.skipped and item.config.getoption("--fail-on-skip"):
        report.outcome = "failed"
        report.longrepr = f"Skipped test forbidden by --fail-on-skip: {report.longrepr}"
    return report
