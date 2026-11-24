"""Library building blocks: capture list model, thumbnail delegate, sidebar and tag completer."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QAbstractListModel, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractItemView, QCompleter, QListWidget, QStyle, QStyledItemDelegate

from . import render, ui
from .theme import ACCENT, draw_brackets

if TYPE_CHECKING:
    from .library_window import LibraryWindow

ID_ROLE = Qt.ItemDataRole.UserRole + 1
SORT_OPTIONS = [("date_desc", "Newest first"), ("date_asc", "Oldest first"), ("name", "Name"),
                ("size", "File size"), ("dims", "Dimensions"), ("app", "Application")]
DATE_FILTERS = [("any", "Any time"), ("today", "Today"), ("7d", "Last 7 days"), ("30d", "Last 30 days"),
                ("year", "This year")]
MODE_LABELS = {"region": "Region", "window": "Window", "full": "Full screen", "monitor": "Monitor",
               "active": "Active window", "last_region": "Repeat region", "import": "Imported",
               "clipboard": "Pasted", "recovered": "Recovered"}
THUMB = 200


def date_group(ts: float) -> str:
    lt = time.localtime()
    today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    if ts >= today:
        return "Today"
    if ts >= today - 86400:
        return "Yesterday"
    if ts >= today - 6 * 86400:
        return "This week"
    return time.strftime("%B %Y", time.localtime(ts))


# ====================================================================== grid model / delegate
class CaptureModel(QAbstractListModel):
    def __init__(self, window: "LibraryWindow"):
        super().__init__()
        self.win = window
        self.caps = []
        self.thumbs: dict[str, QImage] = {}
        self._queue: list[str] = []
        self._timer = QTimer(self, interval=0)
        self._timer.timeout.connect(self._load_some)

    def set_captures(self, caps):
        self.beginResetModel()
        self.caps = caps
        self.endResetModel()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.caps)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        cap = self.caps[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return cap.name
        if role == ID_ROLE:
            return cap.id
        if role == Qt.ItemDataRole.DecorationRole:
            img = self.thumbs.get(cap.id)
            if img is None and cap.id not in self._queue:
                self._queue.append(cap.id)
                self._timer.start()
            return img
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{cap.name}\n{cap.width} × {cap.height}   {ui.human_date(cap.created)}"
        return None

    def flags(self, index):
        return super().flags(index) | Qt.ItemFlag.ItemIsDragEnabled

    def mimeTypes(self):
        return ["text/uri-list", ui.IDS_MIME]

    def mimeData(self, indexes):
        ids = [self.caps[i.row()].id for i in indexes]
        return ui.drag_mime(self.win.library, ids, self.win.config)

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction

    def invalidate(self, cid):
        self.thumbs.pop(cid, None)

    def _load_some(self):
        deadline = time.monotonic() + 0.02
        while self._queue and time.monotonic() < deadline:
            cid = self._queue.pop(0)
            try:
                self.thumbs[cid] = render.render_thumbnail(self.win.library, cid, THUMB)
            except Exception as exc:
                print(f"catcap: thumbnail failed for {cid}: {exc}")
                self.thumbs[cid] = QImage()
            for row, cap in enumerate(self.caps):
                if cap.id == cid:
                    idx = self.index(row)
                    self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
                    break
        if not self._queue:
            self._timer.stop()


class ThumbDelegate(QStyledItemDelegate):
    """Thumbnail with rounded corners; selection is a mint viewfinder bracket, not a filled card."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window

    def sizeHint(self, option, index):
        return QSize(THUMB + 28, THUMB * 3 // 4 + 60)

    def paint(self, p: QPainter, option, index):
        c = self.win.theme.c
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        cell = QRectF(option.rect).adjusted(10, 10, -10, -6)
        area = QRectF(cell.left(), cell.top(), cell.width(), THUMB * 3 / 4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if hovered and not selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["hover"]))
            p.drawRoundedRect(cell.adjusted(-6, -6, 6, 4), 12, 12)
        img = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(img, QImage) and not img.isNull():
            size = img.size().scaled(area.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(area.center().x() - size.width() / 2, area.bottom() - size.height(),
                            size.width(), size.height())
            path = QPainterPath()
            path.addRoundedRect(target, 6, 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 24))
            p.drawRoundedRect(target.adjusted(-1, 1, 1, 4), 7, 7)
            p.setClipPath(path)
            p.drawImage(target, img)
            p.setClipping(False)
            p.setPen(QPen(QColor(c["line"]), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(target, 6, 6)
            frame = target
        else:
            frame = area.adjusted(14, 14, -14, -4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["raised"]))
            p.drawRoundedRect(frame, 6, 6)
        if selected:
            draw_brackets(p, frame.adjusted(-6, -6, 6, 6), QColor(ACCENT), 18, 3.5)
        cap = self.win.model.caps[index.row()]
        text_rect = QRectF(cell.left(), area.bottom() + 9, cell.width(), 20)
        font = QFont(option.font)
        font.setWeight(font.Weight.DemiBold if selected else font.Weight.Normal)
        p.setFont(font)
        p.setPen(QColor(c["ink"]))
        name = p.fontMetrics().elidedText(("★ " if cap.favourite else "") + cap.name, Qt.TextElideMode.ElideRight,
                                          int(cell.width()))
        p.drawText(text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), name)
        font.setWeight(font.Weight.Normal)
        font.setPointSizeF(font.pointSizeF() * 0.88)
        p.setFont(font)
        p.setPen(QColor(c["muted"]))
        sub = f"{cap.width} × {cap.height}" + (f"   #{'  #'.join(cap.tags[:2])}" if cap.tags else "")
        p.drawText(text_rect.translated(0, 19), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                   p.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, int(cell.width())))
        p.restore()


class Sidebar(QListWidget):
    """Library sections; captures can be dropped onto collections, favourites, tags and trash."""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.setObjectName("sidebar")
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setIconSize(QSize(18, 18))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _key_at(self, ev):
        item = self.itemAt(ev.position().toPoint())
        return item.data(ID_ROLE) if item else None

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(ui.IDS_MIME):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        key = self._key_at(ev)
        if key and key[0] in ("collection", "favourites", "trash", "tag"):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        key = self._key_at(ev)
        ids = [i for i in bytes(ev.mimeData().data(ui.IDS_MIME)).decode().split("\n") if i]
        if key and ids:
            self.win.drop_on(key, ids)
            ev.acceptProposedAction()


class TagCompleter(QCompleter):
    """Completes the last comma-separated tag."""

    def splitPath(self, path):
        return [path.split(",")[-1].strip()]

    def pathFromIndex(self, index):
        current = self.widget().text().split(",")[:-1]
        return ", ".join([p.strip() for p in current if p.strip()] + [index.data()]) + ", "
