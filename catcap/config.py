"""Settings stored in $XDG_CONFIG_HOME/catcap/config.toml.

Python 3.10 (Linux Mint 21) has no tomllib, so this module carries a small
reader/writer for the flat subset of TOML that CatCap uses: [sections] with
string, integer, float and boolean values.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from . import paths

DEFAULTS: dict[str, dict] = {
    "general": {
        "start_at_login": False,
        "notifications": True,
        "sound": False,
        "first_run_done": False,
        "theme": "system",
    },
    "capture": {
        # editor | clipboard | library | toolbar
        "after_action": "toolbar",
        "include_cursor": False,
        "delay": 0,
        "window_decorations": True,
        "magnifier": True,
        "magnifier_zoom": 8,
        "snap_to_windows": True,
    },
    "hotkeys": {
        "enabled": False,
        "take_over_print": True,
        "region": "Print",
        "window": "<Alt>Print",
        "full": "<Shift>Print",
        "active": "<Primary><Alt>Print",
        "last_region": "<Primary><Shift>Print",
        "library": "",
    },
    "library": {
        "images_dir": "",
        "trash_days": 30,
        "thumb_size": 160,
        "view": "grid",
        "sort": "date_desc",
    },
    "output": {
        "filename_template": "{app}_{yyyy-MM-dd}_{HHmmss}",
        "default_format": "png",
        "jpg_quality": 90,
        "last_export_dir": "",
    },
    "editor": {
        "color": "#e53935",
        "width": 4,
        "font": "Sans Bold",
        "font_size": 22,
        "fill": False,
        "blur": 12,
    },
    "state": {
        "last_region": "",
        "cinnamon_backup": "",
    },
}

_KEY_RE = re.compile(r"^([A-Za-z0-9_\-]+)\s*=\s*(.+)$")


def _parse_value(raw: str):
    raw = raw.strip()
    if raw.startswith('"'):
        end = _string_end(raw)
        return json.loads(raw[: end + 1])
    raw = raw.split("#", 1)[0].strip()
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _string_end(raw: str) -> int:
    i = 1
    while i < len(raw):
        if raw[i] == "\\":
            i += 2
            continue
        if raw[i] == '"':
            return i
        i += 1
    raise ValueError(f"unterminated string: {raw}")


def loads(text: str) -> dict[str, dict]:
    data: dict[str, dict] = {}
    section = data.setdefault("", {})
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = data.setdefault(line[1:-1].strip(), {})
            continue
        m = _KEY_RE.match(line)
        if m:
            try:
                section[m.group(1)] = _parse_value(m.group(2))
            except ValueError:
                continue
    if not data[""]:
        del data[""]
    return data


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


def dumps(data: dict[str, dict]) -> str:
    out = ["# CatCap settings. Edit while CatCap is not running, or use Settings."]
    for name, values in data.items():
        out.append("")
        out.append(f"[{name}]")
        for key, value in values.items():
            out.append(f"{key} = {_format_value(value)}")
    return "\n".join(out) + "\n"


class Config:
    """Typed access to settings with defaults; saves on every change."""

    def __init__(self, path: Path | None = None):
        self.path = path or (paths.config_dir() / "config.toml")
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            loaded = loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError):
            return
        for section, values in loaded.items():
            target = self.data.setdefault(section, {})
            for key, value in values.items():
                default = DEFAULTS.get(section, {}).get(key)
                if default is not None and type(default) is not type(value):
                    if isinstance(default, float) and isinstance(value, int):
                        value = float(value)
                    else:
                        continue
                target[key] = value

    def save(self) -> None:
        paths.ensure_dir(self.path.parent)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(dumps(self.data), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def get(self, section: str, key: str):
        value = self.data.get(section, {}).get(key)
        if value is None:
            return DEFAULTS.get(section, {}).get(key)
        return value

    def set(self, section: str, key: str, value, save: bool = True) -> None:
        self.data.setdefault(section, {})[key] = value
        if save:
            self.save()

    def images_dir(self) -> Path:
        custom = self.get("library", "images_dir")
        return Path(os.path.expanduser(custom)) if custom else paths.default_images_dir()
