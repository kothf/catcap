"""Library window (BRW-*, ORG-*, SRC-*, IMP-*)."""

from __future__ import annotations

import time

from PyQt6.QtCore import (QAbstractListModel, QItemSelectionModel, QModelIndex, QRectF, QSize, QStringListModel, Qt,
                          QTimer)
from PyQt6.QtGui import (QColor, QFont, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
                         QShortcut, QStandardItem, QStandardItemModel)
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileDialog, QFormLayout, QFrame,
                             QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow,
                             QMenu, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QStyle,
                             QStyledItemDelegate, QToolButton, QTreeView, QVBoxLayout, QWidget, QWidgetAction)

from . import render, ui
from .library import Query
from .theme import ACCENT, draw_brackets

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

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.caps)

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


# ====================================================================== window
class LibraryWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.theme = app.theme
        self.library = app.library
        self.config = app.config
        self.query = Query(sort=self.config.get("library", "sort"))
        self.captures = []
        self._preview_id = None
        self._loading = False
        self.setWindowTitle("CatCap")
        self.setWindowIcon(app.app_icon)
        self.resize(1280, 820)
        self.setAcceptDrops(True)
        self._refresh_timer = QTimer(self, singleShot=True, interval=120)
        self._refresh_timer.timeout.connect(self._deferred_refresh)
        self._build()
        self.library.connect(self._on_library_event)
        self.destroyed.connect(lambda *_: self.library.disconnect(self._on_library_event))
        self.refresh_sidebar()
        self.refresh()

    # ------------------------------------------------------------ build
    def icon(self, name, color=None, size=18):
        return self.theme.icon(name, color, size)

    def _tool(self, icon, tip, cb=None):
        b = QToolButton()
        b.setProperty("flat", True)
        b.setIcon(self.icon(icon, size=20))
        b.setIconSize(QSize(20, 20))
        b.setToolTip(tip)
        if cb:
            b.clicked.connect(lambda _c=False: cb())
        return b

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(236)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(14, 16, 6, 12)
        rl.setSpacing(12)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(self.app.app_icon.pixmap(30, 30))
        name = QLabel("CatCap")
        name.setObjectName("title")
        brand.addWidget(logo)
        brand.addWidget(name)
        brand.addStretch(1)
        rl.addLayout(brand)
        capture = QPushButton("  New capture")
        capture.setProperty("accent", True)
        capture.setIcon(self.icon("capture", "#FFFFFF", 18))
        capture.setMenu(self._capture_menu())
        capture.setToolTip("Take a screenshot (Ctrl+N for a region)")
        rl.addWidget(capture)
        self.sidebar = Sidebar(self)
        self.sidebar.currentItemChanged.connect(self._on_sidebar)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._sidebar_menu)
        rl.addWidget(self.sidebar, 1)
        outer.addWidget(rail)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        top = QFrame()
        top.setObjectName("topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 12, 12, 12)
        tl.setSpacing(6)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search captures")
        self.search.setToolTip("Searches names, tags, apps, window titles and notes")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(self.icon("search", self.theme.c["muted"]), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._on_search)
        tl.addWidget(self.search, 1)
        tl.addSpacing(6)
        self.filter_btn = self._tool("filter", "Filters")
        self.filter_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.filter_btn.setMenu(self._filter_menu())
        tl.addWidget(self.filter_btn)
        self.sort_combo = QComboBox()
        for key, label in SORT_OPTIONS:
            self.sort_combo.addItem(label, key)
        self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(self.query.sort)))
        self.sort_combo.currentIndexChanged.connect(self._on_sort)
        tl.addWidget(self.sort_combo)
        self.grid_btn = self._tool("grid", "Grid", lambda: self.set_view("grid"))
        self.list_btn = self._tool("list", "List", lambda: self.set_view("list"))
        for b in (self.grid_btn, self.list_btn):
            b.setCheckable(True)
            tl.addWidget(b)
        tl.addWidget(self._tool("import", "Import images (or drop files here, or paste with Ctrl+V)",
                                self.import_dialog))
        menu_btn = self._tool("menu", "Menu")
        menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_btn.setMenu(self._main_menu())
        tl.addWidget(menu_btn)
        cl.addWidget(top)
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.info.setContentsMargins(20, 12, 18, 4)
        cl.addWidget(self.info)

        self.stack = QStackedWidget()
        self.model = CaptureModel(self)
        self.grid = QListView()
        self.grid.setObjectName("grid")
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(4)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setDragEnabled(True)
        self.grid.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.grid.setMouseTracking(True)
        self.grid.setModel(self.model)
        self.grid.setItemDelegate(ThumbDelegate(self))
        self.grid.setViewportMargins(10, 0, 10, 10)
        self.grid.doubleClicked.connect(lambda idx: self.open_editor(idx.data(ID_ROLE)))
        self.grid.selectionModel().selectionChanged.connect(lambda *_: self._update_details())
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(lambda pos: self._item_menu(self.grid.viewport().mapToGlobal(pos)))
        self.stack.addWidget(self.grid)

        self.list_model = QStandardItemModel()
        self.tree = QTreeView()
        self.tree.setModel(self.list_model)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.doubleClicked.connect(self._tree_open)
        self.tree.selectionModel().selectionChanged.connect(lambda *_: self._update_details())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(lambda pos: self._item_menu(self.tree.viewport().mapToGlobal(pos)))
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self._build_empty())
        cl.addWidget(self.stack, 1)

        splitter = QSplitter()
        splitter.addWidget(center)
        splitter.addWidget(self._build_details())
        splitter.setStretchFactor(0, 1)
        splitter.setCollapsible(0, False)
        splitter.setSizes([900, 340])
        splitter.setHandleWidth(1)
        outer.addWidget(splitter, 1)
        self.set_view(self.config.get("library", "view"))
        for seq, cb in (("Ctrl+F", self.search.setFocus), ("Ctrl+N", lambda: self.app.start_capture("region", hide=self)),
                        ("Ctrl+V", self.paste), ("Ctrl+C", self.copy_selected), ("Ctrl+E", self.export_selected),
                        ("Delete", self.delete_selected), ("Shift+Delete", lambda: self.delete_selected(True)),
                        ("F2", self.name_edit.setFocus), ("Return", self._open_current),
                        ("Ctrl+?", lambda: self.app.show_shortcuts(self))):
            QShortcut(QKeySequence(seq), self).activated.connect(cb)

    def _capture_menu(self):
        menu = QMenu(self)
        for mode, label, icon in (("region", "Region or window", "capture"), ("window", "Window", "window"),
                                  ("full", "Full screen", "screen"), ("monitor", "Current monitor", "screen"),
                                  ("active", "Active window", "window"), ("last_region", "Repeat last region", "repeat")):
            act = menu.addAction(self.icon(icon), label)
            act.triggered.connect(lambda _c=False, m=mode: self.app.start_capture(m, hide=self))
        menu.addSeparator()
        delayed = menu.addAction(self.icon("timer"), "Region after 5 seconds")
        delayed.triggered.connect(lambda: self.app.start_capture("region", delay=5, hide=self))
        menu.addAction(self.icon("copy"), "New from clipboard", self.paste)
        return menu

    def _main_menu(self):
        menu = QMenu(self)
        menu.addAction(self.icon("settings"), "Settings", lambda: self.app.show_settings(self))
        menu.addAction("Keyboard shortcuts", lambda: self.app.show_shortcuts(self))
        menu.addSeparator()
        menu.addAction(self.icon("trash"), "Empty trash…", self.empty_trash)
        menu.addAction("Rebuild library from disk", self.rebuild)
        menu.addAction(self.icon("folder"), "Open image folder", lambda: ui.show_in_folder(self.library.images_dir / "."))
        menu.addSeparator()
        menu.addAction("About CatCap", lambda: self.app.show_about(self))
        menu.addAction("Quit CatCap", self.app.quit)
        return menu

    def _filter_menu(self):
        menu = QMenu(self)
        box = QWidget()
        form = QFormLayout(box)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(10)
        self.date_combo = QComboBox()
        for key, label in DATE_FILTERS:
            self.date_combo.addItem(label, key)
        self.app_combo = QComboBox()
        self.mode_combo = QComboBox()
        self.annotated = QCheckBox("Only edited captures")
        reset = QPushButton("Reset filters")
        form.addRow("Date", self.date_combo)
        form.addRow("Application", self.app_combo)
        form.addRow("Type", self.mode_combo)
        form.addRow(self.annotated)
        form.addRow(reset)
        act = QWidgetAction(menu)
        act.setDefaultWidget(box)
        menu.addAction(act)
        menu.aboutToShow.connect(self._fill_filters)
        for combo in (self.date_combo, self.app_combo, self.mode_combo):
            combo.currentIndexChanged.connect(self._on_filters)
        self.annotated.toggled.connect(self._on_filters)
        reset.clicked.connect(self._reset_filters)
        self._fill_filters()
        return menu

    def _fill_filters(self):
        self._loading = True
        for combo, items, current in (
                (self.app_combo, [("", "Any application")] + [(a, a) for a in self.library.apps()], self.query.app or ""),
                (self.mode_combo, [("", "Any type")] + [(m, MODE_LABELS.get(m, m)) for m in self.library.modes()],
                 self.query.mode or "")):
            combo.clear()
            for key, label in items:
                combo.addItem(label, key)
            combo.setCurrentIndex(max(0, combo.findData(current)))
        self._loading = False

    def _build_empty(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        lay.addStretch(1)
        art = QLabel()
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setPixmap(self._empty_art())
        self.empty_title = QLabel()
        self.empty_title.setObjectName("empty-title")
        self.empty_text = QLabel()
        self.empty_text.setObjectName("muted")
        self.empty_text.setWordWrap(True)
        for lbl in (self.empty_title, self.empty_text):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_button = QPushButton("  Take a screenshot")
        self.empty_button.setProperty("accent", True)
        self.empty_button.setIcon(self.icon("capture", "#FFFFFF"))
        self.empty_button.clicked.connect(lambda: self.app.start_capture("region", hide=self))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.empty_button)
        row.addStretch(1)
        for item in (art, self.empty_title, self.empty_text):
            lay.addWidget(item)
        lay.addLayout(row)
        lay.addStretch(2)
        return w

    def _empty_art(self):
        pm = QPixmap(320, 220)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_brackets(p, QRectF(50, 20, 220, 170), QColor(ACCENT), 36, 7)
        p.drawPixmap(105, 50, self.app.app_icon.pixmap(110, 110))
        p.end()
        pm.setDevicePixelRatio(2)
        return pm

    def _build_details(self):
        pane = QFrame()
        pane.setObjectName("pane")
        pane.setMinimumWidth(300)
        self.details_stack = QStackedWidget()
        lay = QVBoxLayout(pane)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.details_stack)

        single = QWidget()
        sl = QVBoxLayout(single)
        sl.setContentsMargins(20, 20, 20, 20)
        sl.setSpacing(12)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(190)
        self.preview.setToolTip("Double-click to edit")
        self.preview.mouseDoubleClickEvent = lambda _e: self._preview_id and self.open_editor(self._preview_id)
        sl.addWidget(self.preview)
        name_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setToolTip("Name (F2)")
        self.name_edit.editingFinished.connect(self._save_name)
        self.fav_btn = self._tool("star", "Favourite")
        self.fav_btn.setCheckable(True)
        self.fav_btn.toggled.connect(self._on_fav)
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(self.fav_btn)
        sl.addLayout(name_row)

        self.actions_row = QWidget()
        ar = QHBoxLayout(self.actions_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.setSpacing(6)
        edit = QPushButton("  Edit")
        edit.setProperty("accent", True)
        edit.setIcon(self.icon("edit", "#FFFFFF"))
        edit.clicked.connect(lambda: self._preview_id and self.open_editor(self._preview_id))
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self.copy_selected)
        export = QPushButton("Export…")
        export.clicked.connect(self.export_selected)
        more = self._tool("more", "More")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setMenu(self._single_menu())
        for w in (edit, copy_btn, export):
            ar.addWidget(w, 1)
        ar.addWidget(more)
        sl.addWidget(self.actions_row)

        self.trash_row = QWidget()
        tr = QHBoxLayout(self.trash_row)
        tr.setContentsMargins(0, 0, 0, 0)
        restore = QPushButton("Restore")
        restore.setProperty("accent", True)
        restore.clicked.connect(lambda: self.library.restore(self.selected_ids()))
        purge = QPushButton("Delete forever")
        purge.setProperty("danger", True)
        purge.clicked.connect(lambda: self.delete_selected(True))
        tr.addWidget(restore, 1)
        tr.addWidget(purge, 1)
        sl.addWidget(self.trash_row)

        self.meta = QFormLayout()
        self.meta.setHorizontalSpacing(14)
        self.meta.setVerticalSpacing(6)
        sl.addLayout(self.meta)
        sl.addSpacing(4)
        sl.addWidget(QLabel("Tags"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("docs, bug, invoice")
        self.tag_model = QStringListModel(self)
        self.tag_completer = TagCompleter(self.tag_model, self.tags_edit)
        self.tag_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.tags_edit.setCompleter(self.tag_completer)
        self.tags_edit.editingFinished.connect(self._save_tags)
        sl.addWidget(self.tags_edit)
        self.coll_btn = QPushButton()
        self.coll_btn.setIcon(self.icon("folder"))
        self.coll_btn.setStyleSheet("text-align: left;")
        self.coll_menu = QMenu(self)
        self.coll_menu.aboutToShow.connect(lambda: self._fill_collections(self.coll_menu))
        self.coll_btn.setMenu(self.coll_menu)
        sl.addWidget(self.coll_btn)
        sl.addWidget(QLabel("Notes"))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Anything worth remembering about this capture")
        self.notes.setFixedHeight(90)
        self.notes.focusOutEvent = self._notes_focus_out
        sl.addWidget(self.notes)
        sl.addStretch(1)
        self.details_stack.addWidget(single)

        multi = QWidget()
        ml = QVBoxLayout(multi)
        ml.setContentsMargins(20, 20, 20, 20)
        ml.setSpacing(8)
        self.multi_label = QLabel()
        self.multi_label.setObjectName("title")
        ml.addWidget(self.multi_label)
        self.multi_normal = QWidget()
        mn = QVBoxLayout(self.multi_normal)
        mn.setContentsMargins(0, 6, 0, 0)
        for label, icon, cb in (("Export to folder…", "export", self.export_selected),
                                ("Export as one PDF…", "export",
                                 lambda: ui.export_pdf_dialog(self, self.library, self.config, self.selected_ids())),
                                ("Add tag…", "tag", self.tag_selected),
                                ("Mark as favourite", "star", lambda: self.library.set_favourite(self.selected_ids(), True)),
                                ("Move to trash", "trash", self.delete_selected)):
            b = QPushButton("  " + label)
            b.setIcon(self.icon(icon))
            b.setStyleSheet("text-align: left;")
            b.clicked.connect(lambda _c=False, f=cb: f())
            mn.addWidget(b)
        add_coll = QPushButton("  Add to collection")
        add_coll.setIcon(self.icon("folder"))
        add_coll.setStyleSheet("text-align: left;")
        menu = QMenu(self)
        menu.aboutToShow.connect(lambda: self._fill_collections(menu, multi=True))
        add_coll.setMenu(menu)
        mn.addWidget(add_coll)
        ml.addWidget(self.multi_normal)
        self.multi_trash = QWidget()
        mt = QVBoxLayout(self.multi_trash)
        mt.setContentsMargins(0, 6, 0, 0)
        r = QPushButton("Restore")
        r.setProperty("accent", True)
        r.clicked.connect(lambda: self.library.restore(self.selected_ids()))
        d = QPushButton("Delete forever")
        d.setProperty("danger", True)
        d.clicked.connect(lambda: self.delete_selected(True))
        mt.addWidget(r)
        mt.addWidget(d)
        ml.addWidget(self.multi_trash)
        ml.addStretch(1)
        self.details_stack.addWidget(multi)

        none = QLabel("Select a capture to see its details.\n\nDrag captures onto a collection in the sidebar to file them.")
        none.setObjectName("muted")
        none.setAlignment(Qt.AlignmentFlag.AlignCenter)
        none.setWordWrap(True)
        none.setContentsMargins(24, 24, 24, 24)
        self.details_stack.addWidget(none)
        return pane

    def _single_menu(self):
        menu = QMenu(self)
        menu.addAction(self.icon("pin"), "Pin to screen",
                       lambda: self._first(lambda c: self.app.pin_image(render.render_capture(self.library, c))))
        menu.addAction("Export as PDF…", lambda: ui.export_pdf_dialog(self, self.library, self.config, self.selected_ids()))
        menu.addMenu(ui.open_with_menu(menu, lambda: ui.temp_render(self.library, self.selected_ids()[0], self.config)))
        menu.addAction("Send by email…",
                       lambda: self._first(lambda c: ui.send_email(ui.temp_render(self.library, c, self.config))))
        menu.addAction(self.icon("folder"), "Show original in file manager",
                       lambda: self._first(lambda c: ui.show_in_folder(self.library.original_path(c))))
        menu.addSeparator()
        menu.addAction(self.icon("trash"), "Move to trash", self.delete_selected)
        return menu

    # ------------------------------------------------------------ sidebar
    def _section(self, text, add_cb=None):
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = item.font()
        font.setWeight(font.Weight.DemiBold)
        font.setPointSizeF(font.pointSizeF() * 0.92)
        item.setFont(font)
        item.setForeground(QColor(self.theme.c["muted"]))
        item.setSizeHint(QSize(0, 38))
        self.sidebar.addItem(item)
        if add_cb:
            btn = self._tool("plus", "New collection", add_cb)
            btn.setIconSize(QSize(16, 16))
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 4, 0)
            hl.addStretch(1)
            hl.addWidget(btn)
            self.sidebar.setItemWidget(item, holder)

    def _entry(self, key, label, icon, count=None):
        text = f"{label}    {count}" if count else label
        item = QListWidgetItem(self.icon(icon, self.theme.c["muted"]), text)
        item.setData(ID_ROLE, key)
        self.sidebar.addItem(item)
        return item

    def refresh_sidebar(self):
        current = self.sidebar.currentItem().data(ID_ROLE) if self.sidebar.currentItem() else ("all",)
        self._loading = True
        self.sidebar.clear()
        self._entry(("all",), "All captures", "images", self.library.count())
        self._entry(("favourites",), "Favourites", "star")
        self._entry(("recent",), "Last 7 days", "clock")
        self._entry(("trash",), "Trash", "trash")
        self._section("Collections", self.new_collection)
        for cid, name, count in self.library.collections():
            self._entry(("collection", cid), name, "folder", count)
        tags = self.library.all_tags()
        if tags:
            self._section("Tags")
            for tag, count in tags:
                self._entry(("tag", tag), tag, "tag", count)
        for i in range(self.sidebar.count()):
            if self.sidebar.item(i).data(ID_ROLE) == current:
                self.sidebar.setCurrentRow(i)
                break
        else:
            self.sidebar.setCurrentRow(0)
        self._loading = False

    def _on_sidebar(self, item, _prev):
        if self._loading or item is None or not item.data(ID_ROLE):
            return
        key = item.data(ID_ROLE)
        self.query.view = key[0]
        self.query.collection_id = key[1] if key[0] == "collection" else None
        self.query.tag = key[1] if key[0] == "tag" else None
        self.refresh()

    def _sidebar_menu(self, pos):
        item = self.sidebar.itemAt(pos)
        key = item.data(ID_ROLE) if item else None
        menu = QMenu(self)
        if key and key[0] == "collection":
            menu.addAction("Rename…", lambda: self._rename_collection(key[1]))
            menu.addAction("Delete collection", lambda: self._delete_collection(key[1]))
        elif key and key[0] == "trash":
            menu.addAction("Empty trash…", self.empty_trash)
        else:
            return
        menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    def drop_on(self, key, ids):
        if key[0] == "collection":
            self.library.add_to_collection(ids, key[1])
        elif key[0] == "favourites":
            self.library.set_favourite(ids, True)
        elif key[0] == "trash":
            self.library.trash(ids)
        elif key[0] == "tag":
            self.library.add_tag(ids, key[1])

    # ------------------------------------------------------------ data
    def _on_library_event(self, kind, ids):
        if kind in ("edited", "deleted"):
            for cid in ids:
                self.model.invalidate(cid)
        self._refresh_timer.start()

    def _deferred_refresh(self):
        self.refresh_sidebar()
        self.refresh()

    def refresh(self):
        selected = set(self.selected_ids())
        self.captures = self.library.query(self.query)
        self.model.set_captures(self.captures)
        self._fill_list()
        self._restore_selection(selected)
        self._update_info()
        self._show_view()
        self._update_details()

    def _fill_list(self):
        m = self.list_model
        m.clear()
        m.setHorizontalHeaderLabels(["Name", "Date", "Dimensions", "Size", "Application", "Type"])
        grouped = self.query.sort in ("date_desc", "date_asc")
        group, parent = None, m.invisibleRootItem()
        for cap in self.captures:
            if grouped and date_group(cap.created) != group:
                group = date_group(cap.created)
                head = QStandardItem(group)
                font = head.font()
                font.setWeight(font.Weight.DemiBold)
                head.setFont(font)
                row = [head] + [QStandardItem("") for _ in range(5)]
                for it in row:
                    it.setSelectable(False)
                m.appendRow(row)
                parent = head
            cells = [("★ " if cap.favourite else "") + cap.name, ui.human_date(cap.created),
                     f"{cap.width} × {cap.height}", ui.human_size(cap.size), cap.app,
                     MODE_LABELS.get(cap.mode, cap.mode)]
            items = [QStandardItem(c) for c in cells]
            for it in items:
                it.setData(cap.id, ID_ROLE)
            parent.appendRow(items)
        self.tree.setRootIsDecorated(grouped)
        self.tree.expandAll()
        self.tree.setColumnWidth(0, 360)

    def _restore_selection(self, ids):
        if not ids:
            return
        sel = self.grid.selectionModel()
        for row, cap in enumerate(self.captures):
            if cap.id in ids:
                sel.select(self.model.index(row), QItemSelectionModel.SelectionFlag.Select)

    def _update_info(self):
        n = len(self.captures)
        names = {"all": "All captures", "favourites": "Favourites", "recent": "Last 7 days", "trash": "Trash",
                 "collection": "Collection", "tag": f"Tagged “{self.query.tag}”"}
        text = f"{names.get(self.query.view, '')}, {n} capture{'s' if n != 1 else ''}"
        filters = self._active_filters()
        if filters:
            text += ", filtered by " + ", ".join(filters)
        if self.query.view == "trash" and n:
            text += f". Items older than {self.config.get('library', 'trash_days')} days are removed automatically."
        self.info.setText(text)

    def _active_filters(self):
        out = []
        if self.query.search:
            out.append(f"“{self.query.search}”")
        if self.query.date_from:
            out.append(self.date_combo.currentText().lower())
        if self.query.app:
            out.append(self.query.app)
        if self.query.mode:
            out.append(MODE_LABELS.get(self.query.mode, self.query.mode).lower())
        if self.query.annotated_only:
            out.append("edited only")
        return out

    def _show_view(self):
        if self.captures:
            self.stack.setCurrentIndex(1 if self.config.get("library", "view") == "list" else 0)
            return
        if self.query.view == "trash":
            title, text, button = "Trash is empty", "Deleted captures wait here until you empty the trash.", False
        elif self._active_filters():
            title, text, button = "Nothing matches", "Try a different search, or reset the filters.", False
        elif self.library.count() == 0:
            title = "No captures yet"
            text = "Press Print Screen once hotkeys are on, use New capture, or drop images here."
            button = True
        else:
            title, text, button = "Nothing here yet", "Drag captures onto this item in the sidebar.", False
        self.empty_title.setText(title)
        self.empty_text.setText(text)
        self.empty_button.setVisible(button)
        self.stack.setCurrentIndex(2)

    def set_view(self, view):
        self.config.set("library", "view", view)
        self.grid_btn.setChecked(view != "list")
        self.list_btn.setChecked(view == "list")
        self._show_view()
        self._update_details()

    # ------------------------------------------------------------ selection
    def selected_ids(self) -> list[str]:
        if self.config.get("library", "view") == "list":
            ids = [i.data(ID_ROLE) for i in self.tree.selectionModel().selectedRows(0)]
            return [i for i in ids if i]
        rows = sorted(i.row() for i in self.grid.selectionModel().selectedIndexes())
        return [self.captures[r].id for r in rows if r < len(self.captures)]

    def _first(self, fn):
        ids = self.selected_ids()
        if ids:
            fn(ids[0])

    def select_capture(self, capture_id):
        for row, cap in enumerate(self.captures):
            if cap.id == capture_id:
                idx = self.model.index(row)
                self.grid.selectionModel().select(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                self.grid.scrollTo(idx)
                return

    def _tree_open(self, idx):
        cid = idx.data(ID_ROLE)
        if cid:
            self.open_editor(cid)

    def _open_current(self):
        if self.search.hasFocus() or self.name_edit.hasFocus() or self.tags_edit.hasFocus():
            return
        ids = self.selected_ids()
        if ids:
            self.open_editor(ids[0])

    # ------------------------------------------------------------ details pane
    def _update_details(self):
        ids = self.selected_ids()
        trash = self.query.view == "trash"
        if not ids:
            self.details_stack.setCurrentIndex(2)
            self._preview_id = None
            return
        if len(ids) > 1:
            self.multi_label.setText(f"{len(ids)} captures selected")
            self.multi_normal.setVisible(not trash)
            self.multi_trash.setVisible(trash)
            self.details_stack.setCurrentIndex(1)
            self._preview_id = None
            return
        cap = next((c for c in self.captures if c.id == ids[0]), None)
        if cap is None:
            return
        self._loading = True
        self.details_stack.setCurrentIndex(0)
        if self._preview_id != cap.id:
            try:
                img = render.scale_to_fit(render.render_capture(self.library, cap.id), 600, 380)
                pm = QPixmap.fromImage(img)
                pm.setDevicePixelRatio(2)
                self.preview.setPixmap(pm)
            except Exception:
                self.preview.clear()
        self._preview_id = cap.id
        self.name_edit.setText(cap.name)
        self.fav_btn.setChecked(cap.favourite)
        self.fav_btn.setIcon(self.icon("star-filled" if cap.favourite else "star",
                                       "#F2B705" if cap.favourite else None, 20))
        while self.meta.rowCount():
            self.meta.removeRow(0)
        rows = [("Created", ui.human_date(cap.created)),
                ("Size", f"{cap.width} × {cap.height} px, {ui.human_size(cap.size)}"),
                ("Type", MODE_LABELS.get(cap.mode, cap.mode) + (", edited" if cap.has_annotations else ""))]
        if cap.app:
            rows.append(("App", cap.app))
        if cap.title:
            rows.append(("Window", cap.title))
        if cap.trashed:
            rows.append(("Deleted", ui.human_date(cap.trashed)))
        for k, v in rows:
            key = QLabel(k)
            key.setObjectName("muted")
            val = QLabel(v)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.meta.addRow(key, val)
        self.tags_edit.setText(", ".join(cap.tags))
        self.tag_model.setStringList([t for t, _n in self.library.all_tags()])
        self.notes.setPlainText(cap.notes)
        names = [n for k, n, _c in self.library.collections() if k in self.library.capture_collections(cap.id)]
        self.coll_btn.setText("  " + (", ".join(names) if names else "Add to a collection"))
        self.actions_row.setVisible(not trash)
        self.trash_row.setVisible(trash)
        for w in (self.name_edit, self.tags_edit, self.notes, self.coll_btn, self.fav_btn):
            w.setEnabled(not trash)
        self._loading = False

    def _current_cap(self):
        return next((c for c in self.captures if c.id == self._preview_id), None)

    def _save_name(self):
        cap = self._current_cap()
        name = self.name_edit.text().strip()
        if cap and name and name != cap.name and not self._loading:
            self.library.update(cap.id, name=name)

    def _save_tags(self):
        cap = self._current_cap()
        if cap is None or self._loading:
            return
        tags = [t for t in self.tags_edit.text().split(",") if t.strip()]
        if sorted(self.library.normalize_tag(t) for t in tags) != sorted(cap.tags):
            self.library.set_tags(cap.id, tags)

    def _notes_focus_out(self, ev):
        QPlainTextEdit.focusOutEvent(self.notes, ev)
        cap = self._current_cap()
        if cap and not self._loading and self.notes.toPlainText() != cap.notes:
            self.library.update(cap.id, notes=self.notes.toPlainText())

    def _on_fav(self, value):
        if self._preview_id and not self._loading:
            self.library.set_favourite([self._preview_id], value)

    def _fill_collections(self, menu, multi=False):
        menu.clear()
        ids = self.selected_ids()
        member = self.library.capture_collections(ids[0]) if ids and not multi else set()
        for cid, name, _n in self.library.collections():
            act = menu.addAction(self.icon("folder"), name)
            if multi:
                act.triggered.connect(lambda _c=False, k=cid: self.library.add_to_collection(self.selected_ids(), k))
            else:
                act.setCheckable(True)
                act.setChecked(cid in member)
                act.toggled.connect(lambda on, k=cid: (self.library.add_to_collection if on else
                                                       self.library.remove_from_collection)(self.selected_ids(), k))
        if menu.actions():
            menu.addSeparator()
        menu.addAction(self.icon("plus"), "New collection…", lambda: self.new_collection(add_selected=True))

    # ------------------------------------------------------------ actions
    def open_editor(self, capture_id):
        if capture_id and self.query.view != "trash":
            self.app.open_editor(capture_id)

    def copy_selected(self):
        ids = self.selected_ids()
        if ids:
            ui.copy_capture(self.library, ids[0])
            self.info.setText("Copied to the clipboard")

    def export_selected(self):
        ui.export_dialog(self, self.library, self.config, self.selected_ids())

    def tag_selected(self):
        ids = self.selected_ids()
        tag = ui.prompt_text(self, "Add tag", f"Tag for {len(ids)} captures:") if ids else None
        if tag:
            self.library.add_tag(ids, tag)

    def delete_selected(self, permanent=False):
        ids = self.selected_ids()
        if not ids:
            return
        if permanent or self.query.view == "trash":
            n = len(ids)
            if ui.ask(self, f"Delete {n} capture{'s' if n > 1 else ''} forever?",
                      "The original image and its edits are removed from disk. This can't be undone.",
                      "Delete forever", destructive=True):
                self.library.delete_permanently(ids)
        else:
            self.library.trash(ids)
            self.info.setText(f"Moved {len(ids)} to Trash. Open Trash to restore.")

    def empty_trash(self):
        if ui.ask(self, "Empty the trash?", "Everything in the trash is deleted forever.", "Empty trash", True):
            self.library.empty_trash()

    def rebuild(self):
        added = self.library.rebuild()
        self.info.setText(f"Library rebuilt: {added} capture(s) recovered from disk.")

    def new_collection(self, add_selected=False):
        name = ui.prompt_text(self, "New collection", "Name:")
        if not name or not name.strip():
            return
        try:
            cid = self.library.create_collection(name)
        except Exception as exc:
            ui.error(self, "Couldn't create the collection", str(exc))
            return
        if add_selected and self.selected_ids():
            self.library.add_to_collection(self.selected_ids(), cid)

    def _rename_collection(self, collection_id):
        current = dict((k, n) for k, n, _c in self.library.collections()).get(collection_id, "")
        name = ui.prompt_text(self, "Rename collection", "Name:", current)
        if name and name.strip():
            self.library.rename_collection(collection_id, name)

    def _delete_collection(self, collection_id):
        if ui.ask(self, "Delete this collection?", "The captures in it stay in your library.", "Delete", True):
            self.library.delete_collection(collection_id)

    def import_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Import images", "",
                                                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff)")
        if files:
            self.import_paths(files)

    def import_paths(self, paths):
        done, failed = 0, []
        for p in paths:
            try:
                self.library.import_file(p)
                done += 1
            except Exception as exc:
                failed.append(f"{p}: {exc}")
        self.info.setText(f"Imported {done} image{'s' if done != 1 else ''}."
                          + (f" {len(failed)} failed." if failed else ""))
        if failed:
            ui.error(self, "Some files couldn't be imported", "\n".join(failed[:10]))

    def paste(self):
        clip = QGuiApplication.clipboard()
        mime = clip.mimeData()
        if mime is not None and mime.hasImage():
            cid = self.library.add_capture(QImage(clip.image()), mode="clipboard",
                                           name="Pasted " + time.strftime("%Y-%m-%d %H:%M"))
            QTimer.singleShot(250, lambda: self.select_capture(cid))
        elif mime is not None and mime.hasUrls():
            self.import_paths([u.toLocalFile() for u in mime.urls()
                               if u.isLocalFile() and ui.is_image_path(u.toLocalFile())])
        else:
            self.info.setText("The clipboard has no image to paste.")

    def _item_menu(self, global_pos):
        ids = self.selected_ids()
        if not ids:
            return
        menu = QMenu(self)
        if self.query.view == "trash":
            menu.addAction("Restore", lambda: self.library.restore(ids))
            menu.addAction("Delete forever", lambda: self.delete_selected(True))
        else:
            if len(ids) == 1:
                menu.addAction(self.icon("edit"), "Edit", lambda: self.open_editor(ids[0]))
                menu.addAction(self.icon("copy"), "Copy", self.copy_selected)
                menu.addAction(self.icon("pin"), "Pin to screen",
                               lambda: self.app.pin_image(render.render_capture(self.library, ids[0])))
            menu.addAction(self.icon("export"), "Export…", self.export_selected)
            menu.addAction(self.icon("tag"), "Add tag…", self.tag_selected)
            menu.addAction(self.icon("star"), "Favourite", lambda: self.library.set_favourite(ids, True))
            if self.query.view == "collection":
                menu.addAction("Remove from this collection",
                               lambda: self.library.remove_from_collection(ids, self.query.collection_id))
            menu.addSeparator()
            menu.addAction(self.icon("trash"), "Move to trash", self.delete_selected)
        menu.exec(global_pos)

    # ------------------------------------------------------------ search / filters
    def _on_search(self, text):
        self.query.search = text.strip()
        self.refresh()

    def _on_sort(self, _i):
        self.query.sort = self.sort_combo.currentData()
        self.config.set("library", "sort", self.query.sort)
        self.refresh()

    def _on_filters(self, *_):
        if self._loading:
            return
        key = self.date_combo.currentData()
        now = time.time()
        lt = time.localtime()
        today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        self.query.date_from = {"today": today, "7d": now - 7 * 86400, "30d": now - 30 * 86400,
                                "year": time.mktime((lt.tm_year, 1, 1, 0, 0, 0, 0, 0, -1))}.get(key)
        self.query.app = self.app_combo.currentData() or None
        self.query.mode = self.mode_combo.currentData() or None
        self.query.annotated_only = self.annotated.isChecked()
        self.refresh()

    def _reset_filters(self):
        self._loading = True
        for combo in (self.date_combo, self.app_combo, self.mode_combo):
            combo.setCurrentIndex(0)
        self.annotated.setChecked(False)
        self._loading = False
        self._on_filters()

    # ------------------------------------------------------------ external drops
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls() and ev.source() is None:
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        files = [u.toLocalFile() for u in ev.mimeData().urls()
                 if u.isLocalFile() and ui.is_image_path(u.toLocalFile())]
        if files:
            self.import_paths(files)
