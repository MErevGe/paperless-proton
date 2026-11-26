import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bridge_container.certificates import subject_alt_name


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Invalid settings file")
    return value


def text_field(value: Any, name: str, maximum: int = 256, required: bool = True) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (required and not value.strip())
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"Invalid {name}")
    return value


def web_origin() -> str:
    origin = os.environ.get("WEB_ORIGIN", "https://localhost:8443")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not 1 <= (parsed.port if parsed.port is not None else 443) <= 65535
    ):
        raise ValueError("WEB_ORIGIN must be an HTTPS origin without a path")
    subject_alt_name(parsed.hostname)
    return origin


class Settings:
    def __init__(self, data: Path):
        self.data = data
        self.path = data / "web" / "settings.json"
        self.value: dict[str, Any] = {
            "host": os.environ.get("BRIDGE_HOST", "localhost"),
            "port": int(os.environ.get("BRIDGE_PORT", "1993")),
            "profiles": {},
        }
        if self.path.exists():
            self.value.update(load_json(self.path))
        self.value["port"] = int(os.environ.get("BRIDGE_PORT", "1993"))
        if not 1 <= self.value["port"] <= 65535:
            raise ValueError("BRIDGE_PORT must be between 1 and 65535")
        subject_alt_name(self.value["host"])

    def save(self, changes: dict[str, Any]) -> None:
        self.replace(self.value | changes)

    def replace(self, value: dict[str, Any]) -> None:
        save_json(self.path, value)
        self.value = value
