"""OS-specific helpers: input permissions, window lookup, focus, and moving.

On macOS, synthesized clicks and cursor moves are silently dropped unless the
host process has the Accessibility permission, so the check here is essential.
On other platforms these helpers degrade to safe no-ops.

Windows are identified by application name on macOS and by window title on
Windows; both are stored in ``TargetWindow.app``.
"""

import os
import sys

from .models import TargetWindow

MIN_WINDOW_SIZE = 60  # ignore tool palettes, tooltips, and similar


def accessibility_trusted(prompt: bool = False) -> bool:
    """Return True if this process may synthesize input events.

    With ``prompt=True`` on macOS, the system permission dialog is shown and
    the host application is added to the Accessibility list.
    """
    if sys.platform != "darwin":
        return True
    try:
        from ApplicationServices import (AXIsProcessTrustedWithOptions,
                                         kAXTrustedCheckOptionPrompt)
        return bool(AXIsProcessTrustedWithOptions(
            {kAXTrustedCheckOptionPrompt: prompt}))
    except Exception:
        return True


def list_windows() -> list[TargetWindow]:
    """Return the visible top-level windows, frontmost first."""
    if sys.platform == "darwin":
        seen = set()
        windows = []
        for pid, name, bounds in _macos_windows():
            if not name or name in seen:
                continue
            seen.add(name)
            windows.append(TargetWindow(name, *bounds))
        return windows
    if sys.platform == "win32":
        return _windows_list_windows()
    return []


def find_window(app: str) -> TargetWindow | None:
    """Return the current bounds of the named app's frontmost window."""
    for window in list_windows():
        if window.app == app:
            return window
    return None


def window_info_at(x: int, y: int,
                   include_own: bool = False) -> TargetWindow | None:
    """Return the window under (x, y) with its current bounds.

    With ``include_own=True`` this process's own windows count too — used by
    the click guard, where our own window covering the point must block the
    click rather than be looked through.
    """
    if sys.platform == "darwin":
        for _pid, name, bounds in _macos_windows(include_own=include_own):
            left, top, width, height = bounds
            if left <= x <= left + width and top <= y <= top + height:
                return TargetWindow(name, left, top, width, height) if name else None
        return None
    if sys.platform == "win32":
        return _windows_window_at(x, y)
    return None


def focus_window(window: TargetWindow) -> bool:
    """Bring the target window's application to the foreground."""
    if sys.platform == "darwin":
        return _macos_focus_app(window.app)
    if sys.platform == "win32":
        hwnd = _windows_find_hwnd(window.app)
        if hwnd:
            _windows_set_foreground(hwnd)
            return True
        return False
    return False


def focus_window_at(x: int, y: int) -> str | None:
    """Bring the window under (x, y) to the foreground; return its app name.

    The first click on a background window often only focuses it, so focusing
    ahead of playback keeps that click from being swallowed.
    """
    window = window_info_at(x, y)
    if window and focus_window(window):
        return window.app
    return window.app if window else None


def move_window(window: TargetWindow) -> bool:
    """Move and resize the app's frontmost window to the stored bounds."""
    if not window.has_bounds:
        return False
    if sys.platform == "darwin":
        return _macos_move_window(window)
    if sys.platform == "win32":
        return _windows_move_window(window)
    return False


# --------------------------------------------------------------------- macOS

def _macos_windows(include_own: bool = False):
    """Yield (pid, app_name, (x, y, width, height)) front to back."""
    try:
        import Quartz
        entries = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    except Exception:
        return
    own_pid = os.getpid()
    for entry in entries:
        if entry.get("kCGWindowLayer") != 0:
            continue
        pid = entry.get("kCGWindowOwnerPID")
        if pid == own_pid and not include_own:
            continue
        bounds = entry.get("kCGWindowBounds", {})
        width = int(bounds.get("Width", 0))
        height = int(bounds.get("Height", 0))
        if width < MIN_WINDOW_SIZE or height < MIN_WINDOW_SIZE:
            continue
        yield (pid, entry.get("kCGWindowOwnerName"),
               (int(bounds.get("X", 0)), int(bounds.get("Y", 0)),
                width, height))


def _macos_pid_for_app(app: str) -> int | None:
    for pid, name, _bounds in _macos_windows():
        if name == app:
            return pid
    return None


def _macos_focus_app(app: str) -> bool:
    pid = _macos_pid_for_app(app)
    if pid is None:
        return False
    # AX forcing works with Accessibility trust even from a background
    # process; NSRunningApplication activation alone is subject to macOS
    # cooperative-activation rules and can be silently ignored.
    raised = _macos_ax_raise(pid)
    activated = _macos_activate_pid(pid)
    return raised or activated


def _macos_ax_raise(pid: int) -> bool:
    try:
        from ApplicationServices import (AXUIElementCopyAttributeValue,
                                         AXUIElementCreateApplication,
                                         AXUIElementPerformAction,
                                         AXUIElementSetAttributeValue)
        app_element = AXUIElementCreateApplication(pid)
        front_error = AXUIElementSetAttributeValue(app_element,
                                                   "AXFrontmost", True)
        window_error, ax_windows = AXUIElementCopyAttributeValue(
            app_element, "AXWindows", None)
        if not window_error and ax_windows:
            AXUIElementPerformAction(ax_windows[0], "AXRaise")
        return front_error == 0
    except Exception:
        return False


def _macos_activate_pid(pid: int) -> bool:
    try:
        from AppKit import (NSApplicationActivateIgnoringOtherApps,
                            NSRunningApplication)
        running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if running is None:
            return False
        running.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        return True
    except Exception:
        return False


def _macos_move_window(window: TargetWindow) -> bool:
    try:
        import Quartz
        from ApplicationServices import (AXUIElementCopyAttributeValue,
                                         AXUIElementCreateApplication,
                                         AXUIElementSetAttributeValue,
                                         AXValueCreate, kAXValueCGPointType,
                                         kAXValueCGSizeType)
        pid = _macos_pid_for_app(window.app)
        if pid is None:
            return False
        app_element = AXUIElementCreateApplication(pid)
        error, ax_windows = AXUIElementCopyAttributeValue(
            app_element, "AXWindows", None)
        if error or not ax_windows:
            return False
        front = ax_windows[0]
        position = AXValueCreate(kAXValueCGPointType,
                                 Quartz.CGPoint(window.x, window.y))
        size = AXValueCreate(kAXValueCGSizeType,
                             Quartz.CGSize(window.width, window.height))
        AXUIElementSetAttributeValue(front, "AXPosition", position)
        AXUIElementSetAttributeValue(front, "AXSize", size)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------- Windows

def _windows_user32():
    import ctypes
    return ctypes, ctypes.windll.user32


def _windows_title(user32, ctypes, hwnd) -> str | None:
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return None
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value or None


def _windows_rect(user32, ctypes, hwnd) -> tuple[int, int, int, int]:
    from ctypes import wintypes
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left,
            rect.bottom - rect.top)


def _windows_list_windows() -> list[TargetWindow]:
    try:
        ctypes, user32 = _windows_user32()
        from ctypes import wintypes
        results: list[TargetWindow] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def collect(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                title = _windows_title(user32, ctypes, hwnd)
                if title:
                    x, y, width, height = _windows_rect(user32, ctypes, hwnd)
                    if width >= MIN_WINDOW_SIZE and height >= MIN_WINDOW_SIZE:
                        results.append(TargetWindow(title, x, y, width, height))
            return True

        user32.EnumWindows(collect, 0)
        return results
    except Exception:
        return []


def _windows_find_hwnd(title: str):
    try:
        ctypes, user32 = _windows_user32()
        return user32.FindWindowW(None, title) or None
    except Exception:
        return None


def _windows_window_at(x: int, y: int) -> TargetWindow | None:
    try:
        ctypes, user32 = _windows_user32()
        from ctypes import wintypes
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
        if not hwnd:
            return None
        GA_ROOT = 2
        root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        title = _windows_title(user32, ctypes, root)
        if not title:
            return None
        left, top, width, height = _windows_rect(user32, ctypes, root)
        return TargetWindow(title, left, top, width, height)
    except Exception:
        return None


def _windows_set_foreground(hwnd) -> None:
    try:
        ctypes, user32 = _windows_user32()
        # Best effort: SetForegroundWindow can be rejected by the OS
        # foreground lock; playback still works if the window has focus.
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _windows_move_window(window: TargetWindow) -> bool:
    try:
        ctypes, user32 = _windows_user32()
        hwnd = _windows_find_hwnd(window.app)
        if not hwnd:
            return False
        return bool(user32.MoveWindow(hwnd, window.x, window.y,
                                      window.width, window.height, True))
    except Exception:
        return False
