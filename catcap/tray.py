"""Panel tray icon (TRG-03). Qt talks StatusNotifierItem or XEmbed; Cinnamon's tray shows both.

Left-click captures a region, middle-click opens the library, right-click opens the menu.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCursor, QIcon, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

RECENT = 6


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = QSystemTrayIcon(app.tray_icon)
        self.icon.setToolTip("CatCap\nClick to capture a region. Right-click for more.")
        self.menu = self._menu()
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.show()

    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _menu(self) -> QMenu:
        t = self.app.theme
        menu = QMenu()
        for mode, label, glyph in (("region", "Capture region or window", "capture"),
                                   ("window", "Capture window", "window"),
                                   ("full", "Capture full screen", "screen"),
                                   ("monitor", "Capture this monitor", "screen"),
                                   ("active", "Capture active window", "window"),
                                   ("last_region", "Repeat last region", "repeat")):
            menu.addAction(t.icon(glyph), label, lambda m=mode: self.app.start_capture(m))
        delay = menu.addMenu(t.icon("timer"), "Capture after a delay")
        for seconds in (3, 5, 10):
            delay.addAction(f"{seconds} seconds", lambda s=seconds: self.app.start_capture("region", delay=s))
        menu.addSeparator()
        self.recent = menu.addMenu(t.icon("clock"), "Recent captures")
        menu.addAction(t.icon("copy"), "Copy last capture", self.app.copy_last)
        menu.addAction(t.icon("images"), "Open library", self.app.show_library)
        menu.addSeparator()
        self.login = menu.addAction("Start at login")
        self.login.setCheckable(True)
        self.login.toggled.connect(self._toggle_login)
        menu.addAction(t.icon("settings"), "Settings", self.app.show_settings)
        menu.addAction("About CatCap", self.app.show_about)
        menu.addAction("Quit CatCap", self.app.quit)
        menu.aboutToShow.connect(self._refresh)
        return menu

    def _refresh(self):
        from . import render
        from .library import Query

        self.login.blockSignals(True)
        self.login.setChecked(bool(self.app.config.get("general", "start_at_login")))
        self.login.blockSignals(False)
        self.recent.clear()
        caps = self.app.library.query(Query())[:RECENT]
        if not caps:
            empty = self.recent.addAction("No captures yet")
            empty.setEnabled(False)
            return
        for cap in caps:
            try:
                thumb = QIcon(QPixmap.fromImage(render.render_thumbnail(self.app.library, cap.id, 200)))
            except Exception:
                thumb = QIcon()
            act = self.recent.addAction(thumb, f"{cap.name}   {cap.width} × {cap.height}")
            act.triggered.connect(lambda _c=False, cid=cap.id: self.app.open_editor(cid))
        self.recent.addSeparator()
        self.recent.addAction("Show all in the library", self.app.show_library)

    def _toggle_login(self, on):
        self.app.config.set("general", "start_at_login", bool(on))
        self.app.update_autostart()

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            # Some XEmbed trays deliver the right-click without Qt opening the menu.
            if not self.menu.isVisible():
                QTimer.singleShot(0, lambda: self.menu.popup(QCursor.pos()))
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.app.start_capture("region")
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.app.show_library()

    def message(self, title: str, body: str) -> bool:
        if QSystemTrayIcon.supportsMessages():
            self.icon.showMessage(title, body, self.app.app_icon, 5000)
            return True
        return False
