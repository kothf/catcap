"""Wayland capture through org.freedesktop.portal.Screenshot (Qt D-Bus)."""

from __future__ import annotations

import os
import random
import string

from PyQt6.QtCore import QObject, QUrl, pyqtSlot
from PyQt6.QtDBus import (QDBusConnection, QDBusMessage, QDBusPendingCall, QDBusPendingCallWatcher,
                           QDBusPendingReply)
from PyQt6.QtGui import QGuiApplication, QImage

from . import Snapshot

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
REQUEST_IFACE = "org.freedesktop.portal.Request"
FMT = QImage.Format.Format_ARGB32_Premultiplied


class PortalBackend(QObject):
    name = "wayland"
    window_hover = False
    silent_active_window = False

    def __init__(self):
        super().__init__()
        self.bus = QDBusConnection.sessionBus()
        self._pending: dict[str, object] = {}
        self._watchers: list = []

    def snapshot(self, callback, include_cursor: bool = False) -> None:
        self._request(False, callback)

    def pick_window(self, callback) -> None:
        """Interactive portal dialog: the user chooses a window or area."""
        self._request(True, callback)

    def _request(self, interactive: bool, callback) -> None:
        if not self.bus.isConnected():
            callback(None, "No D-Bus session: screenshots on Wayland need xdg-desktop-portal.")
            return
        token = "catcap_" + "".join(random.choices(string.ascii_lowercase, k=10))
        sender = self.bus.baseService()[1:].replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        self._pending[handle] = callback
        self.bus.connect("", handle, REQUEST_IFACE, "Response", self._on_response)
        msg = QDBusMessage.createMethodCall(PORTAL_BUS, PORTAL_PATH, SCREENSHOT_IFACE, "Screenshot")
        msg.setArguments(["", {"handle_token": token, "interactive": interactive, "modal": False}])
        call: QDBusPendingCall = self.bus.asyncCall(msg)
        watcher = QDBusPendingCallWatcher(call, self)
        self._watchers.append(watcher)

        def finished(w):
            self._watchers.remove(w)
            reply = QDBusPendingReply(w)
            if reply.isError():
                cb = self._pending.pop(handle, None)
                self.bus.disconnect("", handle, REQUEST_IFACE, "Response", self._on_response)
                if cb:
                    cb(None, "Screenshots need xdg-desktop-portal on Wayland (install xdg-desktop-portal-xapp "
                             f"or -gtk). Details: {reply.error().message()}")
            w.deleteLater()

        watcher.finished.connect(finished)

    @pyqtSlot(QDBusMessage)
    def _on_response(self, message: QDBusMessage) -> None:
        handle = message.path()
        callback = self._pending.pop(handle, None)
        self.bus.disconnect("", handle, REQUEST_IFACE, "Response", self._on_response)
        if callback is None:
            return
        args = message.arguments()
        response = int(args[0]) if args else 2
        results = args[1] if len(args) > 1 else {}
        if response != 0:
            callback(None, "cancelled" if response == 1 else "The screenshot portal refused the request.")
            return
        try:
            callback(self._load(str(results.get("uri", ""))), None)
        except Exception as exc:
            callback(None, str(exc))

    @staticmethod
    def _load(uri: str) -> Snapshot:
        path = QUrl(uri).toLocalFile()
        if not path:
            raise RuntimeError("portal returned no file")
        image = QImage(path)
        if image.isNull():
            raise RuntimeError(f"could not read {path}")
        image = image.convertToFormat(FMT)
        try:  # the portal may drop the file into ~/Pictures; CatCap keeps its own copy
            os.unlink(path)
        except OSError:
            pass
        screens = QGuiApplication.screens() if QGuiApplication.instance() else []
        if screens:
            virtual = screens[0].virtualGeometry()
            scale = image.width() / max(1, virtual.width())
            monitors = [(s.name() or "Monitor", round((s.geometry().x() - virtual.x()) * scale),
                         round((s.geometry().y() - virtual.y()) * scale), round(s.geometry().width() * scale),
                         round(s.geometry().height() * scale)) for s in screens]
            origin = (virtual.x(), virtual.y())
        else:
            scale, monitors, origin = 1.0, [("Monitor", 0, 0, image.width(), image.height())], (0, 0)
        return Snapshot(image=image, scale=scale, origin=origin, monitors=monitors, backend="wayland")
