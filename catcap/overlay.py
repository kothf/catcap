"""Frozen-screen selection overlay (CAP-03/CAP-04, OVL-01..08)."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from .capture import Snapshot, WindowInfo
from .theme import ACCENT, draw_brackets, svg_pixmap

HANDLE = 10        # logical px hit radius
MIN_DRAG = 4
CHROME = QColor(30, 33, 37, 235)
ACTIONS = [("capture", "Capture", "check"), ("copy", "Copy", "copy"), ("edit", "Edit", "edit"),
           ("pin", "Pin", "pin"), ("cancel", "", "close")]


class SelectionResult:
    def __init__(self, image, rect, app="", title="", action=None, mode="region"):
        self.image, self.rect, self.app, self.title, self.action, self.mode = image, rect, app, title, action, mode


class Overlay(QWidget):
    """Shows the frozen snapshot full-screen and lets the user pick an area.

    `snap` covers exactly the area this window covers; selection math is in image pixels.
    """

    def __init__(self, snap: Snapshot, mode: str, config, on_done, screen=None):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        if snap.backend == "x11":
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("CatCap selection")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.snap, self.mode, self.config, self.on_done = snap, mode, config, on_done
        self.image = snap.image
        self.s = snap.scale or 1.0
        self.iw, self.ih = self.image.width(), self.image.height()
        self.state = "idle"
        self.sel: list[float] | None = None
        self.anchor = (0.0, 0.0)
        self.drag_from = (0.0, 0.0)
        self.drag_sel = None
        self.handle = None
        self.hover: WindowInfo | None = None
        self.chosen: WindowInfo | None = None
        self.pointer = tuple(snap.pointer)
        self.magnifier = bool(config.get("capture", "magnifier"))
        self.zoom = max(2, int(config.get("capture", "magnifier_zoom")))
        self.snap_windows = bool(config.get("capture", "snap_to_windows")) or mode == "window"
        self.decorations = bool(config.get("capture", "window_decorations"))
        self.action_rects: list = []
        self.hover_action = None
        self.toast = ""
        self._done = False
        self._screen = screen
        if snap.backend == "x11":
            ox, oy = snap.origin
            self.setGeometry(QRect(ox, oy, round(self.iw / self.s), round(self.ih / self.s)))
        if self.snap_windows:
            self.hover = snap.window_at(*self.pointer)

    def start(self) -> None:
        if self.snap.backend == "wayland":
            if self._screen is not None:
                self.setScreen(self._screen)
                self.setGeometry(self._screen.geometry())
            self.showFullScreen()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(40, self._grab)

    def _grab(self):
        if self._done:
            return
        self.activateWindow()
        self.grabKeyboard()
        self.grabMouse()

    # ------------------------------------------------------------ finishing
    def _close(self):
        self._done = True
        self.releaseKeyboard()
        self.releaseMouse()
        self.hide()
        self.close()

    def _cancel(self):
        if self._done:
            return
        self._close()
        QTimer.singleShot(0, lambda: self.on_done(None))

    def _finish(self, rect, action=None, window: WindowInfo | None = None):
        if self._done:
            return
        x, y, w, h = [int(round(v)) for v in rect]
        x, y = max(0, x), max(0, y)
        w, h = min(w, self.iw - x), min(h, self.ih - y)
        if w < 1 or h < 1:
            return
        image = self.image.copy(x, y, w, h)
        info = window or self.snap.window_at(x + w / 2, y + h / 2) or self.snap.active
        result = SelectionResult(image, (x, y, w, h), info.app if info else "", info.title if info else "",
                                 action, "window" if window is not None else self.mode)
        self._close()
        QTimer.singleShot(60, lambda: self.on_done(result))

    # ------------------------------------------------------------ geometry
    def _img(self, pos) -> tuple[float, float]:
        return pos.x() * self.s, pos.y() * self.s

    def _clamp(self, sel):
        x, y, w, h = sel
        w, h = min(w, self.iw), min(h, self.ih)
        return [max(0, min(x, self.iw - w)), max(0, min(y, self.ih - h)), w, h]

    def _lsel(self) -> QRectF | None:
        return QRectF(*[v / self.s for v in self.sel]) if self.sel else None

    def _handles(self):
        r = self._lsel()
        if r is None:
            return []
        return [("nw", r.left(), r.top()), ("n", r.center().x(), r.top()), ("ne", r.right(), r.top()),
                ("e", r.right(), r.center().y()), ("se", r.right(), r.bottom()), ("s", r.center().x(), r.bottom()),
                ("sw", r.left(), r.bottom()), ("w", r.left(), r.center().y())]

    def _handle_at(self, lx, ly):
        for name, hx, hy in self._handles():
            if abs(lx - hx) <= HANDLE and abs(ly - hy) <= HANDLE:
                return name
        return None

    def _inside(self, ix, iy):
        if not self.sel:
            return False
        x, y, w, h = self.sel
        return x <= ix <= x + w and y <= iy <= y + h

    def _action_at(self, lx, ly):
        for name, rect in self.action_rects:
            if rect.contains(QPointF(lx, ly)):
                return name
        return None

    def _window_rect(self, win: WindowInfo):
        x, y, w, h = win.rect(self.decorations)
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(self.iw, x + w), min(self.ih, y + h)
        return [x1, y1, x2 - x1, y2 - y1]

    # ------------------------------------------------------------ mouse
    def mouseMoveEvent(self, ev):
        pos = ev.position()
        ix, iy = self._img(pos)
        self.pointer = (ix, iy)
        if self.state == "drawing" and self.mode == "region":
            if math.hypot(pos.x() - self.anchor[0] / self.s, pos.y() - self.anchor[1] / self.s) >= MIN_DRAG:
                ax, ay = self.anchor
                self.sel = self._clamp([min(ax, ix), min(ay, iy), abs(ix - ax), abs(iy - ay)])
                self.hover = None
        elif self.state == "moving" and self.drag_sel:
            x, y, w, h = self.drag_sel
            self.sel = self._clamp([x + ix - self.drag_from[0], y + iy - self.drag_from[1], w, h])
        elif self.state == "resizing" and self.drag_sel:
            self._resize_to(ix, iy)
        elif self.state == "idle" and self.snap_windows:
            self.hover = self.snap.window_at(ix, iy)
        elif self.state == "selected":
            self.hover_action = self._action_at(pos.x(), pos.y())
            self._update_cursor(pos)
        self.update()

    def _update_cursor(self, pos):
        shapes = {"nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                  "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
                  "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                  "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor}
        handle = self._handle_at(pos.x(), pos.y())
        if self.hover_action:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif handle:
            self.setCursor(shapes[handle])
        elif self._inside(*self._img(pos)):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def _resize_to(self, ix, iy):
        x, y, w, h = self.drag_sel
        x1, y1, x2, y2 = x, y, x + w, y + h
        if "n" in self.handle:
            y1 = iy
        if "s" in self.handle:
            y2 = iy
        if "w" in self.handle:
            x1 = ix
        if "e" in self.handle:
            x2 = ix
        nx1, nx2 = sorted((max(0, x1), min(self.iw, x2)))
        ny1, ny2 = sorted((max(0, y1), min(self.ih, y2)))
        self.sel = [nx1, ny1, max(1, nx2 - nx1), max(1, ny2 - ny1)]

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.position()
        ix, iy = self._img(pos)
        if self.state == "selected":
            action = self._action_at(pos.x(), pos.y())
            if action:
                if action == "cancel":
                    self._cancel()
                else:
                    self._finish(self.sel, None if action == "capture" else action, self.chosen)
                return
            handle = self._handle_at(pos.x(), pos.y())
            if handle:
                self.state, self.handle, self.drag_sel = "resizing", handle, list(self.sel)
                return
            if self._inside(ix, iy):
                self.state, self.drag_from, self.drag_sel = "moving", (ix, iy), list(self.sel)
                return
        self.state, self.anchor, self.sel, self.chosen = "drawing", (ix, iy), None, None
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if self.sel and self._inside(*self._img(ev.position())):
            self._finish(self.sel, None, self.chosen)

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.state == "drawing":
            if self.sel is None or self.sel[2] < 2 or self.sel[3] < 2:
                ix, iy = self._img(ev.position())
                win = self.snap.window_at(ix, iy) if self.snap_windows else None
                if win is not None:
                    self._finish(self._window_rect(win), None, win)
                    return
                self.sel, self.state = None, "idle"
            else:
                self.state = "selected"
        elif self.state in ("moving", "resizing"):
            self.state = "selected"
        self.update()

    # ------------------------------------------------------------ keyboard
    def keyPressEvent(self, ev):
        key = ev.key()
        mods = ev.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Escape:
            self._cancel()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.sel:
                self._finish(self.sel, None, self.chosen)
            elif self.hover is not None:
                self._finish(self._window_rect(self.hover), None, self.hover)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = (10 if shift else 1) * self.s
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(key, 0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(key, 0)
            if self.sel and self.state == "selected":
                x, y, w, h = self.sel
                self.sel = self._clamp([x, y, max(1, w + dx), max(1, h + dy)] if ctrl else [x + dx, y + dy, w, h])
            else:
                x = min(max(0, self.pointer[0] + dx), self.iw - 1)
                y = min(max(0, self.pointer[1] + dy), self.ih - 1)
                self.pointer = (x, y)
                QCursor.setPos(self.mapToGlobal(QPointF(x / self.s, y / self.s).toPoint()))
        elif key == Qt.Key.Key_C:
            color = self._color_at(*self.pointer)
            QGuiApplication.clipboard().setText(color)
            self.toast = f"Copied {color}"
            QTimer.singleShot(1400, self._clear_toast)
        elif key == Qt.Key.Key_M:
            self.magnifier = not self.magnifier
        elif key == Qt.Key.Key_F or (key == Qt.Key.Key_A and ctrl):
            self.sel, self.state = [0, 0, self.iw, self.ih], "selected"
        self.update()

    def _clear_toast(self):
        self.toast = ""
        self.update()

    def _color_at(self, ix, iy) -> str:
        c = self.image.pixelColor(int(min(max(0, ix), self.iw - 1)), int(min(max(0, iy), self.ih - 1)))
        return c.name().upper()

    # ------------------------------------------------------------ painting
    def paintEvent(self, _ev):
        p = QPainter(self)
        lw, lh = self.iw / self.s, self.ih / self.s
        p.drawImage(QRectF(0, 0, lw, lh), self.image)
        hole = None
        if self.sel:
            hole = self.sel
        elif self.hover is not None and self.state in ("idle", "drawing"):
            hole = self._window_rect(self.hover)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dim = QPainterPath()
        dim.setFillRule(Qt.FillRule.OddEvenFill)
        dim.addRect(QRectF(0, 0, lw, lh))
        if hole:
            dim.addRect(QRectF(*[v / self.s for v in hole]))
        p.fillPath(dim, QColor(10, 12, 14, 120 if hole else 70))
        accent = QColor(ACCENT)
        px, py = self.pointer[0] / self.s, self.pointer[1] / self.s
        if hole:
            r = QRectF(*[v / self.s for v in hole])
            p.setPen(QPen(QColor(255, 255, 255, 110), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            draw_brackets(p, r.adjusted(-3, -3, 3, 3), accent, 18, 4)
            label = f"{int(hole[2])} × {int(hole[3])}"
            if self.hover is not None and not self.sel and self.hover.title:
                label = f"{self.hover.title[:60]}    {label}"
            self._pill(p, label, r.left() - 3, r.top() - 38 if r.top() > 44 else r.top() + 8)
        if self.state == "idle" and not self.sel:
            p.setPen(QPen(QColor(255, 255, 255, 120), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(0, int(py) + 0.5), QPointF(lw, int(py) + 0.5))
            p.drawLine(QPointF(int(px) + 0.5, 0), QPointF(int(px) + 0.5, lh))
        if self.sel and self.state != "drawing":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255))
            for name, hx, hy in self._handles():
                if len(name) == 1:
                    p.drawRoundedRect(QRectF(hx - 7, hy - 2.5, 14, 5) if name in "ns" else
                                      QRectF(hx - 2.5, hy - 7, 5, 14), 2.5, 2.5)
        if self.state == "selected":
            self._draw_actions(p)
        else:
            self.action_rects = []
        if self.magnifier and self.state in ("idle", "drawing", "resizing"):
            self._draw_magnifier(p, px, py, lw, lh)
        hints = {
            "region": "Drag to select    Click a window    Arrows nudge    C copies colour    Esc cancels",
            "window": "Click a window to capture it    Enter takes the highlighted one    Esc cancels",
        }
        hint = hints.get(self.mode, hints["region"])
        if self.state == "selected":
            hint = "Drag edges to adjust    Arrows move, Ctrl+arrows resize    Enter captures    Esc cancels"
        self._pill(p, self.toast or hint, None, 16, center=lw)
        p.end()

    def _pill(self, p: QPainter, text: str, x, y, center=None):
        font = QFont(self.font())
        font.setPointSizeF(10)
        p.setFont(font)
        fm = QFontMetricsF(font)
        w, h = fm.horizontalAdvance(text) + 24, fm.height() + 12
        if center is not None:
            x = (center - w) / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(CHROME)
        p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        p.setPen(QColor(236, 239, 237))
        p.drawText(QRectF(x, y, w, h), int(Qt.AlignmentFlag.AlignCenter), text)

    def _draw_actions(self, p: QPainter):
        r = self._lsel()
        font = QFont(self.font())
        font.setPointSizeF(10)
        p.setFont(font)
        fm = QFontMetricsF(font)
        widths = [(fm.horizontalAdvance(label) + 44 if label else 36) for _n, label, _i in ACTIONS]
        bh, gap = 40, 4
        bw = sum(widths) + gap * (len(widths) - 1) + 12
        lw, lh = self.iw / self.s, self.ih / self.s
        bx = min(max(6, r.right() - bw), lw - bw - 6)
        by = r.bottom() + 12
        if by + bh > lh - 6:
            by = r.top() - bh - 12 if r.top() - bh - 12 > 6 else r.bottom() - bh - 12
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.setBrush(CHROME)
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 14, 14)
        cx = bx + 6
        self.action_rects = []
        for (name, label, icon), w in zip(ACTIONS, widths, strict=True):
            rect = QRectF(cx, by + 5, w, bh - 10)
            if name == "capture":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(ACCENT))
                p.drawRoundedRect(rect, 10, 10)
            elif self.hover_action == name:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 28))
                p.drawRoundedRect(rect, 10, 10)
            pm = svg_pixmap(icon, "#FFFFFF", 16)
            if label:
                p.drawPixmap(QPointF(rect.left() + 10, rect.center().y() - 8), pm)
                p.setPen(QColor(255, 255, 255))
                p.drawText(rect.adjusted(30, 0, -8, 0), int(Qt.AlignmentFlag.AlignVCenter), label)
            else:
                p.drawPixmap(QPointF(rect.center().x() - 8, rect.center().y() - 8), pm)
            self.action_rects.append((name, rect))
            cx += w + gap

    def _draw_magnifier(self, p: QPainter, px, py, lw, lh):
        size = 128
        mx, my = px + 26, py + 26
        if mx + size > lw:
            mx = px - 26 - size
        if my + size + 34 > lh:
            my = py - 26 - size - 34
        ix, iy = int(self.pointer[0]), int(self.pointer[1])
        cells = size / (self.zoom / self.s)
        src = QRectF(ix + 0.5 - cells / 2, iy + 0.5 - cells / 2, cells, cells)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(mx, my, size, size), 14, 14)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(mx, my, size, size), QColor(0, 0, 0))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(QRectF(mx, my, size, size), self.image, src)
        p.restore()
        cell = self.zoom / self.s
        p.setPen(QPen(QColor(ACCENT), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(mx + size / 2 - cell / 2, my + size / 2 - cell / 2, cell, cell))
        p.setPen(QPen(QColor(255, 255, 255, 200), 2))
        p.drawRoundedRect(QRectF(mx, my, size, size), 14, 14)
        color = self._color_at(ix, iy)
        swatch_y = my + size + 6
        self._pill(p, f"   {ix}, {iy}    {color}", mx, swatch_y)
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(mx + 14, swatch_y + 11), 5, 5)
