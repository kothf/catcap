"""Small windows: pin-to-screen, post-capture quick bar, delay countdown, shortcuts help, welcome."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
                             QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from . import render, ui
from .theme import ACCENT, draw_brackets


class PinWindow(QWidget):
    """Always-on-top borderless image (OUT-08). Scroll to zoom, drag to move, Esc or double-click to close."""

    def __init__(self, app, image):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("CatCap pin")
        self.image = image
        geo = self.screen().availableGeometry()
        self.scale = min(1.0, geo.width() * 0.6 / image.width(), geo.height() * 0.6 / image.height())
        self._drag = None
        self.setToolTip("Drag to move. Scroll to zoom. Double-click or Esc to close.")
        self._resize()

    def _resize(self):
        self.resize(int(self.image.width() * self.scale) + 6, int(self.image.height() * self.scale) + 6)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(3, 3, self.width() - 6, self.height() - 6)
        p.setPen(QPen(QColor(ACCENT), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawImage(r, self.image)
        p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 3, 3)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _ev):
        self._drag = None

    def mouseDoubleClickEvent(self, _ev):
        self.close()

    def wheelEvent(self, ev):
        self.scale = max(0.05, min(8.0, self.scale * (1.1 if ev.angleDelta().y() > 0 else 1 / 1.1)))
        self._resize()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.close()

    def contextMenuEvent(self, ev):
        menu = QMenu(self)
        menu.addAction("Copy", lambda: ui.copy_image(self.image))
        menu.addAction("Actual size", lambda: (setattr(self, "scale", 1.0), self._resize()))
        opacity = menu.addMenu("Opacity")
        for v in (100, 75, 50):
            opacity.addAction(f"{v}%", lambda v=v: self.setWindowOpacity(v / 100))
        menu.addSeparator()
        menu.addAction("Close", self.close)
        menu.exec(ev.globalPos())


class QuickBar(QWidget):
    """Post-capture toolbar (OVL-08): Copy, Edit, Pin, Library, Delete. Fades out after a few seconds."""

    TIMEOUT_MS = 8000

    def __init__(self, app, capture_id: str):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.app = app
        self.capture_id = capture_id
        frame = QFrame(self)
        frame.setObjectName("quickbar")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(8)
        cap = app.library.get(capture_id)
        thumb = QLabel()
        pm = QPixmap.fromImage(render.render_thumbnail(app.library, capture_id, 200).scaledToHeight(
            112, Qt.TransformationMode.SmoothTransformation))
        pm.setDevicePixelRatio(2)
        thumb.setPixmap(pm)
        row.addWidget(thumb)
        text = QLabel(f"<b>Saved to library</b><br><span style='color:#8E959C'>{cap.width} × {cap.height}</span>")
        row.addWidget(text)
        row.addSpacing(6)
        for label, icon, cb, accent in (("Copy", "copy", self._copy, True), ("Edit", "edit", self._edit, False),
                                        ("Pin", "pin", self._pin, False), ("Library", "images", self._library, False)):
            b = QPushButton(" " + label)
            b.setIcon(app.theme.icon(icon, "#FFFFFF", 16))
            if accent:
                b.setProperty("accent", True)
            b.clicked.connect(cb)
            row.addWidget(b)
        delete = QToolButton()
        delete.setIcon(app.theme.icon("trash", "#E9ECEA", 18))
        delete.setToolTip("Delete this capture")
        delete.setStyleSheet("QToolButton { background: transparent; } QToolButton:hover { background: #3D4248; }")
        delete.clicked.connect(self._delete)
        row.addWidget(delete)
        close = QToolButton()
        close.setIcon(app.theme.icon("close", "#8E959C", 16))
        close.setToolTip("Close")
        close.setStyleSheet(delete.styleSheet())
        close.clicked.connect(self.close)
        row.addWidget(close)
        self.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)
        self._timer = QTimer(self, singleShot=True, interval=self.TIMEOUT_MS)
        self._timer.timeout.connect(self._expire)
        self._timer.start()
        self.show()

    def enterEvent(self, _ev):
        self._timer.stop()

    def leaveEvent(self, _ev):
        self._timer.start(3000)

    def _expire(self):
        if not self.underMouse():
            self.close()

    def _copy(self):
        ui.copy_capture(self.app.library, self.capture_id)
        self.close()

    def _edit(self):
        self.close()
        self.app.open_editor(self.capture_id)

    def _pin(self):
        self.app.pin_image(render.render_capture(self.app.library, self.capture_id))
        self.close()

    def _library(self):
        self.close()
        self.app.show_library(self.capture_id)

    def _delete(self):
        self.app.library.delete_permanently([self.capture_id])
        self.close()


class Countdown(QWidget):
    """Visible delay countdown (OPT-01), drawn as a bracketed number."""

    def __init__(self, seconds: int, on_done, on_cancel=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.remaining = seconds
        self.on_done, self.on_cancel = on_done, on_cancel
        self.resize(150, 150)
        area = QGuiApplication.primaryScreen().geometry()
        self.move(area.center() - QPoint(75, 75))
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.show()
        self.activateWindow()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(10, 10, 130, 130)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(30, 33, 37, 225))
        p.drawRoundedRect(r, 28, 28)
        draw_brackets(p, r.adjusted(14, 14, -14, -14), QColor(ACCENT), 20, 5)
        font = QFont()
        font.setPixelSize(58)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("white"))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), str(self.remaining))

    def _tick(self):
        self.remaining -= 1
        if self.remaining <= 0:
            self._timer.stop()
            self.close()
            QTimer.singleShot(250, self.on_done)
        else:
            self.update()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self._timer.stop()
            self.close()
            if self.on_cancel:
                self.on_cancel()


SHORTCUTS = [
    ("Capture overlay", [
        ("Drag", "Select a region"), ("Click", "Capture the window under the pointer"),
        ("Arrow keys", "Move the pointer or selection by 1 px (Shift: 10 px)"),
        ("Ctrl + arrows", "Resize the selection"), ("Enter, double-click", "Capture"),
        ("F, Ctrl+A", "Select the whole screen"), ("C", "Copy the colour under the pointer"),
        ("M", "Show or hide the magnifier"), ("Esc, right-click", "Cancel"),
        ("Tool column", "Draw on the selection: pen, arrow, shapes, text, steps, blur, redact"),
        ("Mouse wheel (drawing)", "Thickness"), ("Ctrl+Z, Ctrl+Shift+Z", "Undo, redo a drawing"),
        ("Esc (drawing)", "Leave the drawing tool")]),
    ("Editor", [
        ("V A L R E T O H P S B X K G C", "Select, arrow, line, rectangle, ellipse, text, callout, highlighter, "
                                          "pen, step, blur, pixelate, redact, spotlight, crop"),
        ("Ctrl+Z, Ctrl+Shift+Z", "Undo, redo"), ("Delete", "Delete the selected annotation"),
        ("Ctrl+D", "Duplicate"), ("Ctrl+], Ctrl+[", "Bring to front, send to back"),
        ("Shift while drawing", "45° lines, squares, circles"), ("Ctrl+C", "Copy the image"),
        ("Ctrl+S", "Export"), ("Ctrl+scroll, Ctrl+0, Ctrl+1", "Zoom, fit, actual size"),
        ("Middle-drag, Alt+drag", "Pan"), ("Enter, Esc (crop)", "Apply or cancel the crop")]),
    ("Library", [
        ("Ctrl+F", "Search"), ("Ctrl+N", "New region capture"), ("Enter, double-click", "Edit"),
        ("Ctrl+C", "Copy the image"), ("Ctrl+V", "New capture from the clipboard"), ("Ctrl+E", "Export"),
        ("Delete, Shift+Delete", "Move to trash, delete forever"), ("F2", "Rename")]),
]


def show_shortcuts(parent=None):
    dlg = QDialog(parent)
    dlg.setWindowTitle("Keyboard shortcuts")
    dlg.resize(680, 600)
    lay = QVBoxLayout(dlg)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    body = QWidget()
    grid = QGridLayout(body)
    grid.setHorizontalSpacing(24)
    grid.setVerticalSpacing(8)
    row = 0
    for section, items in SHORTCUTS:
        head = QLabel(section)
        head.setObjectName("title")
        head.setContentsMargins(0, 12 if row else 0, 0, 4)
        grid.addWidget(head, row, 0, 1, 2)
        row += 1
        for keys, desc in items:
            k = QLabel(keys)
            k.setStyleSheet("font-weight: 600;")
            d = QLabel(desc)
            d.setWordWrap(True)
            grid.addWidget(k, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(d, row, 1)
            row += 1
    grid.setRowStretch(row, 1)
    scroll.setWidget(body)
    lay.addWidget(scroll)
    close = QPushButton("Close")
    close.clicked.connect(dlg.close)
    lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
    dlg.show()
    return dlg


class WelcomeDialog(QDialog):
    def __init__(self, app, cinnamon: bool, wayland: bool):
        super().__init__(None)
        self.setWindowTitle("Welcome to CatCap")
        self.setWindowIcon(app.app_icon)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(14)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app.app_icon.pixmap(56, 56))
        head.addWidget(logo)
        title = QLabel("Welcome to CatCap")
        title.setObjectName("empty-title")
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)
        intro = QLabel("Three quick choices. You can change any of them later in Settings.")
        intro.setObjectName("muted")
        lay.addWidget(intro)
        self.print_check = QCheckBox("Use Print Screen for CatCap (replaces Cinnamon's screenshot keys)"
                                     if cinnamon else "Turn on global hotkeys")
        self.print_check.setChecked(True)
        self.login_check = QCheckBox("Start CatCap when I log in, in the panel tray")
        self.login_check.setChecked(True)
        lay.addWidget(self.print_check)
        lay.addWidget(self.login_check)
        row = QHBoxLayout()
        row.addWidget(QLabel("After a capture"))
        self.after = QComboBox()
        from .settings_window import AFTER_ACTIONS

        for key, label in AFTER_ACTIONS:
            self.after.addItem(label, key)
        self.after.setCurrentIndex(max(0, self.after.findData(app.config.get("capture", "after_action"))))
        row.addWidget(self.after, 1)
        lay.addLayout(row)
        if wayland:
            note = QLabel("You're in a Wayland session. Screenshots go through the desktop's screenshot portal, "
                          "which may ask for permission the first time.")
            note.setObjectName("muted")
            note.setWordWrap(True)
            lay.addWidget(note)
        start = QPushButton("Start using CatCap")
        start.setProperty("accent", True)
        start.clicked.connect(self.accept)
        lay.addWidget(start, 0, Qt.AlignmentFlag.AlignRight)
