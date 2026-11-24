"""Global hotkeys (TRG-01/TRG-02).

On Cinnamon (X11 and Wayland) CatCap registers its CLI commands as Cinnamon custom
keyboard shortcuts, so they work even when CatCap is not running and need no key grab.
Taking over Print Screen clears Cinnamon's own screenshot bindings (saved for restore).
On other X11 desktops, libkeybinder is used in-process when installed.
"""

from __future__ import annotations

import json
import os
import shlex

import gi

from gi.repository import Gio  # noqa: E402

from . import paths  # noqa: E402

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


def _schema(schema_id: str):
    source = Gio.SettingsSchemaSource.get_default()
    return source.lookup(schema_id, True) if source else None


_MOD_ALIASES = {"control": "ctrl", "ctrl": "ctrl", "primary": "ctrl", "alt": "alt", "mod1": "alt",
                "shift": "shift", "super": "super", "mod4": "super", "meta": "meta", "hyper": "hyper"}


def _norm(accel: str):
    """Display-independent form of a GTK/Cinnamon accelerator, e.g. ('print', {'ctrl','alt'})."""
    accel = (accel or "").strip()
    if not accel:
        return None
    mods = set()
    while accel.startswith("<") and ">" in accel:
        token, accel = accel[1:accel.index(">")], accel[accel.index(">") + 1:]
        mods.add(_MOD_ALIASES.get(token.lower(), token.lower()))
    key = accel.strip().lower()
    return (key, frozenset(mods)) if key else None


def is_cinnamon() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("DESKTOP_SESSION", "")
    return "cinnamon" in desktop.lower() and _schema(KB_SCHEMA) is not None


class HotkeyManager:
    def __init__(self, app):
        self.app = app
        self.config = app.config
        self._keybinder = None
        self._bound: list[str] = []

    # ------------------------------------------------------------ backend
    def backend(self) -> str | None:
        if is_cinnamon() and _schema(CUSTOM_SCHEMA) is not None:
            return "cinnamon"
        if self._load_keybinder():
            return "keybinder"
        return None

    def backend_description(self) -> str:
        return {
            "cinnamon": "Registered as Cinnamon keyboard shortcuts (System Settings › Keyboard › Shortcuts › Custom).",
            "keybinder": "Grabbed by CatCap while it runs in the tray (libkeybinder).",
            None: "Global hotkeys are not available on this desktop. Bind the commands shown below "
                  "(for example “catcap --region”) in your desktop's keyboard settings.",
        }[self.backend()]

    def _load_keybinder(self) -> bool:
        if self._keybinder is not None:
            return bool(self._keybinder)
        try:
            from .capture import is_wayland

            if is_wayland():
                raise ImportError("no key grabs on Wayland")
            gi.require_version("Keybinder", "3.0")
            from gi.repository import Keybinder

            Keybinder.init()
            self._keybinder = Keybinder
        except (ValueError, ImportError):
            self._keybinder = False
        return bool(self._keybinder)

    def command(self, flag: str) -> str:
        launcher = paths.launcher()
        return f"{shlex.quote(launcher)} {flag}" if " " in launcher else f"{launcher} {flag}"

    # ------------------------------------------------------------ apply / remove
    def apply(self) -> list[str]:
        """Apply hotkey settings; returns a list of warnings to show the user."""
        if not self.config.get("hotkeys", "enabled"):
            self.remove()
            return []
        backend = self.backend()
        warnings: list[str] = []
        if backend == "cinnamon":
            self._apply_cinnamon()
            if self.config.get("hotkeys", "take_over_print"):
                self._take_over_print()
            else:
                self._restore_print()
            for action, label, _flag in ACTIONS:
                accel = self.config.get("hotkeys", action)
                for other in self.conflicts(accel, action):
                    warnings.append(f"{label} ({accel}) is also used by {other}.")
        elif backend == "keybinder":
            self._apply_keybinder()
        else:
            warnings.append(self.backend_description())
        return warnings

    def remove(self) -> None:
        if is_cinnamon():
            self._remove_cinnamon()
            self._restore_print()
        if self._keybinder:
            for accel in self._bound:
                self._keybinder.unbind(accel)
            self._bound = []

    def _apply_cinnamon(self) -> None:
        kb = Gio.Settings.new(KB_SCHEMA)
        current = list(kb.get_strv("custom-list"))
        keep = [c for c in current if not c.startswith(PREFIX)]
        ours = []
        for action, label, flag in ACTIONS:
            accel = self.config.get("hotkeys", action)
            if not accel:
                continue
            name = PREFIX + action.replace("_", "-")
            entry = Gio.Settings.new_with_path(CUSTOM_SCHEMA, CUSTOM_PATH.format(name))
            entry.set_string("name", f"CatCap: {label}")
            entry.set_string("command", self.command(flag))
            entry.set_strv("binding", [accel])
            ours.append(name)
        if "__dummy__" in keep:
            keep.remove("__dummy__")
            new_list = ["__dummy__"] + keep + ours if current and current[0] == "__dummy__" else keep + ours + ["__dummy__"]
        else:
            new_list = keep + ours
        kb.set_strv("custom-list", new_list)
        Gio.Settings.sync()

    def _remove_cinnamon(self) -> None:
        kb = Gio.Settings.new(KB_SCHEMA)
        current = list(kb.get_strv("custom-list"))
        ours = [c for c in current if c.startswith(PREFIX)]
        if not ours:
            return
        for name in ours:
            entry = Gio.Settings.new_with_path(CUSTOM_SCHEMA, CUSTOM_PATH.format(name))
            for key in ("name", "command", "binding"):
                entry.reset(key)
        kb.set_strv("custom-list", [c for c in current if not c.startswith(PREFIX)])
        Gio.Settings.sync()

    def _take_over_print(self) -> None:
        if _schema(MEDIA_SCHEMA) is None:
            return
        media = Gio.Settings.new(MEDIA_SCHEMA)
        keys = set(media.list_keys())
        if not self.config.get("state", "cinnamon_backup"):
            backup = {k: list(media.get_strv(k)) for k in SCREENSHOT_KEYS if k in keys}
            self.config.set("state", "cinnamon_backup", json.dumps(backup))
        ours = {_norm(self.config.get("hotkeys", a)) for a, _l, _f in ACTIONS} - {None}
        for k in SCREENSHOT_KEYS:
            if k in keys:
                remaining = [a for a in media.get_strv(k) if _norm(a) not in ours and "Print" not in a]
                media.set_strv(k, remaining)
        Gio.Settings.sync()

    def _restore_print(self) -> None:
        raw = self.config.get("state", "cinnamon_backup")
        if not raw or _schema(MEDIA_SCHEMA) is None:
            return
        try:
            backup = json.loads(raw)
        except ValueError:
            backup = {}
        media = Gio.Settings.new(MEDIA_SCHEMA)
        keys = set(media.list_keys())
        for k, value in backup.items():
            if k in keys:
                media.set_strv(k, value)
        Gio.Settings.sync()
        self.config.set("state", "cinnamon_backup", "")

    def _apply_keybinder(self) -> None:
        for accel in self._bound:
            self._keybinder.unbind(accel)
        self._bound = []
        for action, _label, _flag in ACTIONS:
            accel = self.config.get("hotkeys", action)
            if accel and self._keybinder.bind(accel, self._on_key, action):
                self._bound.append(accel)

    def _on_key(self, _keystring, action):
        if action == "library":
            self.app.show_library()
        else:
            self.app.start_capture(action)

    # ------------------------------------------------------------ conflicts
    def conflicts(self, accel: str, action: str) -> list[str]:
        target = _norm(accel)
        if target is None:
            return []
        found = []
        for a, label, _f in ACTIONS:
            if a != action and _norm(self.config.get("hotkeys", a)) == target:
                found.append(f"CatCap “{label}”")
        if not is_cinnamon():
            return found
        take_over = self.config.get("hotkeys", "take_over_print")
        for schema_id in (MEDIA_SCHEMA, WM_SCHEMA):
            if _schema(schema_id) is None:
                continue
            settings = Gio.Settings.new(schema_id)
            for key in settings.list_keys():
                if take_over and schema_id == MEDIA_SCHEMA and key in SCREENSHOT_KEYS:
                    continue
                try:
                    values = settings.get_strv(key)
                except TypeError:
                    continue
                if any(_norm(v) == target for v in values):
                    found.append(f"Cinnamon “{key}”")
        kb = Gio.Settings.new(KB_SCHEMA)
        for name in kb.get_strv("custom-list"):
            if name.startswith(PREFIX) or name == "__dummy__":
                continue
            entry = Gio.Settings.new_with_path(CUSTOM_SCHEMA, CUSTOM_PATH.format(name))
            if any(_norm(v) == target for v in entry.get_strv("binding")):
                found.append(f"custom shortcut “{entry.get_string('name')}”")
        return found
