"""Screen capture backends.

X11:     Qt screen grab, window list via python-xlib (EWMH), cursor via XFixes.
Wayland: org.freedesktop.portal.Screenshot (xdg-desktop-portal) over Qt D-Bus.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from PyQt6.QtGui import QGuiApplication, QImage


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
    image: QImage
    scale: float = 1.0                      # image pixels per logical pixel
    origin: tuple[int, int] = (0, 0)        # logical position of image (0, 0)
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

    def crop(self, x: int, y: int, w: int, h: int) -> QImage:
        iw, ih = self.image.width(), self.image.height()
        x, y = max(0, int(x)), max(0, int(y))
        w, h = min(int(w), iw - x), min(int(h), ih - y)
        if w < 1 or h < 1:
            raise ValueError("empty capture area")
        return self.image.copy(x, y, w, h)


def is_wayland() -> bool:
    """True in a Wayland session, even when Qt itself runs through XWayland (screens of
    other apps can then only be read through the portal)."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return True
    app = QGuiApplication.instance()
    return app is not None and QGuiApplication.platformName().startswith("wayland")


def get_backend():
    if is_wayland():
        from .portal import PortalBackend

        return PortalBackend()
    from .x11 import X11Backend

    return X11Backend()
