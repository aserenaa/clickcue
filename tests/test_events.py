import queue
from unittest.mock import Mock

from clickcue.events import EventQueue
from clickcue.gui import ClickCueApp


def test_progress_bursts_keep_latest_and_preserve_durable_events():
    events = EventQueue()
    durable = [("hotkey_toggle",), ("hotkey_capture", 10, 20),
               ("blocked", "Button", "Cover", 10, 20),
               ("error", "failed"), ("stopped",)]
    expected = []
    for boundary in durable:
        for index in range(1000):
            events.put(("waiting", 1, index, "Pause", 1.0))
            events.put(("playing", 1, index, "Pause"))
        expected.extend([("playing", 1, 999, "Pause"), boundary])
        events.put(boundary)

    assert events.qsize() == len(expected)
    received = []
    while not events.empty():
        received.append(events.get_nowait())
        events.task_done()
    assert received == expected
    events.join()


def test_poll_yields_with_continuous_events_and_eventually_delivers_all():
    app = object.__new__(ClickCueApp)
    app.root = Mock()
    app.events = queue.Queue()
    received = []

    def handle(event):
        received.append(event)
        if len(received) < 1000:
            app.events.put(("hotkey_toggle",))

    app._handle_event = handle
    app.events.put(("hotkey_toggle",))
    app._poll_events()
    assert 0 < len(received) < 1000
    app.root.after.assert_called_once()

    for _ in range(1000):
        if app.events.empty():
            break
        app._poll_events()
    assert received == [("hotkey_toggle",)] * 1000
