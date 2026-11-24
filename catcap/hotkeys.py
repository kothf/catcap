"""Global hotkeys (TRG-01/TRG-02).

On Cinnamon (X11 and Wayland) CatCap registers its CLI commands as Cinnamon custom keyboard
shortcuts, so they work even when CatCap is not running and need no key grab. Taking over
Print Screen clears Cinnamon's own screenshot bindings; the old values are saved and restored
when hotkeys are switched off or `catcap --release-hotkeys` is run.

Settings are changed with the `gsettings` command, so no GTK libraries are needed.
"""

from __future__ import annotations

import ast
import json
import os
import shlex
import shutil
import subprocess

from . import paths

ACTIONS = [
    ("region", "Region or window", "--region"),
    ("window", "Window", "--window"),
    ("full", "Full screen", "--full"),
    ("active", "Active window", "--active"),
    ("last_region", "Repeat last region", "--last-region"),
    ("library", "Open library", "--library"),
]

KB_SCHEMA = "org.cinnamon.desktop.keybindings"
CUSTOM_SCHEMA = "org.cinnamon.desktop.keybindings.custom-keybinding"
CUSTOM_PATH = "/org/cinnamon/desktop/keybindings/custom-keybindings/{}/"
MEDIA_SCHEMA = "org.cinnamon.desktop.keybindings.media-keys"
WM_SCHEMA = "org.cinnamon.desktop.keybindings.wm"
SCREENSHOT_KEYS = ["screenshot", "screenshot-clip", "window-screenshot", "window-screenshot-clip",
                   "area-screenshot", "area-screenshot-clip"]
PREFIX = "catcap-"


# ---------------------------------------------------------------- gsettings wrapper
def _run(*args) -> str:
    if not shutil.which("gsettings"):
        raise FileNotFoundError("gsettings")
    out = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=5)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"gsettings {' '.join(args)} failed")
    return out.stdout.strip()


def _parse(text: str):
    text = text.strip()
    if text.startswith("@"):
        text = text.split(" ", 1)[1] if " " in text else ""
    try:
        return ast.literal_eval(text) if text else None
    except (ValueError, SyntaxError):
        return text


_schemas_cache: set | None = None


def schema_exists(schema: str) -> bool:
    global _schemas_cache
    if _schemas_cache is None:
        try:
            _schemas_cache = set(_run("list-schemas").split()) | set(_run("list-relocatable-schemas").split())
        except (OSError, RuntimeError, subprocess.SubprocessError):
            _schemas_cache = set()
    return schema in _schemas_cache


class GSettings:
    def __init__(self, schema: str, path: str | None = None):
        self.target = f"{schema}:{path}" if path else schema
        self.schema = schema

    def get(self, key):
        return _parse(_run("get", self.target, key))

    def strv(self, key) -> list[str]:
        value = self.get(key)
        return [str(v) for v in value] if isinstance(value, (list, tuple)) else []

    def set_strv(self, key, values) -> None:
        _run("set", self.target, key, "[" + ", ".join(repr(str(v)) for v in values) + "]")

    def set_string(self, key, value: str) -> None:
        _run("set", self.target, key, repr(str(value)))

    def reset(self, key) -> None:
        _run("reset", self.target, key)

    def keys(self) -> list[str]:
        return _run("list-keys", self.schema).split()


# ---------------------------------------------------------------- accelerators
_MOD_ALIASES = {"control": "ctrl", "ctrl": "ctrl", "primary": "ctrl", "alt": "alt", "mod1": "alt",
                "shift": "shift", "super": "super", "mod4": "super", "meta": "super", "hyper": "hyper"}


def _norm(accel: str):
    """Display-independent form of a GTK/Cinnamon accelerator, e.g. ('print', {'ctrl', 'alt'})."""
    accel = (accel or "").strip()
    if not accel:
        return None
    mods = set()
    while accel.startswith("<") and ">" in accel:
        token, accel = accel[1:accel.index(">")], accel[accel.index(">") + 1:]
        mods.add(_MOD_ALIASES.get(token.lower(), token.lower()))
    key = accel.strip().lower()
    return (key, frozenset(mods)) if key else None


def qt_to_gtk(seq: str) -> str:
    """'Ctrl+Alt+Print' (Qt) → '<Primary><Alt>Print' (Cinnamon)."""
    if not seq:
        return ""
    parts = seq.split("+")
    key = parts[-1] or "+"
    mods = {"ctrl": "<Primary>", "alt": "<Alt>", "shift": "<Shift>", "meta": "<Super>"}
    return "".join(mods.get(p.lower(), "") for p in parts[:-1]) + key


def gtk_to_qt(accel: str) -> str:
    norm = _norm(accel)
    if norm is None:
        return ""
    key, mods = norm
    order = [("ctrl", "Ctrl"), ("alt", "Alt"), ("shift", "Shift"), ("super", "Meta")]
    raw_key = accel.split(">")[-1]
    return "+".join([label for m, label in order if m in mods] + [raw_key[:1].upper() + raw_key[1:]])


def is_cinnamon() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("DESKTOP_SESSION", "")
    return "cinnamon" in desktop.lower() and schema_exists(KB_SCHEMA)


# ---------------------------------------------------------------- manager
class HotkeyManager:
    def __init__(self, config):
        self.config = config

    def available(self) -> bool:
        return is_cinnamon() and schema_exists(CUSTOM_SCHEMA)

    def backend_description(self) -> str:
        if self.available():
            return "Saved as Cinnamon keyboard shortcuts, so they work even when CatCap isn't running."
        return ("Global hotkeys are set up automatically on Cinnamon only. On this desktop, bind the commands "
                "below in your keyboard settings.")

    @staticmethod
    def command(flag: str) -> str:
        launcher = paths.launcher()
        return f"{shlex.quote(launcher)} {flag}" if " " in launcher else f"{launcher} {flag}"

    def apply(self) -> list[str]:
        """Apply hotkey settings; returns warnings to show the user."""
        if not self.config.get("hotkeys", "enabled"):
            self.remove()
            return []
        if not self.available():
            return [self.backend_description()]
        try:
            self._apply_cinnamon()
            if self.config.get("hotkeys", "take_over_print"):
                self._take_over_print()
            else:
                self._restore_print()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return [f"Couldn't change Cinnamon's shortcuts: {exc}"]
        warnings = []
        for action, label, _flag in ACTIONS:
            accel = self.config.get("hotkeys", action)
            for other in self.conflicts(accel, action):
                warnings.append(f"{label} ({gtk_to_qt(accel)}) is also used by {other}.")
        return warnings

    def remove(self) -> None:
        if not self.available():
            return
        try:
            self._remove_cinnamon()
            self._restore_print()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"catcap: could not remove hotkeys: {exc}")

    def _apply_cinnamon(self) -> None:
        kb = GSettings(KB_SCHEMA)
        current = kb.strv("custom-list")
        keep = [c for c in current if not c.startswith(PREFIX)]
        ours = []
        for action, label, flag in ACTIONS:
            accel = self.config.get("hotkeys", action)
            if not accel:
                continue
            name = PREFIX + action.replace("_", "-")
            entry = GSettings(CUSTOM_SCHEMA, CUSTOM_PATH.format(name))
            entry.set_string("name", f"CatCap: {label}")
            entry.set_string("command", self.command(flag))
            entry.set_strv("binding", [accel])
            ours.append(name)
        if "__dummy__" in keep:
            keep.remove("__dummy__")
            new_list = (["__dummy__"] + keep + ours) if current and current[0] == "__dummy__" else keep + ours + ["__dummy__"]
        else:
            new_list = keep + ours
        kb.set_strv("custom-list", new_list)

    def _remove_cinnamon(self) -> None:
        kb = GSettings(KB_SCHEMA)
        current = kb.strv("custom-list")
        ours = [c for c in current if c.startswith(PREFIX)]
        for name in ours:
            entry = GSettings(CUSTOM_SCHEMA, CUSTOM_PATH.format(name))
            for key in ("name", "command", "binding"):
                entry.reset(key)
        if ours:
            kb.set_strv("custom-list", [c for c in current if not c.startswith(PREFIX)])

    def _take_over_print(self) -> None:
        if not schema_exists(MEDIA_SCHEMA):
            return
        media = GSettings(MEDIA_SCHEMA)
        keys = set(media.keys())
        if not self.config.get("state", "cinnamon_backup"):
            backup = {k: media.strv(k) for k in SCREENSHOT_KEYS if k in keys}
            self.config.set("state", "cinnamon_backup", json.dumps(backup))
        ours = {_norm(self.config.get("hotkeys", a)) for a, _l, _f in ACTIONS} - {None}
        for k in SCREENSHOT_KEYS:
            if k in keys:
                values = media.strv(k)
                remaining = [a for a in values if _norm(a) not in ours and "Print" not in a]
                if remaining != values:
                    media.set_strv(k, remaining)

    def _restore_print(self) -> None:
        raw = self.config.get("state", "cinnamon_backup")
        if not raw or not schema_exists(MEDIA_SCHEMA):
            return
        try:
            backup = json.loads(raw)
        except ValueError:
            backup = {}
        media = GSettings(MEDIA_SCHEMA)
        keys = set(media.keys())
        for k, value in backup.items():
            if k in keys:
                media.set_strv(k, value)
        self.config.set("state", "cinnamon_backup", "")

    def conflicts(self, accel: str, action: str) -> list[str]:
        target = _norm(accel)
        if target is None:
            return []
        found = [f"CatCap “{label}”" for a, label, _f in ACTIONS
                 if a != action and _norm(self.config.get("hotkeys", a)) == target]
        if not self.available():
            return found
        take_over = self.config.get("hotkeys", "take_over_print")
        try:
            for schema_id in (MEDIA_SCHEMA, WM_SCHEMA):
                if not schema_exists(schema_id):
                    continue
                dump = _run("list-recursively", schema_id).splitlines()
                for line in dump:
                    parts = line.split(" ", 2)
                    if len(parts) < 3:
                        continue
                    key, value = parts[1], _parse(parts[2])
                    if take_over and schema_id == MEDIA_SCHEMA and key in SCREENSHOT_KEYS:
                        continue
                    if isinstance(value, (list, tuple)) and any(_norm(str(v)) == target for v in value):
                        found.append(f"Cinnamon “{key}”")
            for name in GSettings(KB_SCHEMA).strv("custom-list"):
                if name.startswith(PREFIX) or name == "__dummy__":
                    continue
                entry = GSettings(CUSTOM_SCHEMA, CUSTOM_PATH.format(name))
                if any(_norm(v) == target for v in entry.strv("binding")):
                    found.append(f"custom shortcut “{entry.get('name')}”")
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
        return found
