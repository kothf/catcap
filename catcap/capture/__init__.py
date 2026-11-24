"""Screen capture backends.

X11:     direct root-window grab, window list via libwnck, cursor via XFixes.
Wayland: org.freedesktop.portal.Screenshot (xdg-desktop-portal).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402


@dataclass
class WindowInfo:
    x: int
    y: int
    w: int
    h: int
    client: tuple[int, int, int, int]
    app: str = ""
    title: str = ""

    def rect(self, decorations: bool = True) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h) if decorations else self.client

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


@dataclass
class Snapshot:
    pixbuf: GdkPixbuf.Pixbuf
    scale: float = 1.0                      # image pixels per logical pixel
    windows: list = field(default_factory=list)   # WindowInfo, topmost first
    active: WindowInfo | None = None
    monitors: list = field(default_factory=list)  # (name, x, y, w, h) image px
    pointer: tuple[int, int] = (0, 0)       # image px
    backend: str = "x11"

    def window_at(self, px: float, py: float) -> WindowInfo | None:
        for win in self.windows:
            if win.contains(px, py):
                return win
        return None

    def monitor_at(self, px: float, py: float):
        for mon in self.monitors:
            _name, x, y, w, h = mon
            if x <= px < x + w and y <= py < y + h:
                return mon
        return self.monitors[0] if self.monitors else None

    def crop(self, x: int, y: int, w: int, h: int) -> GdkPixbuf.Pixbuf:
        pw, ph = self.pixbuf.get_width(), self.pixbuf.get_height()
        x, y = max(0, int(x)), max(0, int(y))
        w, h = min(int(w), pw - x), min(int(h), ph - y)
        if w < 1 or h < 1:
            raise ValueError("empty capture area")
        return self.pixbuf.new_subpixbuf(x, y, w, h).copy()


def is_wayland() -> bool:
    display = Gdk.Display.get_default()
    if display is not None:
        return "Wayland" in type(display).__name__
    return os.environ.get("XDG_SESSION_TYPE") == "wayland"


def monitors_logical() -> list[tuple[str, int, int, int, int]]:
    display = Gdk.Display.get_default()
    out = []
    for i in range(display.get_n_monitors()):
        mon = display.get_monitor(i)
        g = mon.get_geometry()
        name = mon.get_model() or f"Monitor {i + 1}"
        out.append((name, g.x, g.y, g.width, g.height))
    return out


def get_backend():
    if is_wayland():
        from .portal import PortalBackend

        return PortalBackend()
    from .x11 import X11Backend

    return X11Backend()
