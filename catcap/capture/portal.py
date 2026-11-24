"""Wayland capture through org.freedesktop.portal.Screenshot."""

from __future__ import annotations

import os
import random
import string

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from . import Snapshot, monitors_logical  # noqa: E402

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
REQUEST_IFACE = "org.freedesktop.portal.Request"


class PortalError(RuntimeError):
    pass


class PortalBackend:
    name = "wayland"
    window_hover = False
    silent_active_window = False

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def snapshot(self, callback, include_cursor: bool = False) -> None:
        self._request(False, callback)

    def pick_window(self, callback) -> None:
        """Interactive portal dialog: the user chooses a window or area."""
        self._request(True, callback)

    def _request(self, interactive: bool, callback) -> None:
        token = "catcap_" + "".join(random.choices(string.ascii_lowercase, k=10))
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        state = {"sub": 0}

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            self.bus.signal_unsubscribe(state["sub"])
            response, results = params.unpack()
            if response != 0:
                callback(None, "cancelled" if response == 1 else "The screenshot portal refused the request.")
                return
            uri = results.get("uri", "")
            try:
                snap = self._load(uri)
            except Exception as exc:
                callback(None, str(exc))
                return
            callback(snap, None)

        state["sub"] = self.bus.signal_subscribe(
            PORTAL_BUS, REQUEST_IFACE, "Response", handle, None,
            Gio.DBusSignalFlags.NO_MATCH_RULE, on_response)

        options = {
            "handle_token": GLib.Variant("s", token),
            "interactive": GLib.Variant("b", interactive),
            "modal": GLib.Variant("b", False),
        }

        def on_called(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error as exc:
                self.bus.signal_unsubscribe(state["sub"])
                callback(None, "Screenshots need xdg-desktop-portal on Wayland "
                               f"(install xdg-desktop-portal-xapp or -gtk). Details: {exc.message}")

        self.bus.call(PORTAL_BUS, PORTAL_PATH, SCREENSHOT_IFACE, "Screenshot",
                      GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
                      Gio.DBusCallFlags.NONE, -1, None, on_called)

    @staticmethod
    def _load(uri: str) -> Snapshot:
        path = Gio.File.new_for_uri(uri).get_path()
        if not path:
            raise PortalError("portal returned no file")
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        try:  # the portal may drop the file into ~/Pictures; CatCap keeps its own copy
            os.unlink(path)
        except OSError:
            pass
        mons = monitors_logical()
        min_x = min((m[1] for m in mons), default=0)
        max_x = max((m[1] + m[3] for m in mons), default=pixbuf.get_width())
        min_y = min((m[2] for m in mons), default=0)
        scale = pixbuf.get_width() / max(1, max_x - min_x)
        monitors = [(n, round((x - min_x) * scale), round((y - min_y) * scale),
                     round(w * scale), round(h * scale)) for n, x, y, w, h in mons]
        return Snapshot(pixbuf=pixbuf, scale=scale, monitors=monitors, backend="wayland")
