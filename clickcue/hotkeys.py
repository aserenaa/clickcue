"""Machine-local function key preferences, separate from shared profiles."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

FUNCTION_KEYS = tuple(f"F{number}" for number in range(1, 13))
DEFAULT_PATH = Path.home() / ".clickcue" / "hotkeys.json"


@dataclass(frozen=True)
class HotkeySettings:
    toggle: str = "F6"
    capture: str = "F8"

    def __post_init__(self) -> None:
        if self.toggle not in FUNCTION_KEYS or self.capture not in FUNCTION_KEYS:
            raise ValueError("Choose function keys from F1 to F12.")
        if self.toggle == self.capture:
            raise ValueError("Start/stop and capture must use different keys.")


def load_hotkeys(path: Path = DEFAULT_PATH) -> HotkeySettings:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return HotkeySettings(data["toggle"], data["capture"])
    except (OSError, ValueError, KeyError, TypeError):
        return HotkeySettings()


def save_hotkeys(settings: HotkeySettings, path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".hotkeys-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(asdict(settings), stream, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
