"""Bounded, observation-only recording of plain left clicks for one window."""

import os
import queue
import sys
from dataclasses import replace
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any

from pynput import mouse

from .models import Step, TargetWindow
from .platform_support import find_window, window_info_at

JITTER_RADIUS = 4  # screen points on macOS, physical pixels on Windows
MAX_PENDING_EVENTS = 256
MAX_RECORDED_CLICKS = 10000


def create_recording_listener(ready: Event, **callbacks):
    # pynput.wait() can wait forever if setup fails before _mark_ready. Adapt
    # the backend readiness hook so our worker can also observe failure/Stop.
    listener_type: Any = mouse.Listener

    class RecordingListener(listener_type):
        def _mark_ready(self):
            super()._mark_ready()
            ready.set()

    return RecordingListener(**callbacks)


class ClickRecording(Thread):
    """One session identity, worker, listener, and completed prefix.

    Callbacks take timestamps and enqueue bounded observations only. Native
    lookups, listener readiness and cleanup never run on Tk or hook threads.
    Results become readable by the UI only after ``finished`` is set.
    """

    def __init__(self, anchor: TargetWindow, events: queue.Queue):
        super().__init__(daemon=True)
        self.anchor = replace(anchor)
        self.events = events
        self.steps: list[Step] = []
        self.reason: str | None = None
        self.finished = Event()
        self._begin = Event()
        self._stopped = Event()
        self._lock = Lock()
        self._accepting = False
        self._press: tuple[int, int] | None = None
        self._pending: queue.Queue = queue.Queue(MAX_PENDING_EVENTS)

    def begin(self) -> None:
        self._begin.set()

    def stop(self, reason: str | None = None) -> None:
        with self._lock:
            if not self._stopped.is_set():
                self._stop_locked(reason)

    def _stop_locked(self, reason: str | None) -> None:
        self._accepting = False
        if self.reason is None and reason is not None:
            self.reason = reason
        self._stopped.set()

    def _enqueue(self, event: tuple) -> None:
        try:
            self._pending.put_nowait(event)
        except queue.Full:
            self._stop_locked("Input arrived too quickly; recording buffer full")

    def _on_move(self, x, y) -> None:
        with self._lock:
            if self._accepting and self._press is not None:
                px, py = self._press
                if (x - px) ** 2 + (y - py) ** 2 > JITTER_RADIUS ** 2:
                    self._stop_locked("Drag detected (movement exceeded 4 screen units)")

    def _on_click(self, x, y, button, pressed) -> None:
        timestamp = monotonic()
        with self._lock:
            if not self._accepting:
                return
            if button != mouse.Button.left:
                self._stop_locked("Only plain left clicks are supported; other button detected")
                return
            point = (int(x), int(y))
            if pressed:
                if self._press is not None:
                    self._stop_locked("Overlapping mouse presses detected")
                    return
                self._press = point
            else:
                if self._press is None:
                    self._stop_locked("Mouse release without a recorded press")
                    return
                px, py = self._press
                if (x - px) ** 2 + (y - py) ** 2 > JITTER_RADIUS ** 2:
                    self._stop_locked("Drag detected (movement exceeded 4 screen units)")
                    return
                self._press = None
            self._enqueue((pressed, *point, timestamp))

    def _on_scroll(self, *_args) -> None:
        with self._lock:
            if self._accepting:
                self._stop_locked("Scroll detected; only plain left clicks are supported")

    def _observe(self, target: TargetWindow, x: int, y: int) -> None:
        current = find_window(target)
        if (current is None or not target.same_window(current)
                or not self.anchor.same_bounds(current)):
            raise ValueError("Target disappeared, changed, moved or resized")
        covering = window_info_at(x, y, include_own=True)
        if covering is not None and covering.pid == os.getpid():
            raise ValueError("ClickCue window detected; its click was excluded")
        if (covering is None or not target.same_window(covering)
                or not self.anchor.same_bounds(covering)):
            raise ValueError("Target covered, wrong target or unknown window at click point")

    def run(self) -> None:
        listener = None
        try:
            if sys.platform not in ("win32", "darwin"):
                raise ValueError("Anchored recording is unavailable on this platform")
            target = find_window(self.anchor)
            if (target is None or target.native_id is None or target.pid is None
                    or target.pid == os.getpid()):
                raise ValueError("Choose one unique live target outside ClickCue")
            if not target.same_bounds(self.anchor):
                raise ValueError("Target bounds differ from the saved anchor; reselect before recording")
            while not self._begin.wait(0.05):
                if self._stopped.is_set():
                    return
            if self._stopped.is_set():
                return
            ready = Event()
            listener = create_recording_listener(
                ready, on_click=self._on_click, on_move=self._on_move,
                on_scroll=self._on_scroll)
            listener.start()
            deadline = monotonic() + 5
            while not ready.wait(0.02):
                if self._stopped.is_set():
                    return
                if not listener.is_alive() or monotonic() >= deadline:
                    raise RuntimeError("Mouse listener did not become ready")
            with self._lock:
                self._accepting = not self._stopped.is_set()
            if not self._stopped.is_set():
                self.events.put(("record_started", self))
            press = None
            previous_release = None
            while True:
                try:
                    pressed, x, y, timestamp = self._pending.get(timeout=0.05)
                except queue.Empty:
                    if self._stopped.is_set():
                        break
                    if not listener.is_alive():
                        raise RuntimeError("Mouse listener stopped unexpectedly") from None
                    continue
                self._observe(target, x, y)
                if pressed:
                    press = (x, y, timestamp)
                elif press is not None:
                    px, py, started = press
                    delay = 0 if previous_release is None else max(0, started - previous_release)
                    self.steps.append(Step(f"Recorded click {len(self.steps) + 1}", px, py, delay))
                    previous_release = timestamp
                    press = None
                    if len(self.steps) >= MAX_RECORDED_CLICKS:
                        with self._lock:
                            self._stop_locked("Recording reached the 10,000 click limit")
                        break
        except Exception as exc:
            with self._lock:
                self.reason = str(exc)
                self._stop_locked(None)
        finally:
            self.stop()
            if listener is not None:
                try:
                    listener.stop()
                except Exception as exc:
                    self.reason = f"Mouse listener failed: {exc}"
                # OS cleanup may block: retain session ownership until done.
                # A timeout keeps pynput.join from waiting for an absent error
                # receipt when startup failed before its emitter was installed.
                while True:
                    try:
                        listener.join(timeout=0.05)
                    except Exception as exc:
                        self.reason = f"Mouse listener failed: {exc}"
                    if not listener.is_alive():
                        break
            self.finished.set()
            self.events.put(("record_finished", self))
