"""Profile persistence as JSON files."""

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import (
    Step,
    TargetWindow,
    validate_boolean,
    validate_integer,
    validate_name,
)

DEFAULT_DIR = Path.home() / ".clickcue" / "profiles"


@dataclass
class Profile:
    """A named click sequence with its loop settings and target window."""

    name: str
    steps: list[Step] = field(default_factory=list)
    loop_forever: bool = True
    repetitions: int = 1
    target_window: TargetWindow | None = None

    def __post_init__(self) -> None:
        validate_name(self.name, "name")
        validate_boolean(self.loop_forever, "loop_forever")
        validate_integer(self.repetitions, "repetitions", positive=True)
        if not isinstance(self.steps, list):
            raise ValueError("steps: must be a list")
        for index, step in enumerate(self.steps, 1):
            if not isinstance(step, Step):
                raise ValueError(f"steps[{index}]: must be a step")
            try:
                step.__post_init__()
            except ValueError as exc:
                raise ValueError(f"steps[{index}].{exc}") from exc
        if self.target_window is not None:
            if not isinstance(self.target_window, TargetWindow):
                raise ValueError("target_window: must be a target window")
            try:
                self.target_window.__post_init__()
            except ValueError as exc:
                raise ValueError(f"target_window.{exc}") from exc

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "loop_forever": self.loop_forever,
            "repetitions": self.repetitions,
            "target_window": (asdict(self.target_window)
                              if self.target_window else None),
            "steps": [asdict(step) for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        if not isinstance(data, dict):
            raise ValueError("profile: must be an object")
        target = None
        if data.get("target_window") is not None:
            raw_target = data["target_window"]
            if not isinstance(raw_target, dict):
                raise ValueError("target_window: must be an object")
            try:
                target = TargetWindow(**raw_target)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"target_window.{exc}") from exc
        elif data.get("target_app") is not None:  # ClickCue < 0.2
            try:
                target = TargetWindow(app=data["target_app"])
            except ValueError as exc:
                raise ValueError(f"target_app: {exc}") from exc
        raw_steps = data.get("steps", [])
        if not isinstance(raw_steps, list):
            raise ValueError("steps: must be a list")
        steps = []
        for index, raw in enumerate(raw_steps, 1):
            if not isinstance(raw, dict):
                raise ValueError(f"steps[{index}]: must be an object")
            try:
                steps.append(Step(raw["name"], raw["x"], raw["y"],
                                  raw["delay"], raw.get("wait_only", False)))
            except KeyError as exc:
                raise ValueError(f"steps[{index}].{exc.args[0]}: is required") from exc
            except ValueError as exc:
                raise ValueError(f"steps[{index}].{exc}") from exc
        validate_name(data.get("name"), "name")
        return cls(
            name=data["name"],
            steps=steps,
            loop_forever=data.get("loop_forever", True),
            repetitions=data.get("repetitions", 1),
            target_window=target,
        )


class ProfileStore:
    """Reads and writes profiles under a directory, one JSON file per profile."""

    def __init__(self, directory: Path = DEFAULT_DIR):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def list_names(self) -> list[str]:
        names = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                names.append(json.loads(path.read_text(encoding="utf-8"))["name"])
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return names

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def save(self, profile: Profile) -> None:
        payload = json.dumps(profile.to_dict(), ensure_ascii=False, indent=2)
        path = self._path(profile.name)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=path.parent,
                    prefix=".clickcue-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(self, name: str) -> Profile:
        data = json.loads(self._path(name).read_text(encoding="utf-8"))
        return Profile.from_dict(data)

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    def _path(self, name: str) -> Path:
        slug = re.sub(r"[^\w\- ]", "_", name).strip() or "profile"
        return self.directory / f"{slug}.json"
