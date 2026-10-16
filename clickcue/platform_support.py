"""OS-specific helpers: input permissions, window lookup, focus, and moving.

On macOS, synthesized clicks and cursor moves are silently dropped unless the
host process has the Accessibility permission, so the check here is essential.
On other platforms these helpers degrade to safe no-ops.

Profiles store application names on macOS and titles on Windows. Playback
resolves a live native window identity; native IDs are never persisted.
"""

import os
import sys

from .models import TargetWindow

MIN_WINDOW_SIZE = 60  # picker only; coverage includes palettes and tooltips


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
        return list(_macos_windows())
    if sys.platform == "win32":
        return _windows_list_windows()
    return []


def find_window(target: str | TargetWindow) -> TargetWindow | None:
    """Resolve a live identity, or a unique legacy app/title; never guess."""
    windows = list_windows()
    if isinstance(target, TargetWindow) and target.native_id is not None:
        matches = [window for window in windows if target.same_window(window)]
    else:
        app = target.app if isinstance(target, TargetWindow) else target
        matches = [window for window in windows if window.app == app]
    return matches[0] if len(matches) == 1 else None


def window_info_at(x: int, y: int,
                   include_own: bool = False) -> TargetWindow | None:
    """Return the window under (x, y) with its current bounds.

    With ``include_own=True`` this process's own windows count too — used by
    the click guard, where our own window covering the point must block the
    click rather than be looked through.
    """
    if sys.platform == "darwin":
        for window in _macos_windows(include_own=include_own, selectable=False):
            if (window.x <= x < window.x + window.width
                    and window.y <= y < window.y + window.height):
                return window
        return None
    if sys.platform == "win32":
        return _windows_window_at(x, y)
    return None


def focus_window(window: TargetWindow) -> bool:
    """Raise the selected native window when the OS permits it."""
    if sys.platform == "darwin":
        return _macos_focus_window(window)
    if sys.platform == "win32":
        current = find_window(window)
        hwnd = current.native_id if current else None
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
    """Move and resize the selected native window to the stored bounds."""
    if not window.has_bounds:
        return False
    if sys.platform == "darwin":
        return _macos_move_window(window)
    if sys.platform == "win32":
        return _windows_move_window(window)
    return False


# --------------------------------------------------------------------- macOS

def _macos_windows(include_own: bool = False, selectable: bool = True):
    """Yield front-to-back CG windows; picker filters never hide coverage."""
    try:
        import Quartz
        entries = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    except Exception:
        return
    for entry in entries:
        pid = entry.get("kCGWindowOwnerPID")
        if pid == os.getpid() and not include_own:
            continue
        bounds = entry.get("kCGWindowBounds", {})
        width, height = int(bounds.get("Width", 0)), int(bounds.get("Height", 0))
        if width <= 0 or height <= 0:
            continue
        if selectable and (entry.get("kCGWindowLayer") != 0
                           or min(width, height) < MIN_WINDOW_SIZE):
            continue
        yield TargetWindow(entry.get("kCGWindowOwnerName") or "Unknown window",
                           int(bounds.get("X", 0)), int(bounds.get("Y", 0)),
                           width, height, entry.get("kCGWindowNumber"), pid,
                           entry.get("kCGWindowName"))


def _macos_ax_window(window: TargetWindow):
    """Map CG to AX only when public bounds/title attributes are unambiguous."""
    from ApplicationServices import (AXUIElementCopyAttributeValue,
                                     AXUIElementCreateApplication,
                                     AXValueGetValue, kAXValueCGPointType,
                                     kAXValueCGSizeType)
    current = find_window(window)
    if current is None:
        return None
    title = (current.title or "").strip()
    # AX candidates include floating panels excluded by the window picker.
    peers = [peer for peer in _macos_windows(selectable=False)
             if peer.pid == current.pid and peer.same_bounds(current)
             # An unknown title cannot distinguish coincident CG windows.
             and (not title or not peer.title or not peer.title.strip()
                  or peer.title.strip() == title)]
    if len(peers) != 1:
        return None
    error, windows = AXUIElementCopyAttributeValue(
        AXUIElementCreateApplication(current.pid), "AXWindows", None)
    if error or not windows:
        return None
    matches = []
    for candidate in windows:
        pe, position = AXUIElementCopyAttributeValue(candidate, "AXPosition", None)
        se, size = AXUIElementCopyAttributeValue(candidate, "AXSize", None)
        if pe or se:
            continue
        pok, point = AXValueGetValue(position, kAXValueCGPointType, None)
        sok, extent = AXValueGetValue(size, kAXValueCGSizeType, None)
        if not pok or not sok:
            continue
        bounds = (*point, *extent)
        if bounds != (current.x, current.y, current.width, current.height):
            continue
        if title:
            te, ax_title = AXUIElementCopyAttributeValue(candidate, "AXTitle", None)
            if te or not ax_title or ax_title.strip() != title:
                continue
        matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def _macos_focus_window(window: TargetWindow) -> bool:
    try:
        from ApplicationServices import AXUIElementPerformAction
        selected = _macos_ax_window(window)
        if selected is None:
            return False
        activated = _macos_activate_pid(window.pid)
        raised = AXUIElementPerformAction(selected, "AXRaise") == 0
        return activated and raised
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
        from ApplicationServices import (AXUIElementSetAttributeValue,
                                         AXValueCreate, kAXValueCGPointType,
                                         kAXValueCGSizeType)
        selected = _macos_ax_window(window)
        if selected is None:
            return False
        position = AXValueCreate(kAXValueCGPointType,
                                 Quartz.CGPoint(window.x, window.y))
        size = AXValueCreate(kAXValueCGSizeType,
                             Quartz.CGSize(window.width, window.height))
        moved = AXUIElementSetAttributeValue(selected, "AXPosition", position)
        sized = AXUIElementSetAttributeValue(selected, "AXSize", size)
        return moved == 0 and sized == 0
    except Exception:
        return False


# ------------------------------------------------------------------- Windows

def configure_dpi_awareness() -> None:
    """Use physical coordinates for both pynput and Tk before creating windows."""
    if sys.platform != "win32":
        return
    ctypes, user32 = _windows_user32()
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
    user32.GetThreadDpiAwarenessContext.argtypes = []
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
    if not user32.SetProcessDpiAwarenessContext(-3):
        awareness = user32.GetAwarenessFromDpiAwarenessContext(
            user32.GetThreadDpiAwarenessContext())
        if awareness != 2:
            raise RuntimeError("ClickCue requires per-monitor DPI awareness on Windows")


def _windows_user32():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # HWND and DPI_AWARENESS_CONTEXT are pointer-sized, including return values.
    signatures = {
        "GetTopWindow": ([wintypes.HWND], wintypes.HWND),
        "GetWindow": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        "WindowFromPoint": ([wintypes.POINT], wintypes.HWND),
        "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
        "GetWindowTextLengthW": ([wintypes.HWND], ctypes.c_int),
        "GetWindowTextW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
        "MoveWindow": ([wintypes.HWND, ctypes.c_int, ctypes.c_int,
                        ctypes.c_int, ctypes.c_int, wintypes.BOOL], wintypes.BOOL),
        "SetThreadDpiAwarenessContext": ([ctypes.c_void_p], ctypes.c_void_p),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(user32, name)
        function.argtypes, function.restype = arguments, result
    return ctypes, user32


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
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("Window bounds unavailable")
    return (rect.left, rect.top, rect.right - rect.left,
            rect.bottom - rect.top)


def _windows_list_windows(selectable: bool = True) -> list[TargetWindow]:
    try:
        ctypes, user32 = _windows_user32()
        from ctypes import wintypes
        # Match pynput's physical screen coordinates, even on a DPI-unaware
        # Tk/worker thread. Restore the caller's context on every exit.
        previous = user32.SetThreadDpiAwarenessContext(-3)  # PER_MONITOR_AWARE
        if not previous:
            return []
        try:
            results = []
            seen = set()
            hwnd = user32.GetTopWindow(None)
            while hwnd and hwnd not in seen:
                seen.add(hwnd)
                if user32.IsWindowVisible(hwnd):
                    title = _windows_title(user32, ctypes, hwnd)
                    x, y, width, height = _windows_rect(user32, ctypes, hwnd)
                    if (width > 0 and height > 0 and
                            (not selectable or (title and min(width, height) >= MIN_WINDOW_SIZE))):
                        pid = wintypes.DWORD()
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        results.append(TargetWindow(title or "Untitled window", x, y,
                                                    width, height, hwnd, pid.value))
                hwnd = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT: next in Z order
            return results
        finally:
            user32.SetThreadDpiAwarenessContext(previous)
    except Exception:
        return []


def _windows_window_at(x: int, y: int) -> TargetWindow | None:
    # Rectangle coverage blocks even overlays skipped by WindowFromPoint.
    # Native point lookup must also agree: a layered target's transparent hole
    # may send input underneath its rectangle. Neither check alone authorizes.
    for window in _windows_list_windows(selectable=False):
        if (window.x <= x < window.x + window.width
                and window.y <= y < window.y + window.height):
            try:
                ctypes, user32 = _windows_user32()
                from ctypes import wintypes
                previous = user32.SetThreadDpiAwarenessContext(-3)
                if not previous:
                    return None
                try:
                    receiver = user32.WindowFromPoint(wintypes.POINT(x, y))
                    root = user32.GetAncestor(receiver, 2) if receiver else None
                    if root != window.native_id:
                        return None
                    pid = wintypes.DWORD()
                    if not user32.GetWindowThreadProcessId(root, ctypes.byref(pid)):
                        return None
                    return window if pid.value == window.pid else None
                finally:
                    user32.SetThreadDpiAwarenessContext(previous)
            except Exception:
                return None
    return None


def _windows_set_foreground(hwnd) -> None:
    try:
        _ctypes, user32 = _windows_user32()
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _windows_move_window(window: TargetWindow) -> bool:
    try:
        _ctypes, user32 = _windows_user32()
        current = find_window(window)
        if current is None:
            return False
        previous = user32.SetThreadDpiAwarenessContext(-3)
        if not previous:
            return False
        try:
            return bool(user32.MoveWindow(current.native_id, window.x, window.y,
                                          window.width, window.height, True))
        finally:
            user32.SetThreadDpiAwarenessContext(previous)
    except Exception:
        return False
