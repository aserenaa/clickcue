"""Keep the test suite isolated from the operating system's input devices."""

import os

# Set before test collection imports pynput, including on headless CI hosts.
os.environ["PYNPUT_BACKEND"] = "dummy"
