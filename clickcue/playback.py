"""Threaded playback of click sequences.

The player emits ("waiting", repetition, step_index, step_name, remaining),
("playing", repetition, step_index, step_name),
("blocked", step_name, covering_app, x, y), ("error", message) and ("stopped",)
tuples on the given queue; the GUI consumes them from the Tk main loop.
"""

import queue
import threading
import time
from typing import Callable

from pynput import mouse

from .models import Step

FOCUS_GRACE_SECONDS = 0.3
PRESS_HOLD_SECONDS = 0.02

# Returns None when it is safe to click the step, or the name of the window
# covering the click point when it is not.
ClickGuard = Callable[[Step], str | None]


class Player(threading.Thread):
    def __init__(self, steps: list[Step], loop_forever: bool, repetitions: int,
                 events: queue.Queue, guard: ClickGuard | None = None):
        super().__init__(daemon=True)
        self.steps = steps
        self.loop_forever = loop_forever
        self.repetitions = repetitions
        self.events = events
        self.guard = guard
        self._stop_event = threading.Event()
        self._mouse = mouse.Controller()

    def run(self) -> None:
        repetition = 0
        try:
            self._stop_event.wait(FOCUS_GRACE_SECONDS)
            while not self._stop_event.is_set():
                repetition += 1
                if not self.loop_forever and repetition > self.repetitions:
                    break
                self._play_once(repetition)
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("stopped",))

    def stop(self) -> None:
        self._stop_event.set()

    def _play_once(self, repetition: int) -> None:
        for index, step in enumerate(self.steps):
            deadline = time.monotonic() + step.delay
            while not self._stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.events.put(("waiting", repetition, index,
                                 step.name, remaining))
                # Event.wait keeps the countdown interruptible by stop().
                if self._stop_event.wait(min(0.1, remaining)):
                    return
            if self._stop_event.is_set():
                return
            self.events.put(("playing", repetition, index, step.name))
            if step.wait_only:
                continue
            if self.guard is not None:
                covering = self.guard(step)
                if covering is not None:
                    # Never click outside the target window; stop instead.
                    self.events.put(("blocked", step.name, covering,
                                     step.x, step.y))
                    self.stop()
                    return
            self._click(step)

    def _click(self, step: Step) -> None:
        self._mouse.position = (step.x, step.y)
        time.sleep(PRESS_HOLD_SECONDS)
        self._mouse.press(mouse.Button.left)
        time.sleep(PRESS_HOLD_SECONDS)
        self._mouse.release(mouse.Button.left)
