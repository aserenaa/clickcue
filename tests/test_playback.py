import queue
from unittest.mock import Mock

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


def test_stop_interrupts_countdown_without_click(controller):
    events = queue.Queue()
    player = Player([Step("Long wait", 10, 20, 60)], loop_forever=False,
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
                    guard=lambda _step: "WhatsApp")
    player.start()
    try:
        player.join(timeout=5)
    finally:
        player.stop()
        player.join(timeout=2)

    received = []
    while not events.empty():
        received.append(events.get())

    assert ("blocked", "Btn", "WhatsApp") in received
    assert ("stopped",) in received
    assert not player.is_alive()
    controller.press.assert_not_called()
