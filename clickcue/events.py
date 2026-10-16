"""Playback and hotkey events shared with the GUI."""

import queue


class EventQueue(queue.Queue[tuple]):
    """Coalesce consecutive progress updates; retain other events in FIFO order."""

    def _put(self, item: tuple) -> None:
        # Queue calls this hook under its mutex. Never coalesce across a durable
        # event, so progress cannot move past a stop, error, or hotkey.
        if (item[0] in ("waiting", "playing") and self.queue
                and self.queue[-1][0] in ("waiting", "playing")):
            self.queue[-1] = item
            # Queue.put increments this after _put, even for a replacement.
            self.unfinished_tasks -= 1
        else:
            super()._put(item)
