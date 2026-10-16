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
    # The GUI decides whether to accept these absolute coordinates. The guard
    # retains the anchor even when that fallback is accepted.
    missing_target: bool = False


class WindowPlayback:
    def __init__(self, anchor: TargetWindow | None):
        self.anchor = anchor

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

        current = find_window(anchor.app)
        if current is None:
            return PreparationResult(
                list(steps), "Playing (target window not found)…",
                missing_target=True)

        focus_window(current)
        if not anchor.has_bounds or current.same_bounds(anchor):
            return self._verified(anchor, list(steps),
                                  f"Playing on “{anchor.app}”…")

        if move_window(anchor):
            restored = find_window(anchor.app)
            if restored and restored.same_bounds(anchor):
                return self._verified(
                    anchor, list(steps),
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
            anchor, adjusted,
            f"Playing on “{anchor.app}” (coordinates adjusted)…")

    def _verified(self, anchor: TargetWindow, steps: list[Step],
                  status: str) -> PreparationResult:
        """Warn when another window still covers the first click point."""
        time.sleep(0.25)  # let the focus change settle before checking
        first = next(step for step in steps if not step.wait_only)
        on_top = window_info_at(first.x, first.y)
        if on_top and on_top.app != anchor.app:
            status += f"  ⚠ “{on_top.app}” is covering the click area"
        return PreparationResult(steps, status)

    def guard(self, step: Step, cancelled: Event) -> str | None:
        """Runs on the player thread before every click: allow it only when
        the target window is on top at the click point. Tries to refocus the
        target first; returns the covering window's name when it gives up."""
        anchor = self.anchor
        if anchor is None:
            return None
        on_top = None
        for attempt in range(3):
            if cancelled.is_set():
                return None
            on_top = window_info_at(step.x, step.y, include_own=True)
            if on_top and on_top.app == anchor.app:
                return None
            if attempt < 2:
                focus_window(anchor)
                if cancelled.wait(0.4):
                    return None
        return on_top.app if on_top else "no window detected"

