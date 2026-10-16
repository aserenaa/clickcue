import queue
import threading
from unittest.mock import Mock

import pytest

from clickcue.models import Step, TargetWindow
from clickcue.playback import Player
from clickcue.preparation import WindowPlayback


@pytest.fixture
def windows(monkeypatch):
    api = {}
    for name in ("find_window", "focus_window", "focus_window_at",
                 "move_window", "window_info_at"):
        api[name] = Mock(return_value=None)
        monkeypatch.setattr(f"clickcue.preparation.{name}", api[name])
    return api


@pytest.fixture
def preparation(windows):
    return windows


@pytest.mark.parametrize("anchor", [None, TargetWindow("Target")])
def test_wait_only_preparation_does_not_touch_windows(preparation, anchor):
    steps = [Step("Pause", 17, 29, 2, wait_only=True)]
    result = WindowPlayback(anchor).prepare(steps)
    assert result.steps == steps
    assert result.status == "Playing waits…"
    assert not result.missing_target
    for operation in preparation.values():
        operation.assert_not_called()


@pytest.mark.parametrize("app", ["Target", None])
def test_unanchored_preparation_focuses_first_click(preparation, app):
    preparation["focus_window_at"].return_value = app
    steps = [Step("Pause", 0, 0, 1, wait_only=True), Step("Click", 125, 250, 0)]
    result = WindowPlayback(None).prepare(steps)
    assert result.steps == steps
    assert result.status == ("Playing on “Target”…" if app else "Playing…")
    preparation["focus_window_at"].assert_called_once_with(125, 250)
    preparation["find_window"].assert_not_called()


@pytest.mark.parametrize("mode, expected, status", [
    ("legacy", (125, 250), "Playing on “Target”…"),
    ("unchanged", (125, 250), "Playing on “Target”…"),
    ("restored", (125, 250), "Playing on “Target” (window restored)…"),
    ("move_failed", (-150, 125), "Playing on “Target” (coordinates adjusted)…"),
    ("partial_restore", (60, 120), "Playing on “Target” (coordinates adjusted)…"),
    ("disappeared", (-150, 125), "Playing on “Target” (coordinates adjusted)…"),
])
def test_anchored_preparation_restores_or_scales_without_changing_saved_steps(
        preparation, mode, expected, status):
    anchor = TargetWindow("Target", 100, 200, 100, 200)
    current = TargetWindow("Target", -200, 100, 200, 100)
    partial = TargetWindow("Target", 10, 20, 200, 400)
    if mode == "legacy":
        anchor = TargetWindow("Target")
    if mode == "unchanged":
        current = anchor
    preparation["find_window"].side_effect = [
        current, {"restored": anchor, "partial_restore": partial}.get(mode)]
    preparation["move_window"].return_value = mode != "move_failed"
    preparation["window_info_at"].return_value = TargetWindow("Cover")
    steps = [Step("Pause", 9, 7, 3, wait_only=True), Step("Click", 125, 250, 0.5)]

    result = WindowPlayback(anchor).prepare(steps)

    assert result.steps == [steps[0], Step("Click", *expected, 0.5)]
    assert steps[1] == Step("Click", 125, 250, 0.5)
    assert result.status == status + "  ⚠ “Cover” is covering the click area"
    assert not result.missing_target
    preparation["focus_window"].assert_called_once_with(current)
    preparation["window_info_at"].assert_called_once_with(*expected)
    if mode in ("legacy", "unchanged"):
        preparation["move_window"].assert_not_called()
    else:
        preparation["move_window"].assert_called_once_with(anchor)


def test_missing_target_surfaces_absolute_fallback_for_gui_decision(preparation):
    steps = [Step("Click", 125, 250, 0)]
    result = WindowPlayback(TargetWindow("Target")).prepare(steps)
    assert result.missing_target
    assert result.steps == steps
    assert result.status == "Playing (target window not found)…"
    preparation["focus_window"].assert_not_called()
    preparation["window_info_at"].assert_not_called()


@pytest.mark.parametrize("top", [None, TargetWindow("Target")])
def test_preparation_does_not_warn_without_a_covering_window(preparation, top):
    preparation["find_window"].return_value = TargetWindow("Target")
    preparation["window_info_at"].return_value = top
    result = WindowPlayback(TargetWindow("Target")).prepare([Step("Click", 1, 2, 0)])
    assert result.status == "Playing on “Target”…"


@pytest.mark.parametrize("tops, covering, retries", [
    ([TargetWindow("Target")], None, 0),
    ([TargetWindow("Cover"), TargetWindow("Target")], None, 1),
    ([TargetWindow("Cover")] * 3, "Cover", 2),
    ([None] * 3, "no window detected", 2),
])
def test_guard_recovers_focus_or_reports_cover_including_own_window(
        windows, tops, covering, retries):
    windows["window_info_at"].side_effect = tops
    cancelled = Mock(spec=threading.Event)
    cancelled.is_set.return_value = False
    cancelled.wait.return_value = False
    guard = WindowPlayback(TargetWindow("Target")).guard
    assert guard(Step("Click", 17, 29, 0), cancelled) == covering
    assert windows["focus_window"].call_count == retries
    for call in windows["window_info_at"].call_args_list:
        assert call.args == (17, 29)
        assert call.kwargs == {"include_own": True}


def test_unanchored_guard_does_not_check_coverage(windows):
    assert WindowPlayback(None).guard(Step("Click", 1, 2, 0), threading.Event()) is None
    windows["window_info_at"].assert_not_called()


@pytest.mark.parametrize("retry", [1, 2])
def test_player_stop_interrupts_focus_retry_without_click_or_block(windows, monkeypatch, retry):
    monkeypatch.setattr("clickcue.playback.FOCUS_GRACE_SECONDS", 0)
    controller = Mock()
    monkeypatch.setattr("clickcue.playback.mouse.Controller", lambda: controller)
    ready = threading.Event()
    attempts = 0

    def focus(_anchor):
        nonlocal attempts
        attempts += 1
        if attempts == retry:
            ready.set()

    windows["focus_window"].side_effect = focus
    windows["window_info_at"].return_value = TargetWindow("Cover")
    events = queue.Queue()
    player = Player([Step("Click", 17, 29, 0)], False, 1, events,
                    guard=WindowPlayback(TargetWindow("Target")).guard)
    player.start()
    try:
        assert ready.wait(timeout=2)
        player.stop()
        player.join(timeout=0.25)
        assert not player.is_alive()
        controller.press.assert_not_called()
        assert list(events.queue) == [("playing", 1, 0, "Click"), ("stopped",)]
        assert attempts == retry
    finally:
        player.stop()
        player.join(timeout=2)


def test_stop_interrupts_preparation_settle_wait_before_coverage_check(windows):
    settling = threading.Event()

    class SettleEvent(threading.Event):
        def wait(self, timeout=None):
            settling.set()
            return super().wait(timeout)

    playback = WindowPlayback(TargetWindow("Target"))
    playback.cancelled = SettleEvent()
    windows["find_window"].return_value = TargetWindow("Target")
    events = queue.Queue()

    def prepare():
        try:
            playback.prepare([Step("Click", 17, 29, 0)])
        except InterruptedError:
            events.put("cancelled")

    worker = threading.Thread(target=prepare)
    worker.start()
    try:
        assert settling.wait(2)
        playback.cancelled.set()
        assert events.get(timeout=0.2) == "cancelled"
        windows["window_info_at"].assert_not_called()
    finally:
        playback.cancelled.set()
        worker.join(timeout=2)
