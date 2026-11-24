"""Editor building blocks: the drawing canvas, the colour button and the drag handle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QColorDialog, QHBoxLayout, QMenu, QToolButton, QWidget

from . import annotations as ann
from . import render, ui
from .editor_tools import BAR_ICON, SWATCHES
from .theme import ACCENT, CANVAS, draw_brackets

if TYPE_CHECKING:
    from .editor import Editor


# ====================================================================== canvas
class Canvas(QWidget):
    cursorMoved = pyqtSignal(float, float)

    def __init__(self, editor: "Editor"):
        super().__init__()
        self.ed = editor
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(False)

    # coordinate mapping ----------------------------------------------
    def origin(self) -> QPointF:
        _cx, _cy, w, h = self.ed.view_rect()
        z = self.ed.zoom
        return QPointF(max(24.0, (self.width() - w * z) / 2), max(24.0, (self.height() - h * z) / 2))

    def to_doc(self, pos) -> tuple[float, float]:
        cx, cy, _w, _h = self.ed.view_rect()
        o = self.origin()
        return (pos.x() - o.x()) / self.ed.zoom + cx, (pos.y() - o.y()) / self.ed.zoom + cy

    def sizeHint(self):
        if self.ed.base is None:
            return QSize(200, 200)
        _cx, _cy, w, h = self.ed.view_rect()
        return QSize(int(w * self.ed.zoom) + 48, int(h * self.ed.zoom) + 48)

    # painting ----------------------------------------------------------
    def paintEvent(self, _ev):
        ed = self.ed
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(CANVAS))
        if ed.rendered is None:
            return
        cx, cy, w, h = ed.view_rect()
        o = self.origin()
        z = ed.zoom
        frame = QRectF(o.x(), o.y(), w * z, h * z)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for i, alpha in enumerate((40, 26, 14)):      # soft drop shadow under the image
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(frame.adjusted(-2 - i * 2, 0 - i, 2 + i * 2, 6 + i * 3), 4 + i, 4 + i)
        p.save()
        p.translate(o)
        p.scale(z, z)
        p.translate(-cx, -cy)
        p.setClipRect(QRectF(cx, cy, w, h))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, z < 2)
        p.drawImage(0, 0, ed.rendered)
        if ed.active is not None:
            kind = ed.active["type"]
            if kind in ann.EFFECT_KINDS or kind == "spotlight":
                self._dashed(p, QRectF(*ann.bbox(ed.active)))
            else:
                render.draw_item(p, ed.active)
        p.setClipping(False)
        if ed.selected is not None and ed.tool == "select":
            x, y, bw, bh = ann.bbox(ed.selected)
            pad = 6 / z
            draw_brackets(p, QRectF(x - pad, y - pad, bw + 2 * pad, bh + 2 * pad), QColor(ACCENT), 12 / z, 3 / z)
            for _n, hx, hy in ann.handles(ed.selected):
                self._knob(p, hx, hy)
        if ed.tool == "crop" and ed.crop_draft:
            r = QRectF(*ed.crop_draft)
            dim = QPainterPath()
            dim.setFillRule(Qt.FillRule.OddEvenFill)
            dim.addRect(QRectF(cx, cy, w, h))
            dim.addRect(r)
            p.fillPath(dim, QColor(0, 0, 0, 140))
            p.setPen(QPen(QColor(255, 255, 255, 90), 1 / z))
            for i in (1, 2):
                p.drawLine(QPointF(r.left() + r.width() * i / 3, r.top()), QPointF(r.left() + r.width() * i / 3, r.bottom()))
                p.drawLine(QPointF(r.left(), r.top() + r.height() * i / 3), QPointF(r.right(), r.top() + r.height() * i / 3))
            draw_brackets(p, r, QColor("white"), 20 / z, 4 / z)
        p.restore()
        p.end()

    def _dashed(self, p, rect: QRectF):
        z = self.ed.zoom
        pen = QPen(QColor("white"), 1.5 / z, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(255, 255, 255, 25))
        p.drawRect(rect)

    def _knob(self, p, x, y):
        z = self.ed.zoom
        p.setPen(QPen(QColor(ACCENT), 2 / z))
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(x, y), 5 / z, 5 / z)

    # events ------------------------------------------------------------
    def mousePressEvent(self, ev):
        self.setFocus()
        self.ed.on_press(ev)

    def mouseMoveEvent(self, ev):
        x, y = self.to_doc(ev.position())
        self.cursorMoved.emit(x, y)
        self.ed.on_motion(ev)

    def mouseReleaseEvent(self, ev):
        self.ed.on_release(ev)

    def mouseDoubleClickEvent(self, ev):
        self.ed.on_double_click(ev)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.ed.set_zoom(self.ed.zoom * (1.15 if ev.angleDelta().y() > 0 else 1 / 1.15))
            ev.accept()
        else:
            ev.ignore()

    def contextMenuEvent(self, ev):
        self.ed.context_menu(ev)


# ====================================================================== floating panels
class ColorButton(QToolButton):
    colorChanged = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.color = "#e53935"
        self.setToolTip("Colour")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self)
        grid = QWidget()
        lay = QHBoxLayout(grid)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        for c in SWATCHES:
            b = QToolButton()
            b.setFixedSize(26, 26)
            b.setStyleSheet(f"QToolButton {{ background: {c}; border-radius: 13px; border: 2px solid rgba(255,255,255,0.25); }}"
                            f"QToolButton:hover {{ border-color: white; }}")
            b.clicked.connect(lambda _c=False, col=c: (self.set_color(col, emit=True), menu.close()))
            lay.addWidget(b)
        more = QToolButton()
        more.setText("…")
        more.setToolTip("Other colour")
        more.clicked.connect(lambda: (menu.close(), self._pick()))
        lay.addWidget(more)
        from PyQt6.QtWidgets import QWidgetAction

        act = QWidgetAction(menu)
        act.setDefaultWidget(grid)
        menu.addAction(act)
        self.setMenu(menu)
        self._paint()

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Colour")
        if c.isValid():
            self.set_color(c.name(), emit=True)

    def set_color(self, color: str, emit=False):
        self.color = color
        self._paint()
        if emit:
            self.colorChanged.emit(color)

    def _paint(self):
        self.setStyleSheet("QToolButton { background: transparent; border-radius: 8px; padding: 4px; }"
                           "QToolButton:hover { background: #383D43; }")
        from PyQt6.QtGui import QIcon, QPixmap

        pm = QPixmap(40, 40)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setPen(QPen(QColor(255, 255, 255, 120), 3))
        q.setBrush(QColor(self.color))
        q.drawEllipse(QRectF(4, 4, 32, 32))
        q.end()
        pm.setDevicePixelRatio(2)
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(20, 20))




class DragHandle(QToolButton):
    """Drag this into another app to drop the edited image there."""

    def __init__(self, editor: Editor):
        super().__init__()
        self.ed = editor
        self.setIcon(editor.theme.icon("drag", BAR_ICON, 20))
        self.setText(" Drag")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setToolTip("Drag into a file manager, browser or chat to share the edited image")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._start = None

    def mousePressEvent(self, ev):
        self._start = ev.position()

    def mouseMoveEvent(self, ev):
        if self._start is None or (ev.position() - self._start).manhattanLength() < 8:
            return
        self._start = None
        self.ed._flush_save()
        drag = QDrag(self)
        drag.setMimeData(ui.drag_mime(self.ed.library, [self.ed.capture_id], self.ed.config, with_ids=False))
        thumb = render.scale_to_fit(self.ed.render_output(), 160, 120)
        from PyQt6.QtGui import QPixmap

        drag.setPixmap(QPixmap.fromImage(thumb))
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, ev):
        self._start = None
