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
    """A click, fixed wait, or bounded wait for the profile's target window."""

    name: str
    x: int
    y: int
    delay: float
    wait_only: bool = False
    wait_condition: str | None = None
    timeout: float | None = None

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
        if self.wait_condition is None:
            if self.timeout is not None:
                raise ValueError("timeout: requires a wait_condition")
            return
        if self.wait_condition != "target_window_ready":
            raise ValueError("wait_condition: must be target_window_ready")
        if not self.wait_only:
            raise ValueError("wait_only: must be true for a condition wait")
        if self.delay != 0:
            raise ValueError("delay: must be zero for a condition wait")
        try:
            valid_timeout = (self.timeout is not None and type(self.timeout) in (int, float)
                             and self.timeout > 0 and isfinite(self.timeout))
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("timeout: must be a finite positive number")

    @classmethod
    def from_fields(cls, name: str, x: str, y: str, delay: str,
                    wait_only: bool, wait_condition: str | None = None) -> "Step":
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
            field = "timeout" if wait_condition else "delay"
            raise ValueError(f"{field}: must be a finite number") from exc
        if wait_condition is not None:
            return cls(name.strip(), coordinates[0], coordinates[1], 0,
                       wait_only, wait_condition, seconds)
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
