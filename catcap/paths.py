"""XDG base directories and other well-known locations."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / default


def ensure_dir(path: Path, private: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    return path


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "catcap"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "catcap"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "catcap"


def thumbs_dir() -> Path:
    return ensure_dir(cache_dir() / "thumbs")


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return ensure_dir(Path(base) / "catcap")


def autostart_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart"


def pictures_dir() -> Path:
    """XDG_PICTURES_DIR from ~/.config/user-dirs.dirs (localised on non-English systems)."""
    env = os.environ.get("XDG_PICTURES_DIR")
    if env:
        return Path(env)
    try:
        text = (_xdg("XDG_CONFIG_HOME", ".config") / "user-dirs.dirs").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("XDG_PICTURES_DIR="):
                value = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(Path.home()))
                if value:
                    return Path(value)
    except OSError:
        pass
    return Path.home() / "Pictures"


def default_images_dir() -> Path:
    return pictures_dir() / "CatCap"


def source_root() -> Path | None:
    """Repository root when running from a source checkout, else None."""
    root = Path(__file__).resolve().parent.parent
    return root if (root / "data" / "icons").is_dir() else None


def data_path(*parts: str) -> Path:
    """Locate a bundled data file (source checkout or installed)."""
    root = source_root()
    if root is not None:
        return root.joinpath("data", *parts)
    for prefix in (Path(sys.prefix) / "share", Path("/usr/share"), Path("/usr/local/share")):
        candidate = prefix.joinpath("catcap", *parts)
        if candidate.exists():
            return candidate
    return Path("/usr/share/catcap").joinpath(*parts)


def icons_dir() -> Path:
    root = source_root()
    if root is not None:
        return root / "data" / "icons"
    return Path("/usr/share/icons")


def launcher() -> str:
    """Command used to start CatCap (for hotkeys and autostart)."""
    return os.environ.get("CATCAP_LAUNCHER") or "catcap"
