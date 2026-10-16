"""Window preparation and click coverage policy, independent of the GUI."""

import time
from dataclasses import dataclass, replace
from threading import Event

from .models import Step, TargetWindow
from .platform_support import (
    find_window,
    focus_window,
    focus_window_at,
    move_window,
    window_info_at,
)


@dataclass
class PreparationResult:
    steps: list[Step]
    status: str
    # The GUI must block playback until the target is available or cleared.
    missing_target: bool = False


class WindowPlayback:
    def __init__(self, anchor: TargetWindow | None):
        self.anchor = anchor
        self.target: TargetWindow | None = None

    def prepare(self, steps: list[Step]) -> PreparationResult:
        """Focus the target, restore its bounds if possible, and return the
        steps to play — coordinate-adjusted when the window cannot be moved
        back to where the profile was recorded."""
        anchor = self.anchor
        first = next((step for step in steps if not step.wait_only), None)
        if first is None:
            return PreparationResult(list(steps), "Playing waits…")
        if anchor is None:
            app = focus_window_at(first.x, first.y)
            return PreparationResult(list(steps), (f"Playing on “{app}”…" if app
                                                   else "Playing…"))

        self.target = None
        current = find_window(anchor)
        if current is None:
            return PreparationResult(
                list(steps), "Target unavailable or ambiguous — open, reselect, or clear the target",
                missing_target=True)

        self.target = current
        focus_window(current)
        if not anchor.has_bounds or current.same_bounds(anchor):
            return self._verified(list(steps),
                                  f"Playing on “{anchor.app}”…")

        if move_window(replace(current, x=anchor.x, y=anchor.y,
                               width=anchor.width, height=anchor.height)):
            restored = find_window(current)
            if restored and restored.same_bounds(anchor):
                return self._verified(
                    list(steps),
                    f"Playing on “{anchor.app}” (window restored)…")
            current = restored or current

        # Saved bounds and platform-reported bounds are complete here.
        assert anchor.x is not None and anchor.y is not None
        assert anchor.width is not None and anchor.height is not None
        assert current.x is not None and current.y is not None
        assert current.width is not None and current.height is not None
        scale_x = current.width / anchor.width
        scale_y = current.height / anchor.height
        adjusted = [
            replace(step,
                    x=round(current.x + (step.x - anchor.x) * scale_x),
                    y=round(current.y + (step.y - anchor.y) * scale_y))
            if not step.wait_only else step
            for step in steps
        ]
        return self._verified(
            adjusted,
            f"Playing on “{anchor.app}” (coordinates adjusted)…")

    def _verified(self, steps: list[Step],
                  status: str) -> PreparationResult:
        """Warn when another window still covers the first click point."""
        time.sleep(0.25)  # let the focus change settle before checking
        first = next(step for step in steps if not step.wait_only)
        on_top = window_info_at(first.x, first.y)
        if on_top and (self.target is None or not self.target.same_window(on_top)):
            status += f"  ⚠ “{on_top.app}” is covering the click area"
        return PreparationResult(steps, status)

    def guard(self, step: Step, cancelled: Event) -> str | None:
        """Runs on the player thread before every click: allow it only when
        the target window is on top at the click point. Tries to refocus the
        target first; returns the covering window's name when it gives up.
        Cancellation interrupts retry waits; Player then skips the click."""
        anchor = self.anchor
        if anchor is None:
            return None
        on_top = None
        target = self.target
        for attempt in range(3):
            if cancelled.is_set():
                return None
            on_top = window_info_at(step.x, step.y, include_own=True)
            if on_top and target is not None and target.same_window(on_top):
                return None
            if attempt < 2:
                if target is not None:
                    focus_window(target)
                if cancelled.wait(0.4):
                    return None
        if on_top and on_top.app == anchor.app and on_top.native_id is not None:
            return f"{on_top.app} (window {on_top.native_id})"
        return on_top.app if on_top else "no window detected"

