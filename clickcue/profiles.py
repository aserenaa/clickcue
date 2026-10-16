"""Profile persistence as JSON files."""

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import Step, TargetWindow

DEFAULT_DIR = Path.home() / ".clickcue" / "profiles"


@dataclass
class Profile:
    """A named click sequence with its loop settings and target window."""

    name: str
    steps: list[Step] = field(default_factory=list)
    loop_forever: bool = True
    repetitions: int = 1
    target_window: TargetWindow | None = None

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
        target = None
        if data.get("target_window"):
            target = TargetWindow(**data["target_window"])
        elif data.get("target_app"):  # profiles saved by ClickCue < 0.2
            target = TargetWindow(app=data["target_app"])
        return cls(
            name=data["name"],
            steps=[
                Step(s["name"], int(s["x"]), int(s["y"]), float(s["delay"]),
                     wait_only=bool(s.get("wait_only", False)))
                for s in data.get("steps", [])
            ],
            loop_forever=bool(data.get("loop_forever", True)),
            repetitions=int(data.get("repetitions", 1)),
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
                    prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
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
