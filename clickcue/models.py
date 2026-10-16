"""Data model for click sequences."""

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from math import isfinite


def validate_name(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: must be a nonempty string")


def validate_integer(value: object, field: str, *, positive: bool = False) -> None:
    if type(value) is not int or (positive and value <= 0):
        kind = "a positive integer" if positive else "an integer"
        raise ValueError(f"{field}: must be {kind}")


def validate_boolean(value: object, field: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{field}: must be a boolean")


@dataclass
class Step:
    """A named click with a preceding delay, or a delay without a click."""

    name: str
    x: int
    y: int
    delay: float
    wait_only: bool = False

    def __post_init__(self) -> None:
        validate_name(self.name, "name")
        validate_integer(self.x, "x")
        validate_integer(self.y, "y")
        try:
            valid_delay = (type(self.delay) in (int, float)
                           and self.delay >= 0 and isfinite(self.delay))
        except OverflowError:
            valid_delay = False
        if not valid_delay:
            raise ValueError("delay: must be a finite nonnegative number")
        validate_boolean(self.wait_only, "wait_only")

    @classmethod
    def from_fields(cls, name: str, x: str, y: str, delay: str,
                    wait_only: bool) -> "Step":
        """Parse dialog text, then use the same validation as loaded steps."""
        coordinates = []
        for field, value in (("x", x), ("y", y)):
            try:
                coordinates.append(0 if wait_only else int(value))
            except ValueError as exc:
                raise ValueError(f"{field}: must be an integer") from exc
        try:
            seconds = float(delay)
        except ValueError as exc:
            raise ValueError("delay: must be a finite nonnegative number") from exc
        return cls(name.strip(), coordinates[0], coordinates[1], seconds, wait_only)


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

    # Live OS identity only; profiles deliberately omit these fields.
    native_id: int | None = dataclass_field(default=None, compare=False, repr=False)
    pid: int | None = dataclass_field(default=None, compare=False, repr=False)
    title: str | None = dataclass_field(default=None, compare=False)

    def same_window(self, other: "TargetWindow") -> bool:
        return (self.native_id is not None and self.pid is not None
                and (self.native_id, self.pid) == (other.native_id, other.pid))

    def __post_init__(self) -> None:
        validate_name(self.app, "app")
        bounds = (self.x, self.y, self.width, self.height)
        if all(value is None for value in bounds):
            return
        for field in ("x", "y", "width", "height"):
            validate_integer(getattr(self, field), field,
                             positive=field in ("width", "height"))

    @property
    def has_bounds(self) -> bool:
        return None not in (self.x, self.y, self.width, self.height)

    def same_bounds(self, other: "TargetWindow") -> bool:
        return (self.has_bounds and other.has_bounds
                and (self.x, self.y, self.width, self.height)
                == (other.x, other.y, other.width, other.height))
