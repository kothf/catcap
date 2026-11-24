"""Panel tray icon (TRG-03): XApp.StatusIcon on Mint, AppIndicator elsewhere, GtkStatusIcon as last resort."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import APP_ID, paths  # noqa: E402


def _icon_file() -> str | None:
    root = paths.source_root()
    if root is None:
        return None
    return str(root / "data/icons/hicolor/scalable/apps" / f"{APP_ID}.svg")


class Tray:
    def __init__(self, app):
        self.app = app
        self.menu = self._build_menu()
        self.kind = None
        self.icon = None
        for builder in (self._xapp, self._appindicator, self._status_icon):
            try:
                if builder():
                    break
            except (ValueError, ImportError, AttributeError) as exc:
                print(f"catcap: tray backend unavailable: {exc}")

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        def add(label, cb):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda *_: cb())
            menu.append(item)

        add("Region or window", lambda: self.app.start_capture("region"))
        add("Window", lambda: self.app.start_capture("window"))
        add("Full screen", lambda: self.app.start_capture("full"))
        add("Current monitor", lambda: self.app.start_capture("monitor"))
        add("Active window", lambda: self.app.start_capture("active"))
        add("Repeat last region", lambda: self.app.start_capture("last_region"))
        delay_item = Gtk.MenuItem(label="Region after delay")
        sub = Gtk.Menu()
        for seconds in (3, 5, 10):
            item = Gtk.MenuItem(label=f"{seconds} seconds")
            item.connect("activate", lambda _w, s=seconds: self.app.start_capture("region", delay=s))
            sub.append(item)
        delay_item.set_submenu(sub)
        menu.append(delay_item)
        menu.append(Gtk.SeparatorMenuItem())
        add("Open library", lambda: self.app.show_library())
        add("Settings", lambda: self.app.show_settings())
        menu.append(Gtk.SeparatorMenuItem())
        add("Quit CatCap", lambda: self.app.quit())
        menu.show_all()
        return menu

    def _xapp(self) -> bool:
        gi.require_version("XApp", "1.0")
        from gi.repository import XApp

        icon = XApp.StatusIcon()
        icon.set_name("catcap")
        icon.set_icon_name(_icon_file() or APP_ID)
        icon.set_tooltip_text("CatCap: click to capture a region")
        icon.set_secondary_menu(self.menu)
        icon.connect("activate", lambda _i, button, _t: self.app.start_capture("region") if button == 1 else None)
        icon.set_visible(True)
        self.icon, self.kind = icon, "xapp"
        return True

    def _appindicator(self) -> bool:
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except (ValueError, ImportError):
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        indicator = AppIndicator.Indicator.new("catcap", APP_ID,
                                               AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        root = paths.source_root()
        if root is not None:
            indicator.set_icon_theme_path(str(root / "data/icons"))
        indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        indicator.set_title("CatCap")
        indicator.set_menu(self.menu)
        self.icon, self.kind = indicator, "appindicator"
        return True

    def _status_icon(self) -> bool:
        from .capture import is_wayland

        if is_wayland():
            return False
        icon_file = _icon_file()
        icon = Gtk.StatusIcon.new_from_file(icon_file) if icon_file else Gtk.StatusIcon.new_from_icon_name(APP_ID)
        icon.set_tooltip_text("CatCap: click to capture a region")
        icon.connect("activate", lambda *_: self.app.start_capture("region"))
        icon.connect("popup-menu", lambda _i, button, t: self.menu.popup(None, None, None, None, button, t))
        icon.set_visible(True)
        self.icon, self.kind = icon, "statusicon"
        return True
