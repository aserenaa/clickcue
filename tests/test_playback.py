import queue
import threading
from unittest.mock import Mock, PropertyMock

import pytest

from clickcue.models import Step
from clickcue.playback import Player


@pytest.fixture(autouse=True)
def controller(monkeypatch):
    controller = Mock()
    monkeypatch.setattr("clickcue.playback.mouse.Controller", lambda: controller)
    return controller


def test_countdown_updates_before_click(controller):
    events = queue.Queue()
    player = Player([Step("Wait", 10, 20, 0.25)], loop_forever=False,
                    repetitions=1, events=events)
    player.start()
    try:
        first = events.get(timeout=2)
        second = events.get(timeout=2)
        assert first[:4] == ("waiting", 1, 0, "Wait")
        assert second[:4] == first[:4]
        assert 0 < second[4] < first[4] <= 0.25
        controller.press.assert_not_called()
        player.join(timeout=2)
        assert not player.is_alive()
        controller.press.assert_called_once()
        received = list(events.queue)
        assert ("playing", 1, 0, "Wait") in received
        assert received[-1] == ("stopped",)
    finally:
        player.stop()
        player.join(timeout=2)


@pytest.mark.parametrize("steps", [[], [Step("No pause", 0, 0, 0, wait_only=True)]])
def test_infinite_zero_work_sequence_stops_with_error(controller, monkeypatch, steps):
    monkeypatch.setattr("clickcue.playback.FOCUS_GRACE_SECONDS", 0)
    events = queue.Queue()
    player = Player(steps, loop_forever=True, repetitions=1, events=events)
    player.start()
    try:
        player.join(timeout=2)
        assert not player.is_alive()
        received = list(events.queue)
        assert [event[0] for event in received] == ["error", "stopped"]
        assert "delay" in received[0][1]
        controller.press.assert_not_called()
    finally:
        player.stop()
        player.join(timeout=2)


def test_finite_zero_delay_wait_sequence_completes(controller):
    events = queue.Queue()
    player = Player([Step("No pause", 0, 0, 0, wait_only=True)],
                    loop_forever=False, repetitions=2, events=events)
    player.run()
    assert list(events.queue) == [("playing", 1, 0, "No pause"),
                                  ("playing", 2, 0, "No pause"), ("stopped",)]
    controller.press.assert_not_called()


@pytest.mark.parametrize("wait_only", [False, True])
def test_stop_interrupts_countdown_without_click(controller, wait_only):
    events = queue.Queue()
    player = Player([Step("Long wait", 10, 20, 60, wait_only=wait_only)],
                    loop_forever=False,
                    repetitions=1, events=events)
    player.start()
    try:
        assert events.get(timeout=2)[:4] == ("waiting", 1, 0, "Long wait")
        player.stop()
        player.join(timeout=1)
        assert not player.is_alive()
        controller.press.assert_not_called()
        assert list(events.queue)[-1] == ("stopped",)
    finally:
        player.stop()
        player.join(timeout=2)


def test_guard_blocks_click_and_stops(controller):
    events = queue.Queue()
    player = Player([Step("Btn", 10, 20, 0.01)], loop_forever=True,
                    repetitions=1, events=events,
                    guard=lambda _step, _cancelled: "WhatsApp")
    player.start()
    try:
        player.join(timeout=5)
    finally:
        player.stop()
        player.join(timeout=2)

    received = []
    while not events.empty():
        received.append(events.get())

    assert ("blocked", "Btn", "WhatsApp", 10, 20) in received
    assert ("stopped",) in received
    assert not player.is_alive()
    controller.press.assert_not_called()


def test_stop_inside_successful_guard_prevents_click(controller, monkeypatch):
    monkeypatch.setattr("clickcue.playback.FOCUS_GRACE_SECONDS", 0)
    events = queue.Queue()

    def guard(_step, _cancelled):
        player.stop()
        return None

    player = Player([Step("Button", 10, 20, 0)], loop_forever=False,
                    repetitions=1, events=events, guard=guard)
    player.run()

    controller.press.assert_not_called()
    assert list(events.queue) == [("playing", 1, 0, "Button"), ("stopped",)]


@pytest.mark.parametrize("while_pressed", [False, True])
def test_stop_interrupts_click_waits(controller, monkeypatch, while_pressed):
    monkeypatch.setattr("clickcue.playback.FOCUS_GRACE_SECONDS", 0)
    monkeypatch.setattr("clickcue.playback.PRESS_HOLD_SECONDS", 2)
    ready = threading.Event()
    if while_pressed:
        controller.press.side_effect = lambda *_: ready.set()
    else:
        type(controller).position = PropertyMock(side_effect=lambda *_: ready.set())
    events = queue.Queue()
    player = Player([Step("Button", 10, 20, 0)], loop_forever=False,
                    repetitions=1, events=events)
    player.start()
    try:
        assert ready.wait(timeout=4)
        player.stop()
        player.join(timeout=1)
        assert not player.is_alive()
        if while_pressed:
            controller.press.assert_called_once()
            controller.release.assert_called_once()
        else:
            controller.press.assert_not_called()
        assert list(events.queue)[-1] == ("stopped",)
    finally:
        player.stop()
        player.join(timeout=5)


def test_press_error_still_releases_mouse(controller, monkeypatch):
    monkeypatch.setattr("clickcue.playback.FOCUS_GRACE_SECONDS", 0)
    events = queue.Queue()
    player = Player([Step("Button", 10, 20, 0)], loop_forever=True,
                    repetitions=1, events=events)

    controller.press.side_effect = RuntimeError("press failed")
    player.run()

    controller.press.assert_called_once()
    controller.release.assert_called_once_with(controller.press.call_args.args[0])
    received = list(events.queue)
    assert received[-1] == ("stopped",)
    assert [event for event in received if event[0] == "error"] == [
        ("error", "press failed")
    ]


def test_wait_only_step_does_not_guard_or_click_and_continues(controller):
    from clickcue.profiles import Profile

    profile = Profile.from_dict({
        "name": "Wait then click",
        "steps": [
            {"name": "Pause", "x": 0, "y": 0, "delay": 0.01,
             "wait_only": True},
            {"name": "Button", "x": 100, "y": 200, "delay": 0},
        ],
    })
    events = queue.Queue()
    guarded = []

    def guard(step, _cancelled):
        guarded.append(step.name)
        return "outside target" if (step.x, step.y) == (0, 0) else None

    player = Player(profile.steps, loop_forever=False, repetitions=1,
                    events=events, guard=guard)
    player.start()
    try:
        player.join(timeout=2)
        assert not player.is_alive()
        assert guarded == ["Button"]
        controller.press.assert_called_once()
        assert controller.position == (100, 200)
        received = list(events.queue)
        assert received[0][:4] == ("waiting", 1, 0, "Pause")
        assert ("playing", 1, 1, "Button") in received
        assert not any(event[0] == "blocked" for event in received)
        assert received[-1] == ("stopped",)
    finally:
        player.stop()
        player.join(timeout=2)
