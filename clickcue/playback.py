"""Threaded playback of click sequences.

The player emits ("waiting", repetition, step_index, step_name, remaining),
("playing", repetition, step_index, step_name),
("blocked", step_name, covering_app, x, y), ("error", message) and ("stopped",)
tuples on the given queue; the GUI consumes them from the Tk main loop.
"""

import queue
import threading
import time
from typing import Callable, Literal

from pynput import mouse

from .models import Step, TargetWindow

FOCUS_GRACE_SECONDS = 0.3
PRESS_HOLD_SECONDS = 0.02

# Returns None when it is safe to click the step, or the name of the window
# covering the click point when it is not. Pause/Stop set the event to interrupt
# retry waits. Player discards cancelled results and rechecks after Resume.
ClickGuard = Callable[[Step, threading.Event], str | None]
TargetProbe = Callable[[TargetWindow | None], TargetWindow | None]
PrepareClicks = Callable[[TargetWindow | None, threading.Event], list[Step]]


class Player(threading.Thread):
    def __init__(self, steps: list[Step], loop_forever: bool, repetitions: int,
                 events: queue.Queue, guard: ClickGuard | None = None,
                 *, target_probe: TargetProbe | None = None,
                 prepare_clicks: PrepareClicks | None = None):
        super().__init__(daemon=True)
        self.steps = steps
        self.loop_forever = loop_forever
        self.repetitions = repetitions
        self.events = events
        self.guard = guard
        self.target_probe = target_probe
        self.prepare_clicks = prepare_clicks
        self._target: TargetWindow | None = None
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
        # A UI snapshot, not an input admission check: never wait on a backend.
        return self._paused_at is not None

    def request_control(self, action: Literal["pause", "resume", "stop"]) -> None:
        """Keep Tk responsive while a synchronized control awaits native input."""
        def apply() -> None:
            {"pause": self.pause, "resume": self.resume, "stop": self.stop}[action]()
            self.events.put(("controlled", self, action))

        threading.Thread(target=apply, daemon=True).start()

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
        for index in range(len(self.steps)):
            step = self.steps[index]
            def progress(remaining: float, index: int = index, step: Step = step) -> None:
                self.events.put(("waiting", repetition, index, step.name, remaining))

            if step.wait_condition is not None:
                if not self._wait_for_target(step, repetition, index):
                    return
            elif not self._wait(step.delay, progress):
                return
            self.events.put(("playing", repetition, index, step.name))
            if step.wait_only:
                continue
            if not self._prepare_clicks():
                return
            self._click(self.steps[index])

    def _wait_for_target(self, step: Step, repetition: int, index: int) -> bool:
        if self.target_probe is None:
            raise ValueError(f"{step.name}: target window checking is unavailable")
        assert step.timeout is not None
        with self._condition:
            deadline = self._active_time() + step.timeout
        while self._wait(0):
            with self._condition:
                if self._stop_event.is_set():
                    return False
                if self._paused_at is not None:
                    continue
                generation = self._pause_generation
                remaining = deadline - self._active_time()
                if remaining <= 0:
                    raise TimeoutError(f"{step.name}: timed out waiting for the target window")
                self.events.put(("condition_waiting", repetition, index, step.name, remaining))
            try:
                target = self.target_probe(self._target)
            except Exception:
                with self._condition:
                    if self._stop_event.is_set():
                        return False
                    if self._paused_at is not None or generation != self._pause_generation:
                        continue
                    raise
            with self._condition:
                if self._stop_event.is_set():
                    return False
                if self._paused_at is not None or generation != self._pause_generation:
                    continue
                remaining = deadline - self._active_time()
                if remaining <= 0:
                    raise TimeoutError(f"{step.name}: timed out waiting for the target window")
                if target is not None:
                    # Pin a name-only profile's first accepted live identity.
                    self._target = target
                    return True
            if not self._wait(min(0.1, remaining)):
                return False
        return False

    def _prepare_clicks(self) -> bool:
        prepare = self.prepare_clicks
        if prepare is None:
            return True
        while self._wait(0):
            with self._condition:
                if self._stop_event.is_set():
                    return False
                if self._paused_at is not None:
                    continue
                generation = self._pause_generation
                self._guard_cancelled = threading.Event()
            try:
                steps = prepare(self._target, self._guard_cancelled)
            except Exception:
                with self._condition:
                    if self._stop_event.is_set():
                        return False
                    if self._paused_at is not None or generation != self._pause_generation:
                        continue
                    raise
            with self._condition:
                if self._stop_event.is_set():
                    return False
                if self._paused_at is not None or generation != self._pause_generation:
                    continue
                self.steps = steps
                self.prepare_clicks = None
                return True
        return False

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
            # Validation and native initiation share the Pause/Stop lock.
            # Synchronous backends retain it until the call returns; neither
            # guard queries nor interruptible waits hold it.
            with self._condition:
                if self._stop_event.is_set():
                    return
                if self._paused_at is not None or generation != self._pause_generation:
                    continue
                self._mouse.position = (step.x, step.y)
            if not self._wait(PRESS_HOLD_SECONDS):
                return
            attempted = False
            try:
                with self._condition:
                    if self._stop_event.is_set():
                        return
                    if self._paused_at is not None or generation != self._pause_generation:
                        # Resume can move the pointer: recheck and reposition.
                        continue
                    attempted = True
                    self._mouse.press(mouse.Button.left)
                self._stop_event.wait(PRESS_HOLD_SECONDS)
                return
            finally:
                if attempted:
                    # Release even on Pause/Stop or a partially sent press error.
                    self._mouse.release(mouse.Button.left)
