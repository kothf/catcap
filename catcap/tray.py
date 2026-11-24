"""Panel tray icon (TRG-03). Qt talks StatusNotifierItem or XEmbed, both shown by Cinnamon's tray."""

from __future__ import annotations

from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("catcap: no system tray available; use the library window or hotkeys")
        icon = QSystemTrayIcon(app.tray_icon)
        icon.setToolTip("CatCap: click to capture a region")
        icon.setContextMenu(self._menu())
        icon.activated.connect(self._activated)
        icon.show()
        self.icon = icon

    def _menu(self) -> QMenu:
        t = self.app.theme
        menu = QMenu()
        for mode, label, glyph in (("region", "Region or window", "capture"), ("window", "Window", "window"),
                                   ("full", "Full screen", "screen"), ("monitor", "Current monitor", "screen"),
                                   ("active", "Active window", "window"), ("last_region", "Repeat last region", "repeat")):
            menu.addAction(t.icon(glyph), label, lambda m=mode: self.app.start_capture(m))
        delay = menu.addMenu(t.icon("timer"), "Region after a delay")
        for seconds in (3, 5, 10):
            delay.addAction(f"{seconds} seconds", lambda s=seconds: self.app.start_capture("region", delay=s))
        menu.addSeparator()
        menu.addAction(t.icon("images"), "Open library", self.app.show_library)
        menu.addAction(t.icon("settings"), "Settings", self.app.show_settings)
        menu.addSeparator()
        menu.addAction("Quit CatCap", self.app.quit)
        self._menu_ref = menu
        return menu

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.app.start_capture("region")
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.app.show_library()

    def message(self, title: str, body: str) -> bool:
        if self.icon is not None and QSystemTrayIcon.supportsMessages():
            self.icon.showMessage(title, body, self.app.app_icon, 4000)
            return True
        return False
