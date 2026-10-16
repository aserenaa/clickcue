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
# covering the click point when it is not. Pause/Stop set the event to interrupt
# retry waits. Player discards cancelled results and rechecks after Resume.
ClickGuard = Callable[[Step, threading.Event], str | None]


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
        self._guard_cancelled = threading.Event()
        self._condition = threading.Condition()
        self._paused_at: float | None = None
        self._paused_seconds = 0.0
        self._pause_generation = 0
        self._mouse = mouse.Controller()

    def run(self) -> None:
        repetition = 0
        try:
            if self.loop_forever and all(
                    step.wait_only and step.delay <= 0 for step in self.steps):
                raise ValueError("Loop forever requires a click or a positive delay")
            self._wait(FOCUS_GRACE_SECONDS)
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
        with self._condition:
            self._stop_event.set()
            self._guard_cancelled.set()
            self._condition.notify_all()

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused_at is not None

    def pause(self) -> None:
        with self._condition:
            if self._paused_at is None and not self._stop_event.is_set():
                self._paused_at = time.monotonic()
                self._pause_generation += 1
                self._guard_cancelled.set()
                self._condition.notify_all()

    def resume(self) -> None:
        with self._condition:
            if self._paused_at is not None:
                self._paused_seconds += time.monotonic() - self._paused_at
                self._paused_at = None
                self._condition.notify_all()

    def _active_time(self) -> float:
        # Called under the condition: paused time never consumes a delay.
        now = self._paused_at if self._paused_at is not None else time.monotonic()
        return now - self._paused_seconds

    def _wait(self, seconds: float,
              progress: Callable[[float], None] | None = None) -> bool:
        with self._condition:
            deadline = self._active_time() + seconds
            while not self._stop_event.is_set():
                if self._paused_at is not None:
                    self._condition.wait()
                    continue
                remaining = deadline - self._active_time()
                if remaining <= 0:
                    return True
                if progress is not None:
                    progress(remaining)
                self._condition.wait(min(0.1, remaining) if progress else remaining)
            return False

    def _play_once(self, repetition: int) -> None:
        for index, step in enumerate(self.steps):
            def progress(remaining: float, index: int = index, step: Step = step) -> None:
                self.events.put(("waiting", repetition, index, step.name, remaining))

            if not self._wait(step.delay, progress):
                return
            self.events.put(("playing", repetition, index, step.name))
            if step.wait_only:
                continue
            self._click(step)

    def _guard(self, step: Step) -> int | None:
        while self._wait(0):
            with self._condition:
                if self._paused_at is not None or self._stop_event.is_set():
                    continue
                generation = self._pause_generation
                self._guard_cancelled = threading.Event()
            if self.guard is not None:
                covering = self.guard(step, self._guard_cancelled)
                if not self._wait(0):
                    return None
                # A native lookup may finish after Pause. Recheck on Resume.
                if generation != self._pause_generation:
                    continue
                if covering is not None:
                    # Never click outside the target window; stop instead.
                    self.events.put(("blocked", step.name, covering,
                                     step.x, step.y))
                    self.stop()
                    return None
            return generation
        return None

    def _click(self, step: Step) -> None:
        while True:
            generation = self._guard(step)
            if generation is None or not self._wait(0):
                return
            # Keep the generation approved by the guard across the handoff.
            if generation != self._pause_generation:
                continue
            self._mouse.position = (step.x, step.y)
            if not self._wait(PRESS_HOLD_SECONDS):
                return
            if generation == self._pause_generation:
                break
            # Using Resume may move the pointer: recheck and position it again.
        try:
            self._mouse.press(mouse.Button.left)
            self._stop_event.wait(PRESS_HOLD_SECONDS)
        finally:
            # A backend may send the press before raising an error.
            self._mouse.release(mouse.Button.left)
