"""X11 capture: root window grab, windows from libwnck, cursor from XFixes."""

from __future__ import annotations

import ctypes
import ctypes.util
import os

import cairo
import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402

from . import Snapshot, WindowInfo, monitors_logical  # noqa: E402

try:
    gi.require_version("Wnck", "3.0")
    from gi.repository import Wnck  # noqa: E402
except (ValueError, ImportError):  # pragma: no cover - optional
    Wnck = None


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
        root = Gdk.get_default_root_window()
        lw, lh = root.get_width(), root.get_height()
        windows, active = self._windows()  # list before grabbing, overlay not shown yet
        pixbuf = Gdk.pixbuf_get_from_window(root, 0, 0, lw, lh)
        if pixbuf is None:
            raise RuntimeError("could not read the screen")
        scale = pixbuf.get_width() / lw
        wscale = self._wnck_scale(pixbuf.get_width())
        windows = [self._scaled(w, wscale) for w in windows]
        active = self._scaled(active, wscale) if active else None
        monitors = [(n, round(x * scale), round(y * scale), round(w * scale), round(h * scale))
                    for n, x, y, w, h in monitors_logical()]
        seat = Gdk.Display.get_default().get_default_seat()
        _screen, px, py = seat.get_pointer().get_position()
        pointer = (round(px * scale), round(py * scale))
        if include_cursor:
            pixbuf = _composite_cursor(pixbuf) or pixbuf
        return Snapshot(pixbuf=pixbuf, scale=scale, windows=windows, active=active,
                        monitors=monitors, pointer=pointer, backend="x11")

    # ------------------------------------------------------------- windows
    @staticmethod
    def _wnck_scale(image_width: int) -> float:
        if Wnck is None:
            return 1.0
        screen = Wnck.Screen.get_default()
        return image_width / screen.get_width() if screen and screen.get_width() else 1.0

    @staticmethod
    def _scaled(win: WindowInfo, k: float) -> WindowInfo:
        if abs(k - 1.0) < 1e-6:
            return win
        cx, cy, cw, ch = win.client
        return WindowInfo(round(win.x * k), round(win.y * k), round(win.w * k), round(win.h * k),
                          (round(cx * k), round(cy * k), round(cw * k), round(ch * k)), win.app, win.title)

    def _windows(self) -> tuple[list[WindowInfo], WindowInfo | None]:
        if Wnck is None:
            return [], None
        screen = Wnck.Screen.get_default()
        if screen is None:
            return [], None
        screen.force_update()
        workspace = screen.get_active_workspace()
        skip = {Wnck.WindowType.DESKTOP}
        own_pid = os.getpid()
        result: list[WindowInfo] = []
        active_info = None
        active = screen.get_active_window()
        for win in reversed(screen.get_windows_stacked() or []):
            if win.is_minimized() or win.get_window_type() in skip:
                continue
            if workspace is not None and not win.is_visible_on_workspace(workspace):
                continue
            if win.get_pid() == own_pid:
                continue
            info = self._info(win)
            if info.w < 2 or info.h < 2:
                continue
            result.append(info)
            if active is not None and win.get_xid() == active.get_xid():
                active_info = info
        return result, active_info

    @staticmethod
    def _info(win) -> WindowInfo:
        x, y, w, h = win.get_geometry()
        client = tuple(win.get_client_window_geometry())
        app = ""
        group = win.get_class_group()
        if group is not None:
            app = group.get_res_class() or group.get_name() or ""
        if not app and win.get_application() is not None:
            app = win.get_application().get_name() or ""
        return WindowInfo(x, y, w, h, client, app=app.lower(), title=win.get_name() or "")


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


def _cursor_surface():
    """Return (surface, x, y) of the current cursor in device pixels, or None."""
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
        img_p = xfixes.XFixesGetCursorImage(dpy)
        if not img_p:
            return None
        img = img_p.contents
        w, h = img.width, img.height
        if w == 0 or h == 0:
            xlib.XFree(img_p)
            return None
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        stride = surface.get_stride()
        data = surface.get_data()
        for row in range(h):
            for col in range(w):
                argb = img.pixels[row * w + col] & 0xFFFFFFFF
                offset = row * stride + col * 4
                data[offset:offset + 4] = argb.to_bytes(4, "little")
        surface.mark_dirty()
        result = (surface, img.x - img.xhot, img.y - img.yhot)
        xlib.XFree(img_p)
        return result
    finally:
        xlib.XCloseDisplay(dpy)


def _composite_cursor(pixbuf):
    cursor = _cursor_surface()
    if cursor is None:
        return None
    from ..render import pixbuf_to_surface, surface_to_pixbuf

    surface, cx, cy = cursor
    base = pixbuf_to_surface(pixbuf)
    ctx = cairo.Context(base)
    ctx.set_source_surface(surface, cx, cy)
    ctx.paint()
    base.flush()
    return surface_to_pixbuf(base)
