"""X11 capture: Qt screen grab, EWMH window list via python-xlib, cursor via XFixes."""

from __future__ import annotations

import ctypes
import ctypes.util
import os

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QCursor, QGuiApplication, QImage, QPainter

from . import Snapshot, WindowInfo

try:
    from Xlib import X, display as xdisplay
    from Xlib.error import XError
except ImportError:  # pragma: no cover - python3-xlib is a package dependency
    xdisplay = None
    XError = Exception

FMT = QImage.Format.Format_ARGB32_Premultiplied


class X11Backend:
    name = "x11"
    window_hover = True
    silent_active_window = True

    def snapshot(self, callback, include_cursor: bool = False) -> None:
        try:
            callback(self.snapshot_sync(include_cursor), None)
        except Exception as exc:  # report instead of crashing the tray process
            callback(None, str(exc))

    def snapshot_sync(self, include_cursor: bool = False) -> Snapshot:
        windows, active = list_windows()   # before any CatCap overlay exists
        screens = QGuiApplication.screens()
        virtual = QGuiApplication.primaryScreen().virtualGeometry()
        scale = max(s.devicePixelRatio() for s in screens) if screens else 1.0
        image = QImage(round(virtual.width() * scale), round(virtual.height() * scale), FMT)
        image.fill(0)
        painter = QPainter(image)
        monitors = []
        for screen in screens:
            geo = screen.geometry()
            pixmap = screen.grabWindow(0)
            if pixmap.isNull():
                continue
            x = round((geo.x() - virtual.x()) * scale)
            y = round((geo.y() - virtual.y()) * scale)
            painter.drawImage(x, y, pixmap.toImage())
            monitors.append((screen.name() or screen.model() or "Monitor", x, y,
                             round(geo.width() * scale), round(geo.height() * scale)))
        painter.end()
        if not monitors:
            raise RuntimeError("could not read the screen")
        pos: QPoint = QCursor.pos()
        pointer = (round((pos.x() - virtual.x()) * scale), round((pos.y() - virtual.y()) * scale))
        offset = (round(virtual.x() * scale), round(virtual.y() * scale))
        windows = [_shift(w, offset) for w in windows]
        active = _shift(active, offset) if active else None
        if include_cursor:
            composite_cursor(image, offset)
        return Snapshot(image=image, scale=scale, origin=(virtual.x(), virtual.y()), windows=windows,
                        active=active, monitors=monitors, pointer=pointer, backend="x11")


def _shift(win: WindowInfo, offset) -> WindowInfo:
    ox, oy = offset
    if not ox and not oy:
        return win
    cx, cy, cw, ch = win.client
    return WindowInfo(win.x - ox, win.y - oy, win.w, win.h, (cx - ox, cy - oy, cw, ch), win.app, win.title)


# ---------------------------------------------------------------- EWMH window list
def list_windows() -> tuple[list[WindowInfo], WindowInfo | None]:
    if xdisplay is None or not os.environ.get("DISPLAY"):
        return [], None
    try:
        d = xdisplay.Display()
    except Exception:
        return [], None
    try:
        root = d.screen().root
        atom = d.intern_atom

        def prop(win, name, kind=X.AnyPropertyType):
            try:
                value = win.get_full_property(atom(name), kind)
            except XError:
                return None
            return value.value if value is not None else None

        stack = prop(root, "_NET_CLIENT_LIST_STACKING") or prop(root, "_NET_CLIENT_LIST") or []
        active_id = (prop(root, "_NET_ACTIVE_WINDOW") or [0])[0]
        current_desktop = (prop(root, "_NET_CURRENT_DESKTOP") or [0])[0]
        hidden = atom("_NET_WM_STATE_HIDDEN")
        skip_types = {atom("_NET_WM_WINDOW_TYPE_DESKTOP")}
        own_pid = os.getpid()
        result, active = [], None
        for wid in reversed(list(stack)):
            win = d.create_resource_object("window", wid)
            try:
                if win.get_attributes().map_state != X.IsViewable:
                    continue
                if hidden in (prop(win, "_NET_WM_STATE") or []):
                    continue
                if skip_types & set(prop(win, "_NET_WM_WINDOW_TYPE") or []):
                    continue
                desk = prop(win, "_NET_WM_DESKTOP")
                if desk is not None and desk[0] not in (current_desktop, 0xFFFFFFFF):
                    continue
                if (prop(win, "_NET_WM_PID") or [0])[0] == own_pid:
                    continue
                geom = win.get_geometry()
                origin = root.translate_coords(win, 0, 0)
                cx, cy, cw, ch = origin.x, origin.y, geom.width, geom.height
                gtk = prop(win, "_GTK_FRAME_EXTENTS")
                if gtk is not None and len(gtk) == 4:      # client-side decorations: drop the shadow
                    left, right, top, bottom = gtk
                    cx, cy, cw, ch = cx + left, cy + top, cw - left - right, ch - top - bottom
                    frame = (cx, cy, cw, ch)
                else:
                    ext = prop(win, "_NET_FRAME_EXTENTS") or [0, 0, 0, 0]
                    left, right, top, bottom = ext
                    frame = (cx - left, cy - top, cw + left + right, ch + top + bottom)
                name = prop(win, "_NET_WM_NAME", atom("UTF8_STRING"))
                title = name.decode("utf-8", "replace") if isinstance(name, bytes) else ""
                if not title:
                    wm_name = win.get_wm_name()
                    title = wm_name if isinstance(wm_name, str) else ""
                wm_class = win.get_wm_class() or ("", "")
                app = (wm_class[1] or wm_class[0] or "").lower()
            except XError:
                continue
            if frame[2] < 2 or frame[3] < 2:
                continue
            info = WindowInfo(*frame, client=(cx, cy, cw, ch), app=app, title=title)
            result.append(info)
            if wid == active_id:
                active = info
        return result, active
    finally:
        d.close()


# ---------------------------------------------------------------- cursor (XFixes)
class _XFixesCursorImage(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short), ("y", ctypes.c_short),
        ("width", ctypes.c_ushort), ("height", ctypes.c_ushort),
        ("xhot", ctypes.c_ushort), ("yhot", ctypes.c_ushort),
        ("cursor_serial", ctypes.c_ulong),
        ("pixels", ctypes.POINTER(ctypes.c_ulong)),
        ("atom", ctypes.c_ulong),
        ("name", ctypes.c_char_p),
    ]


def cursor_image():
    """(QImage, x, y) of the current cursor in device pixels, or None."""
    try:
        xlib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        xfixes = ctypes.CDLL(ctypes.util.find_library("Xfixes") or "libXfixes.so.3")
    except OSError:
        return None
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xlib.XFree.argtypes = [ctypes.c_void_p]
    xfixes.XFixesGetCursorImage.restype = ctypes.POINTER(_XFixesCursorImage)
    xfixes.XFixesGetCursorImage.argtypes = [ctypes.c_void_p]
    dpy = xlib.XOpenDisplay(None)
    if not dpy:
        return None
    try:
        ptr = xfixes.XFixesGetCursorImage(dpy)
        if not ptr:
            return None
        img = ptr.contents
        w, h = img.width, img.height
        if not w or not h:
            xlib.XFree(ptr)
            return None
        data = bytearray(w * h * 4)
        for i in range(w * h):
            data[i * 4:i * 4 + 4] = (img.pixels[i] & 0xFFFFFFFF).to_bytes(4, "little")
        qimg = QImage(bytes(data), w, h, w * 4, FMT).copy()
        result = (qimg, img.x - img.xhot, img.y - img.yhot)
        xlib.XFree(ptr)
        return result
    finally:
        xlib.XCloseDisplay(dpy)


def composite_cursor(image: QImage, offset=(0, 0)) -> None:
    cur = cursor_image()
    if cur is None:
        return
    qimg, x, y = cur
    painter = QPainter(image)
    painter.drawImage(x - offset[0], y - offset[1], qimg)
    painter.end()
