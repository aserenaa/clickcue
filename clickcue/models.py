"""Data model for click sequences."""

from dataclasses import dataclass


@dataclass
class Step:
    """A named click with a preceding delay, or a delay without a click."""

    name: str
    x: int
    y: int
    delay: float
    wait_only: bool = False


@dataclass
class TargetWindow:
    """The window a profile is anchored to, with its bounds at capture time.

    Bounds may be None for profiles that only recorded an application name.
    """

    app: str
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None

    @property
    def has_bounds(self) -> bool:
        return None not in (self.x, self.y, self.width, self.height)

    def same_bounds(self, other: "TargetWindow") -> bool:
        return (self.has_bounds and other.has_bounds
                and (self.x, self.y, self.width, self.height)
                == (other.x, other.y, other.width, other.height))
