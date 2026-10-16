import queue
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock

import pytest

from clickcue import platform_support as platform
from clickcue.models import Step, TargetWindow
from clickcue.playback import Player
from clickcue.preparation import WindowPlayback


@pytest.fixture(autouse=True)
def controller(monkeypatch):
    controller = Mock()
    monkeypatch.setattr("clickcue.playback.mouse.Controller", lambda: controller)
    return controller


def test_countdown_updates_before_click(controller):
    events = queue.Queue()
    player = Player([Step("Wait", 10, 20, 0.25)], loop_forever=False,
                    repetitions=1, events=events)
    # Observe native input in the progress FIFO. The consumer may wake only
    # after the delay has elapsed; no second timer tick is guaranteed.
    controller.press.side_effect = lambda *_: events.put(("pressed",))
    player.start()
    try:
        player.join(timeout=2)
        assert not player.is_alive()
        received = list(events.queue)
        playing = ("playing", 1, 0, "Wait")
        playing_index = received.index(playing)
        countdown = received[:playing_index]
        assert countdown
        assert all(event[:4] == ("waiting", 1, 0, "Wait") for event in countdown)
        remaining = [event[4] for event in countdown]
        assert all(0 < seconds <= 0.25 for seconds in remaining)
        assert all(later < earlier for earlier, later in zip(remaining, remaining[1:], strict=False))
        assert received[playing_index:] == [playing, ("pressed",), ("stopped",)]
        controller.press.assert_called_once()
    finally:
        player.stop()
        player.join(timeout=2)


@pytest.mark.parametrize("steps", [[], [Step("No pause", 0, 0, 0, wait_only=True)],
                                  [Step("Available", 0, 0, 0, True,
                                        "target_window_ready", 1)]])
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


@pytest.mark.parametrize("outcome", ["ready", "timeout", "late", "stop"])
def test_condition_wait_only_continues_when_available(controller, monkeypatch, outcome):
    monkeypatch.setattr("clickcue.playback.FOCUS_GRACE_SECONDS", 0)
    events = queue.Queue()
    calls = []
    target = TargetWindow("Example", native_id=1, pid=10)

    def probe(identity):
        calls.append(identity)
        if outcome == "stop":
            player.stop()
        if outcome == "late":
            threading.Event().wait(0.3)  # Native success arriving after the deadline is unusable.
            return target
        return target if outcome == "ready" and len(calls) > 1 else None

    player = Player([Step("Available", 0, 0, 0, True, "target_window_ready", 0.25),
                     Step("Click", 10, 20, 0)], False, 1, events, target_probe=probe)
    player.start()
    try:
        player.join(timeout=2)
        assert not player.is_alive()
        assert controller.press.call_count == (outcome == "ready")
        received = list(events.queue)
        assert received[0][:4] == ("condition_waiting", 1, 0, "Available")
        assert received[-1] == ("stopped",)
        errors = [event[1] for event in received if event[0] == "error"]
        if outcome in ("timeout", "late"):
            assert len(errors) == 1 and "Available" in errors[0] and "timed out" in errors[0]
            assert not any(event[0] == "playing" for event in received)
        else:
            assert not errors
    finally:
        player.stop()
        player.join(timeout=2)


@pytest.mark.parametrize("boundary,late_error", [
    (boundary, late_error) for boundary in ("probe", "preparation")
    for late_error in (False, True)
] + [("focus", False), ("restore", False), ("focus_query", False)])
@pytest.mark.parametrize("finish", ["resume", "stop"])
def test_condition_native_results_are_invalidated_by_controls(
        controller, monkeypatch, boundary, finish, late_error):
    monkeypatch.setattr("clickcue.playback.FOCUS_GRACE_SECONDS", 0)
    entered = threading.Event()
    release = threading.Event()
    retried = threading.Event()
    target = TargetWindow("Example", 10, 20, 100, 100, native_id=1, pid=10)
    calls = []
    ordering = []
    original = [Step("Available", 0, 0, 0, True, "target_window_ready", 0.4),
                Step("Click", 10, 20, 0)]

    def native():
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
            if late_error:
                raise RuntimeError("Obsolete native failure")
        else:
            retried.set()

    def probe(identity):
        if boundary == "probe":
            native()
        return target

    def prepare(identity, cancelled, admit=None):
        assert identity.same_window(target)
        native()
        return [original[0], Step("Click", 999 if len(calls) == 1 else 10, 20, 0)]

    if boundary in ("focus", "restore", "focus_query"):
        def suspend():
            entered.set()
            assert release.wait(2)

        class PreparationBoundary(WindowPlayback):
            def _check_cancelled(self):
                super()._check_cancelled()
                # The target is assigned immediately before the final focus
                # cancellation check. Suspend after that check has succeeded.
                if boundary == "focus" and self.target is not None and not entered.is_set():
                    suspend()

        def restoring(*args, **kwargs):
            # Argument construction follows the last restoration check and
            # precedes native initiation; no operation is in flight here.
            if not entered.is_set():
                suspend()
            return replace(*args, **kwargs)

        def action(name):
            ordering.append(name)
            if entered.is_set():
                retried.set()
            return False

        def queried(_):
            if boundary == "focus_query" and not entered.is_set():
                suspend()
            return target

        monkeypatch.setattr("clickcue.preparation.find_window", lambda _: target)
        monkeypatch.setattr("clickcue.preparation.window_info_at", lambda *a, **kw: target)
        monkeypatch.setattr(platform, "sys", SimpleNamespace(platform="win32"))
        monkeypatch.setattr(platform, "find_window", queried)
        monkeypatch.setattr(platform, "_windows_set_foreground", lambda _: action("focus"))
        user32 = SimpleNamespace(SetThreadDpiAwarenessContext=lambda _: -3,
                                 MoveWindow=lambda *args: action("restore"))
        monkeypatch.setattr(platform, "_windows_user32", lambda: (None, user32))
        if boundary == "restore":
            monkeypatch.setattr("clickcue.preparation.replace", restoring)
        windows = PreparationBoundary(TargetWindow("Example", 0, 0, 100, 100))

        def prepare(identity, cancelled, admit=None):
            if admit is None:
                return windows.prepare_deferred(original, identity, cancelled)
            return windows.prepare_deferred(original, identity, cancelled, admit)

    player = Player(original, False, 1, queue.Queue(), target_probe=probe,
                    prepare_clicks=prepare if boundary != "probe" else None)
    player.start()
    try:
        assert entered.wait(2)
        player.request_control("stop" if finish == "stop" else "pause")
        while player.events.get(timeout=2)[0] != "controlled":
            pass
        ordering.append("acknowledged")
        release.set()
        if boundary in ("focus", "restore", "focus_query"):
            assert not retried.wait(0.15), ordering
        if finish == "resume":
            # Paused time exceeds the full condition timeout; no result may
            # authorize later input, and Resume must perform fresh native work.
            assert not retried.wait(0.5)
            controller.press.assert_not_called()
            player.resume()
            assert retried.wait(2)
        player.join(timeout=2)
        assert not player.is_alive()
        assert controller.press.call_count == (finish == "resume")
        if finish == "resume":
            expected = (20, 40) if boundary in ("focus", "restore", "focus_query") else (10, 20)
            assert controller.position == expected
        assert not any(event[0] == "error" for event in player.events.queue)
    finally:
        release.set()
        player.stop()
        player.join(timeout=2)


@pytest.mark.parametrize("finish", ["resume", "stop"])
def test_pause_freezes_remaining_delay_and_stop_wakes_worker(controller, monkeypatch, finish):
    monkeypatch.setattr("clickcue.playback.FOCUS_GRACE_SECONDS", 0)
    events = queue.Queue()
    pressed = threading.Event()
    controller.press.side_effect = lambda *_: pressed.set()
    player = Player([Step("Delayed", 1, 2, 0.5)], False, 1, events)
    player.start()
    try:
        assert events.get(timeout=2)[0] == "waiting"
        player.pause()
        assert player.paused
        assert not pressed.wait(0.65)  # Longer than the entire original delay.
        if finish == "stop":
            player.stop()
        else:
            resumed = time.monotonic()
            player.resume()
            assert not player.paused
            assert pressed.wait(2)
            assert time.monotonic() - resumed >= 0.3
        player.join(timeout=1)
        assert not player.is_alive()
        assert controller.press.call_count == (finish == "resume")
        assert list(events.queue)[-1] == ("stopped",)
    finally:
        player.stop()
        player.join(timeout=2)


@pytest.mark.parametrize("pause_at", ["guard", "handoff", "move", "press",
                                     "move_gap", "press_gap", "stop_press_gap"])
def test_pause_at_input_boundary_preserves_cleanup_and_resume_guard(
        controller, monkeypatch, pause_at):
    monkeypatch.setattr("clickcue.playback.FOCUS_GRACE_SECONDS", 0)
    entered = threading.Event()
    released = threading.Event()
    next_click = threading.Event()
    guarded = []
    pointer = [None]
    clicked_at = []
    moved_while_paused = []
    controlled = threading.Event()
    operation_started = threading.Event()
    control_started = threading.Event()
    ordering = []
    accesses = 0

    class BoundaryPlayer(Player):
        def __getattribute__(self, name):
            nonlocal accesses
            value = super().__getattribute__(name)
            if name == "_mouse":
                accesses += 1
                if ((pause_at == "move_gap" and accesses == 1)
                        or (pause_at in ("press_gap", "stop_press_gap") and accesses == 2)):
                    # The backend operation has not begun: stop between the
                    # admission check and accessing its controller to initiate it.
                    entered.set()
                    assert control_started.wait(2)
                    controlled.wait(0.2)
            return value

    def guard(step, _cancelled):
        guarded.append(step.name)
        if pause_at == "guard" and len(guarded) == 1:
            player.pause()  # Simulate Pause arriving during a native lookup.
            entered.set()
            assert _cancelled.wait(0.1)  # Interrupt subsequent native retry waits.
        return None

    def moved(position):
        ordering.append("move")
        if player.paused:
            moved_while_paused.append(position)
        pointer[0] = position
        if pause_at == "move_gap":
            operation_started.set()
        if pause_at == "move" and not entered.is_set():
            player.pause()
            entered.set()

    def pressed(*_):
        ordering.append("press")
        clicked_at.append(pointer[0])
        if pause_at in ("press_gap", "stop_press_gap"):
            operation_started.set()
        if pause_at == "press" and not entered.is_set():
            player.pause()
            entered.set()
        else:
            next_click.set()

    type(controller).position = PropertyMock(side_effect=moved)
    controller.press.side_effect = pressed
    controller.release.side_effect = lambda *_: released.set()
    events = queue.Queue()
    player = BoundaryPlayer([Step("First", 1, 2, 0), Step("Second", 3, 4, 0)],
                    False, 1, events, guard=guard)
    if pause_at == "handoff":
        approve = player._guard

        def pause_after_approval(step):
            approval = approve(step)
            if not entered.is_set():
                player.pause()
                entered.set()
            return approval

        monkeypatch.setattr(player, "_guard", pause_after_approval)
    player.start()
    control_thread = None
    try:
        assert entered.wait(2)
        if pause_at.endswith("gap"):
            def control():
                control_started.set()
                if pause_at == "stop_press_gap":
                    player.stop()
                else:
                    player.pause()
                ordering.append("controlled")
                controlled.set()

            control_thread = threading.Thread(target=control)
            control_thread.start()
            assert controlled.wait(2)
            assert operation_started.wait(2)
            # An operation already admitted may finish before acknowledgment;
            # none may start after Pause/Stop has acknowledged completion.
            assert ordering[-1] == "controlled"
            if pause_at == "stop_press_gap":
                player.join(timeout=2)
                assert not player.is_alive()
                assert controller.press.call_count == 1
                controller.release.assert_called_once()
                return
            if pause_at == "press_gap":
                assert released.wait(1)
                next_click.clear()
        if pause_at == "press":
            assert released.wait(1)  # Pausing must never leave the button held.
        assert not next_click.wait(0.15)
        if pause_at in ("handoff", "move"):
            pointer[0] = (999, 999)  # Clicking Resume moved away from the target.
        player.resume()
        player.join(timeout=2)
        assert not player.is_alive()
        assert controller.press.call_count == 2
        assert controller.release.call_count == 2
        assert clicked_at == [(1, 2), (3, 4)]
        assert not moved_while_paused
        if pause_at in ("guard", "handoff"):
            assert guarded == ["First", "First", "Second"]
        assert list(events.queue)[-1] == ("stopped",)
    finally:
        player.stop()
        player.join(timeout=2)
        if control_thread is not None:
            control_thread.join(timeout=2)
