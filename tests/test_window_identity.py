import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from clickcue import platform_support as platform
from clickcue.models import Step, TargetWindow
from clickcue.preparation import WindowPlayback


def test_legacy_resolution_requires_a_unique_window(monkeypatch):
    windows = [TargetWindow("Editor", 0, 0, 200, 200),
               TargetWindow("Editor", 200, 0, 200, 200)]
    monkeypatch.setattr(platform, "list_windows", lambda: windows)
    assert platform.find_window("Editor") is None
    windows.pop()
    assert platform.find_window("Editor") is windows[0]


def test_guard_rejects_another_window_of_the_selected_app(monkeypatch):
    selected = TargetWindow("Editor", 100, 100, 200, 200)
    selected.native_id, selected.pid = 11, 101
    other = TargetWindow("Editor", 0, 0, 200, 200)
    other.native_id, other.pid = 12, 101
    monkeypatch.setattr(platform, "list_windows", lambda: [other, selected])
    monkeypatch.setattr("clickcue.preparation.find_window", platform.find_window)
    monkeypatch.setattr("clickcue.preparation.time.sleep", lambda _: None)
    focus = Mock()
    move = Mock(return_value=False)
    monkeypatch.setattr("clickcue.preparation.focus_window", focus)
    monkeypatch.setattr("clickcue.preparation.move_window", move)
    top = Mock(return_value=selected)
    monkeypatch.setattr("clickcue.preparation.window_info_at", top)
    playback = WindowPlayback(TargetWindow("Editor", 0, 0, 200, 200, native_id=11, pid=101))
    step = Step("Click", 10, 10, 0)
    result = playback.prepare([step])
    assert (result.steps[0].x, result.steps[0].y) == (110, 110)
    assert focus.call_args.args[0].native_id == 11
    assert move.call_args.args[0].native_id == 11
    assert move.call_args.args[0].x == 0
    top.return_value = other
    cancelled = Mock(spec=threading.Event)
    cancelled.is_set.return_value = False
    cancelled.wait.return_value = False
    assert playback.guard(step, cancelled) == "Editor (window 12)"
    top.return_value = selected
    assert playback.guard(step, cancelled) is None


def test_macos_picker_keeps_windows_but_coverage_includes_small_layered_overlay(monkeypatch):
    def entry(number, layer, size):
        return {"kCGWindowNumber": number, "kCGWindowOwnerPID": 101,
                "kCGWindowOwnerName": "Editor", "kCGWindowLayer": layer,
                "kCGWindowBounds": {"X": 0, "Y": 0, "Width": size, "Height": size}}
    entries = [entry(3, 5, 20), entry(2, 0, 200), entry(1, 0, 200)]
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(
        CGWindowListCopyWindowInfo=lambda *_: entries,
        kCGWindowListOptionOnScreenOnly=1, kCGNullWindowID=0))
    assert len(platform.list_windows()) == 2
    top = platform.window_info_at(10, 10, include_own=True)
    assert top is not None and top.width == 20


def test_profile_round_trip_discards_live_identity():
    from clickcue.profiles import Profile

    selected = TargetWindow("Editor", 0, 0, 200, 200, native_id=11, pid=101)
    data = Profile("Example", target_window=selected).to_dict()
    assert data["target_window"] == {"app": "Editor", "x": 0, "y": 0,
                                     "width": 200, "height": 200}
    # Importing an old/external handle must not bind playback to its new owner.
    data["target_window"].update(native_id=11, pid=101)
    loaded = Profile.from_dict(data).target_window
    assert loaded is not None
    assert loaded.native_id is None and loaded.pid is None


def test_macos_actions_map_selected_cg_window_to_unique_ax_window(monkeypatch):
    selected = TargetWindow("Editor", 100, 100, 200, 200, 11, 101, "Document")
    entries = [
        {"kCGWindowNumber": number, "kCGWindowOwnerPID": 101,
         "kCGWindowOwnerName": "Editor", "kCGWindowName": title,
         "kCGWindowLayer": 0,
         "kCGWindowBounds": {"X": x, "Y": y, "Width": 200, "Height": 200}}
        for number, title, x, y in [(12, "Other", 0, 0), (11, "Document", 100, 100)]
    ]
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "_macos_activate_pid", lambda _: True)
    ax_other = {"AXPosition": (0, 0), "AXSize": (200, 200), "AXTitle": "Other"}
    ax_selected = {"AXPosition": (100, 100), "AXSize": (200, 200), "AXTitle": "Document"}
    app = {"AXWindows": [ax_other, ax_selected]}
    raised = []

    def set_attribute(window, key, value):
        window[key] = value
        return 0

    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXUIElementCreateApplication=lambda _: app,
        AXUIElementCopyAttributeValue=lambda element, key, _: (0, element[key]),
        AXValueGetValue=lambda value, *_: (True, value),
        AXValueCreate=lambda _, value: value,
        AXUIElementSetAttributeValue=set_attribute,
        AXUIElementPerformAction=lambda element, _: raised.append(element) or 0,
        kAXValueCGPointType=1, kAXValueCGSizeType=2))
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(
        CGPoint=lambda x, y: (x, y), CGSize=lambda w, h: (w, h),
        CGWindowListCopyWindowInfo=lambda *_: entries,
        kCGWindowListOptionOnScreenOnly=1, kCGNullWindowID=0))
    assert platform.focus_window(selected)
    assert raised == [ax_selected]
    from dataclasses import replace
    assert platform.move_window(replace(selected, x=50, y=60))
    assert ax_selected["AXPosition"] == (50, 60)
    assert ax_other["AXPosition"] == (0, 0)
    # Public AX attributes cannot distinguish coincident, identically titled peers.
    entries.append({**entries[1], "kCGWindowNumber": 13})
    assert not platform.focus_window(selected)
    assert not platform.move_window(selected)
    assert len(raised) == 1


def test_windows_coverage_keeps_untitled_overlay_and_pointer_sized_identity(monkeypatch):
    import ctypes
    from ctypes import wintypes

    overlay, target = 0x100000001, 0x100000002
    context = [123]

    def dpi(value):
        previous, context[0] = context[0], value
        return previous

    def rect(hwnd, pointer):
        assert context[0] == -3  # physical coordinates while reading OS bounds
        bounds = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        bounds.left, bounds.top = -100, -100
        bounds.right, bounds.bottom = ((-80, -80) if hwnd == overlay else (200, 200))
        return 1

    def pid(hwnd, pointer):
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = 101
        return 1

    top = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(lambda _: overlay)
    below = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint)(
        lambda hwnd, _: target if hwnd == overlay else 0)
    # A ctypes loader defaults to c_int; the platform adapter must correct it.
    top.restype = below.restype = ctypes.c_int
    api = SimpleNamespace(
        GetTopWindow=top, GetWindow=below,
        IsWindowVisible=Mock(return_value=1),
        GetWindowTextLengthW=Mock(side_effect=lambda hwnd: 0 if hwnd == overlay else 6),
        GetWindowTextW=Mock(side_effect=lambda hwnd, buffer, _: setattr(buffer, "value", "Editor") or 6),
        GetWindowRect=Mock(side_effect=rect),
        GetWindowThreadProcessId=Mock(side_effect=pid),
        SetForegroundWindow=Mock(), MoveWindow=Mock(),
        SetThreadDpiAwarenessContext=Mock(side_effect=dpi),
        WindowFromPoint=Mock(return_value=target), GetAncestor=Mock(return_value=target))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: api, raising=False)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=api), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    top_window = platform.window_info_at(-90, -90, include_own=True)
    assert top_window is not None and top_window.native_id == overlay
    assert top_window.pid == 101
    assert [window.native_id for window in platform.list_windows()] == [target]
    assert context[0] == 123
