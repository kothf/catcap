"""Editor window (EDT-*, ANN-*, IMG-*): graphite canvas, floating tool dock, contextual settings."""

from __future__ import annotations

import copy
import json
import math

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QImage, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFontDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                             QPushButton, QScrollArea, QSlider, QSpinBox, QToolButton, QVBoxLayout,
                             QWidget)

from . import annotations as ann
from . import render, ui
from .annotations import Document
from .editor_tools import (ASPECTS, BAR_ICON, DEFAULT_STYLES, GROUP_BREAKS, HINTS, PROPS_FOR,  # noqa: F401
                           SWATCHES, TOOLS)
from .editor_widgets import Canvas, ColorButton, DragHandle  # noqa: F401
from .theme import CANVAS




# ====================================================================== window
class Editor(QMainWindow):
    def __init__(self, app, capture_id: str):
        super().__init__()
        self.app = app
        self.theme = app.theme
        self.library = app.library
        self.config = app.config
        self.capture_id = capture_id
        self.cap = self.library.get(capture_id)
        self.src = render.load_original(self.library.original_path(capture_id))
        self.doc: Document = self.library.load_doc(capture_id)
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self.tool = "select"
        self.selected = None
        self.active = None
        self.drag = None
        self.crop_draft = None
        self.aspect = None
        self.zoom = 1.0
        self.fit_mode = True
        self.base = None
        self.rendered = None
        self._loading_props = False
        self._pan = None
        self._save_timer = QTimer(self, singleShot=True, interval=500)
        self._save_timer.timeout.connect(self._save_now)
        self.styles = copy.deepcopy(DEFAULT_STYLES)
        try:
            for k, v in json.loads(self.config.get("editor", "styles") or "{}").items():
                if k in self.styles and isinstance(v, dict):
                    self.styles[k].update(v)
        except (ValueError, TypeError):
            pass
        self.setWindowIcon(app.app_icon)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        screen = self.screen().availableGeometry()
        self.resize(min(1320, int(screen.width() * 0.85)), min(880, int(screen.height() * 0.85)))
        self._build()
        self._rebuild_base()
        self.set_tool("select")
        self._update_title()
        self.library.connect(self._on_library_event)
        self.destroyed.connect(lambda *_: self.library.disconnect(self._on_library_event))

    # ------------------------------------------------------------ layout
    def _icon_btn(self, icon, tip, cb, color=BAR_ICON, checkable=False):
        b = QToolButton()
        b.setIcon(self.theme.icon(icon, color, 20))
        b.setIconSize(QSize(20, 20))
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setAutoRaise(True)
        if cb:
            b.clicked.connect(lambda _c=False: cb())
        return b

    def _build(self):
        root = QWidget()
        root.setObjectName("editor-root")
        self.setCentralWidget(root)
        col = QVBoxLayout(root)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("editor-bar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(4)
        self.undo_btn = self._icon_btn("undo", "Undo (Ctrl+Z)", self.undo)
        self.redo_btn = self._icon_btn("redo", "Redo (Ctrl+Shift+Z)", self.redo)
        row.addWidget(self.undo_btn)
        row.addWidget(self.redo_btn)
        row.addSpacing(10)
        self.title = QLabel()
        self.title.setObjectName("title")
        row.addWidget(self.title)
        row.addStretch(1)
        drag = DragHandle(self)
        row.addWidget(drag)
        for icon, tip, cb in (("pin", "Pin to screen", lambda: self.app.pin_image(self.render_output())),
                              ("export", "Export… (Ctrl+S)", self.export)):
            row.addWidget(self._icon_btn(icon, tip, cb))
        more = self._icon_btn("more", "More", None)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setMenu(self._build_menu())
        row.addWidget(more)
        row.addWidget(self._icon_btn("grid", "Library", lambda: self.app.show_library(self.capture_id)))
        row.addSpacing(6)
        copy_btn = QPushButton(" Copy")
        copy_btn.setProperty("accent", True)
        copy_btn.setIcon(self.theme.icon("copy", "#FFFFFF", 18))
        copy_btn.setToolTip("Copy image (Ctrl+C)")
        copy_btn.clicked.connect(self.copy)
        row.addWidget(copy_btn)
        col.addWidget(bar)

        stage = QWidget()
        stage_lay = QVBoxLayout(stage)
        stage_lay.setContentsMargins(0, 0, 0, 0)
        self.canvas = Canvas(self)
        self.canvas.cursorMoved.connect(lambda x, y: self._update_status((x, y)))
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet(f"QScrollArea {{ background: {CANVAS}; }}")
        stage_lay.addWidget(self.scroll)
        col.addWidget(stage, 1)

        self.dock = self._build_dock(stage)
        self.props = self._build_props(stage)
        stage.installEventFilter(self)
        self._stage = stage

        status = QFrame()
        status.setObjectName("statusline")
        srow = QHBoxLayout(status)
        srow.setContentsMargins(14, 4, 10, 6)
        self.status = QLabel()
        srow.addWidget(self.status, 1)
        for icon, tip, cb in (("zoom-out", "Zoom out (Ctrl+-)", lambda: self.set_zoom(self.zoom / 1.25)),):
            srow.addWidget(self._icon_btn(icon, tip, cb, "#C9CDD0"))
        self.zoom_btn = QToolButton()
        self.zoom_btn.setToolTip("Actual size (Ctrl+1)")
        self.zoom_btn.clicked.connect(lambda: self.set_zoom(1.0))
        srow.addWidget(self.zoom_btn)
        srow.addWidget(self._icon_btn("zoom-in", "Zoom in (Ctrl++)", lambda: self.set_zoom(self.zoom * 1.25), "#C9CDD0"))
        srow.addWidget(self._icon_btn("fit", "Fit to window (Ctrl+0)", self.zoom_fit, "#C9CDD0"))
        status.setStyleSheet(f"QFrame#statusline {{ background: {CANVAS}; }}")
        col.addWidget(status)
        self._shortcuts()

    def _build_dock(self, parent):
        dock = QFrame(parent)
        dock.setObjectName("dock")
        row = QHBoxLayout(dock)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(2)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_buttons = {}
        for name, label, key in TOOLS:
            b = QToolButton()
            b.setCheckable(True)
            b.setIcon(self.theme.icon(name, BAR_ICON, 22))
            b.setIconSize(QSize(22, 22))
            b.setToolTip(f"{label}  ({key})")
            b.clicked.connect(lambda _c=False, n=name: self.set_tool(n))
            self.tool_group.addButton(b)
            self.tool_buttons[name] = b
            row.addWidget(b)
            if name in GROUP_BREAKS:
                sep = QFrame()
                sep.setFixedSize(1, 22)
                sep.setStyleSheet("background: #41464C;")
                row.addSpacing(4)
                row.addWidget(sep)
                row.addSpacing(4)
        dock.adjustSize()
        return dock

    def _build_props(self, parent):
        props = QFrame(parent)
        props.setObjectName("props")
        row = QHBoxLayout(props)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)
        self.prop_widgets = {}

        def add(key, widget, label=None):
            box = QWidget()
            lay = QHBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            if label:
                lay.addWidget(QLabel(label))
            lay.addWidget(widget)
            row.addWidget(box)
            self.prop_widgets[key] = box
            return widget

        self.tool_label = QLabel()
        self.tool_label.setStyleSheet("font-weight: 600;")
        row.addWidget(self.tool_label)
        self.color_btn = add("color", ColorButton())
        self.color_btn.colorChanged.connect(lambda c: self._prop_changed({"color": c}))
        self.width_spin = add("width", QSpinBox(), "Width")
        self.width_spin.setRange(1, 60)
        self.width_spin.valueChanged.connect(lambda v: self._prop_changed({"width": v}))
        self.size_spin = add("size", QSpinBox(), "Size")
        self.size_spin.setRange(12, 160)
        self.size_spin.setSingleStep(2)
        self.size_spin.valueChanged.connect(lambda v: self._prop_changed({"size": v}))
        self.style_combo = add("style", QComboBox())
        self.style_combo.addItem("1, 2, 3", "number")
        self.style_combo.addItem("A, B, C", "letter")
        self.style_combo.currentIndexChanged.connect(lambda _i: self._prop_changed({"style": self.style_combo.currentData()}))
        self.font_btn = add("font", QToolButton())
        self.font_btn.clicked.connect(self._pick_font)
        self.strength_spin = add("strength", QSpinBox(), "Strength")
        self.strength_spin.setRange(2, 60)
        self.strength_spin.valueChanged.connect(lambda v: self._prop_changed({"strength": v}))
        self.fill_check = add("fill", QCheckBox("Fill"))
        self.fill_check.toggled.connect(lambda v: self._prop_changed({"fill": v}))
        self.bg_check = add("bg", QCheckBox("Background"))
        self.bg_check.toggled.connect(lambda v: self._prop_changed({"bg": v}))
        self.dashed_check = add("dashed", QCheckBox("Dashed"))
        self.dashed_check.toggled.connect(lambda v: self._prop_changed({"dashed": v}))
        self.shadow_check = add("shadow", QCheckBox("Shadow"))
        self.shadow_check.toggled.connect(lambda v: self._prop_changed({"shadow": v}))
        self.opacity = add("opacity", QSlider(Qt.Orientation.Horizontal), "Opacity")
        self.opacity.setRange(10, 100)
        self.opacity.setFixedWidth(90)
        self.opacity.valueChanged.connect(lambda v: self._prop_changed({"opacity": v / 100}))

        crop = QWidget()
        cl = QHBoxLayout(crop)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self.aspect_combo = QComboBox()
        for label, _v in ASPECTS:
            self.aspect_combo.addItem(label)
        self.aspect_combo.currentIndexChanged.connect(self._on_aspect)
        apply_btn = QPushButton("Apply crop")
        apply_btn.setProperty("accent", True)
        apply_btn.clicked.connect(self.apply_crop)
        reset_btn = QToolButton()
        reset_btn.setText("Remove crop")
        reset_btn.clicked.connect(self.reset_crop)
        cl.addWidget(QLabel("Aspect"))
        cl.addWidget(self.aspect_combo)
        cl.addWidget(apply_btn)
        cl.addWidget(reset_btn)
        row.addWidget(crop)
        self.prop_widgets["aspect"] = crop

        self.image_ops = QWidget()
        ol = QHBoxLayout(self.image_ops)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(0)
        for icon, tip, op in (("rotate-left", "Rotate left", "rotate_ccw"), ("rotate-right", "Rotate right", "rotate_cw"),
                              ("flip-h", "Flip horizontally", "flip_h"), ("flip-v", "Flip vertically", "flip_v")):
            ol.addWidget(self._icon_btn(icon, tip, lambda o=op: self.image_op(o)))
        ol.addWidget(self._icon_btn("resize", "Resize image…", self.resize_dialog))
        row.addWidget(self.image_ops)
        self.hint = QLabel()
        self.hint.setStyleSheet("color: #8E959C;")
        row.addWidget(self.hint)
        return props

    def eventFilter(self, obj, ev):
        if obj is self._stage and ev.type() == ev.Type.Resize:
            self._place_panels()
        return False

    def _place_panels(self):
        w, h = self._stage.width(), self._stage.height()
        self.dock.adjustSize()
        dw, dh = self.dock.sizeHint().width(), self.dock.sizeHint().height()
        self.dock.setGeometry((w - dw) // 2, h - dh - 18, dw, dh)
        self.props.adjustSize()
        pw, ph = min(self.props.sizeHint().width(), w - 24), self.props.sizeHint().height()
        self.props.setGeometry((w - pw) // 2, h - dh - 18 - ph - 8, pw, ph)
        self.dock.raise_()
        self.props.raise_()

    def _build_menu(self):
        menu = QMenu(self)
        for label, cb in (("Rotate left", lambda: self.image_op("rotate_ccw")),
                          ("Rotate right", lambda: self.image_op("rotate_cw")),
                          ("Flip horizontally", lambda: self.image_op("flip_h")),
                          ("Flip vertically", lambda: self.image_op("flip_v")),
                          ("Resize image…", self.resize_dialog), (None, None),
                          ("Export as PDF…", lambda: (self._flush_save(),
                                                       ui.export_pdf_dialog(self, self.library, self.config,
                                                                            [self.capture_id]))),
                          ("Send by email…", lambda: ui.send_email(self._temp())),
                          ("Show original in file manager",
                           lambda: ui.show_in_folder(self.library.original_path(self.capture_id))),
                          (None, None),
                          ("Revert to original", self.revert),
                          ("Keyboard shortcuts", lambda: self.app.show_shortcuts(self))):
            if label is None:
                menu.addSeparator()
            else:
                menu.addAction(label, cb)
        menu.insertMenu(menu.actions()[7], ui.open_with_menu(menu, self._temp))
        return menu

    def _temp(self):
        self._flush_save()
        return ui.temp_render(self.library, self.capture_id, self.config)

    def _shortcuts(self):
        bind = [
            (QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo),
            ("Ctrl+Shift+Z", self.redo), (QKeySequence.StandardKey.Copy, self.copy), ("Ctrl+S", self.export),
            ("Ctrl+D", self.duplicate), ("Ctrl+0", self.zoom_fit), ("Ctrl+1", lambda: self.set_zoom(1.0)),
            ("Ctrl++", lambda: self.set_zoom(self.zoom * 1.25)), ("Ctrl+=", lambda: self.set_zoom(self.zoom * 1.25)),
            ("Ctrl+-", lambda: self.set_zoom(self.zoom / 1.25)), ("Ctrl+]", lambda: self.reorder(1)),
            ("Ctrl+[", lambda: self.reorder(-1)), ("Ctrl+W", self.close), ("Ctrl+?", lambda: self.app.show_shortcuts(self)),
            ("Delete", self.delete_selected), ("Backspace", self.delete_selected), ("Escape", self._escape),
            ("Return", self._enter), ("Enter", self._enter),
        ]
        for name, _label, key in TOOLS:
            bind.append((key, lambda n=name: self.set_tool(n)))
        for seq, cb in bind:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(cb)
        for key, dx, dy in ((Qt.Key.Key_Left, -1, 0), (Qt.Key.Key_Right, 1, 0), (Qt.Key.Key_Up, 0, -1), (Qt.Key.Key_Down, 0, 1)):
            for mod, step in ((Qt.KeyboardModifier.NoModifier, 1), (Qt.KeyboardModifier.ShiftModifier, 10)):
                sc = QShortcut(QKeySequence(mod.value | key.value), self)
                sc.activated.connect(lambda dx=dx, dy=dy, s=step: self.nudge(dx * s, dy * s))

    # ------------------------------------------------------------ tools and properties
    def set_tool(self, name):
        if self.tool == "crop" and name != "crop":
            self.crop_draft = None
        self.tool = name
        self.tool_buttons[name].setChecked(True)
        if name == "crop":
            w, h = self.doc.transformed_size(self.src.width(), self.src.height())
            self.crop_draft = list(self.doc.crop) if self.doc.crop else [0, 0, w, h]
            self.selected = None
            self.fit_mode = True
            self._update_canvas_size()
            QTimer.singleShot(0, self.zoom_fit)
        elif name != "select":
            self.selected = None
        self.tool_label.setText({t: label for t, label, _k in TOOLS}[name])
        self.hint.setText(HINTS.get(name, ""))
        self._load_props(self.styles.get(name, {}))
        self._sync_props()
        cursor = {"select": Qt.CursorShape.ArrowCursor, "text": Qt.CursorShape.IBeamCursor}.get(name, Qt.CursorShape.CrossCursor)
        self.canvas.setCursor(cursor)
        self.canvas.update()

    def _sync_props(self):
        kind = self.selected["type"] if (self.tool == "select" and self.selected) else self.tool
        wanted = PROPS_FOR.get(kind, [])
        for key, box in self.prop_widgets.items():
            box.setVisible(key in wanted)
        self.image_ops.setVisible(kind == "select" or self.tool == "crop")
        self.hint.setVisible(not wanted or kind in ("select", "crop"))
        if self.tool == "select" and self.selected:
            self.tool_label.setText({t: label for t, label, _k in TOOLS}.get(kind, kind))
        QTimer.singleShot(0, self._place_panels)

    def _load_props(self, style: dict):
        self._loading_props = True
        try:
            if "color" in style:
                self.color_btn.set_color(style["color"])
            if "width" in style:
                self.width_spin.setValue(int(round(style["width"])))
            if "size" in style:
                self.size_spin.setValue(int(round(style["size"])))
            if "style" in style:
                self.style_combo.setCurrentIndex(1 if style["style"] == "letter" else 0)
            if "font" in style:
                self.font_btn.setText(f"{style['font']} · {int(style.get('font_size', 22))}px")
            if "strength" in style:
                self.strength_spin.setValue(int(round(style["strength"])))
            self.fill_check.setChecked(bool(style.get("fill")))
            self.bg_check.setChecked(bool(style.get("bg")))
            self.dashed_check.setChecked(bool(style.get("dashed")))
            self.shadow_check.setChecked(bool(style.get("shadow")))
            self.opacity.setValue(int(100 * style.get("opacity", 1.0)))
        finally:
            self._loading_props = False

    def _pick_font(self):
        target = self.selected if (self.tool == "select" and self.selected) else self.styles.get(self.tool, {})
        font = render.item_font(target)
        font.setPointSizeF(max(6.0, target.get("font_size", 22) * 0.75))
        font, ok = QFontDialog.getFont(font, self, "Font")
        if ok:
            family = font.family() + (" Bold" if font.bold() else "") + (" Italic" if font.italic() else "")
            size = font.pointSizeF() / 0.75 if font.pointSizeF() > 0 else font.pixelSize()
            self._prop_changed({"font": family, "font_size": round(size)})
            self.font_btn.setText(f"{family} · {round(size)}px")

    def _prop_changed(self, values: dict):
        if self._loading_props:
            return
        if self.tool == "select" and self.selected is not None:
            self._checkpoint()
            self.selected.update(values)
            if self.selected["type"] in ann.BOX_KINDS:
                render.measure_text(self.selected)
            self._commit()
            kind = self.selected["type"]
        else:
            kind = self.tool
        if kind in self.styles:
            self.styles[kind].update(values)
            self.config.set("editor", "styles", json.dumps(self.styles))

    def _on_aspect(self, index):
        self.aspect = ASPECTS[index][1]
        if self.crop_draft and self.aspect:
            x, y, w, _h = self.crop_draft
            self.crop_draft = self._clamp([x, y, w, w / self.aspect])
        self.canvas.update()

    # ------------------------------------------------------------ document and history
    def _checkpoint(self):
        self.undo_stack.append(self.doc.to_json())
        del self.undo_stack[:-500]
        self.redo_stack.clear()

    def _restore(self, text):
        index = self.doc.items.index(self.selected) if self.selected in self.doc.items else None
        self.doc = Document.from_json(text)
        self.selected = self.doc.items[index] if index is not None and index < len(self.doc.items) else None
        self._rebuild_base()
        self._schedule_save()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.doc.to_json())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.doc.to_json())
            self._restore(self.redo_stack.pop())

    def _commit(self):
        self._render_items()
        self._schedule_save()

    def _schedule_save(self):
        self._save_timer.start()
        self._update_buttons()

    def _save_now(self):
        try:
            self.library.save_doc(self.capture_id, self.doc)
        except Exception as exc:
            self.status.setText(f"Could not save: {exc}")

    def _flush_save(self):
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_now()

    def _rebuild_base(self):
        self.base = render.transformed_image(self.src, self.doc)
        self._render_items()
        self._update_canvas_size()
        if self.fit_mode:
            QTimer.singleShot(0, self.zoom_fit)

    def _render_items(self, exclude=None):
        image = self.base.copy()
        render.draw_items(image, [i for i in self.doc.items if i is not exclude])
        self.rendered = image
        self._update_buttons()
        self._update_status()
        self.canvas.update()

    def _update_buttons(self):
        self.undo_btn.setEnabled(bool(self.undo_stack))
        self.redo_btn.setEnabled(bool(self.redo_stack))

    def _update_title(self):
        self.title.setText(self.cap.name)
        self.setWindowTitle(f"{self.cap.name} – CatCap")

    def _update_status(self, pos=None):
        w, h = self.doc.output_size(self.src.width(), self.src.height())
        parts = [f"{w} × {h} px"]
        n = len(self.doc.items)
        if n:
            parts.append(f"{n} annotation{'s' if n != 1 else ''}")
        if pos:
            parts.append(f"{int(pos[0])}, {int(pos[1])}")
        self.status.setText("     ".join(parts))

    def render_output(self) -> QImage:
        return render.render(self.src, self.doc)

    # ------------------------------------------------------------ view
    def view_rect(self):
        tw, th = self.base.width(), self.base.height()
        if self.doc.crop and self.tool != "crop":
            return tuple(self.doc.crop)
        return 0, 0, tw, th

    def set_zoom(self, z, fit=False):
        self.fit_mode = fit
        self.zoom = max(0.05, min(z, 16.0))
        self.zoom_btn.setText(f"{round(self.zoom * 100)}%")
        self._update_canvas_size()

    def zoom_fit(self):
        _cx, _cy, w, h = self.view_rect()
        vp = self.scroll.viewport().size()
        if vp.width() < 50:
            return
        dock_h = self.dock.height() + self.props.height() + 40
        self.set_zoom(min((vp.width() - 64) / w, (vp.height() - 48 - dock_h) / h, 1.0), fit=True)

    def _update_canvas_size(self):
        self.canvas.setMinimumSize(self.canvas.sizeHint())
        self.canvas.updateGeometry()
        self.canvas.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.fit_mode:
            QTimer.singleShot(0, self.zoom_fit)

    # ------------------------------------------------------------ mouse
    def _hit_handle(self, handles, x, y):
        tol = 9 / self.zoom
        for name, hx, hy in handles:
            if abs(x - hx) <= tol and abs(y - hy) <= tol:
                return name
        return None

    def _item_at(self, x, y):
        for item in reversed(self.doc.items):
            if ann.hit_test(item, x, y, 6 / self.zoom):
                return item
        return None

    def _new_item(self, kind, x, y):
        style = copy.deepcopy(self.styles.get(kind, {}))
        if kind == "callout":
            return {"type": kind, "x": x, "y": y, "text": "…", "tail": [x, y], **style}
        if kind in ann.LINE_KINDS:
            return {"type": kind, "x1": x, "y1": y, "x2": x, "y2": y, **style}
        if kind in ann.RECT_KINDS:
            return {"type": kind, "x": x, "y": y, "w": 0, "h": 0, **style}
        if kind in ann.PATH_KINDS:
            return {"type": kind, "points": [[x, y]], **style}
        return {"type": kind, "x": x, "y": y, **style}

    def on_press(self, ev):
        x, y = self.canvas.to_doc(ev.position())
        btn = ev.button()
        if btn == Qt.MouseButton.MiddleButton or (btn == Qt.MouseButton.LeftButton
                                                   and ev.modifiers() & Qt.KeyboardModifier.AltModifier):
            self._pan = (ev.globalPosition(), self.scroll.horizontalScrollBar().value(),
                         self.scroll.verticalScrollBar().value())
            return
        if btn != Qt.MouseButton.LeftButton:
            return
        tool = self.tool
        if tool == "select":
            if self.selected is not None:
                handle = self._hit_handle(ann.handles(self.selected), x, y)
                if handle:
                    self._checkpoint()
                    self.drag = {"mode": "handle", "name": handle}
                    self._begin_live(self.selected)
                    return
            item = self._item_at(x, y)
            self.selected = item
            self._load_props(item or {})
            self._sync_props()
            if item is not None:
                self._checkpoint()
                self.drag = {"mode": "move", "last": (x, y), "moved": False}
                self._begin_live(item)
            self.canvas.update()
            return
        if tool == "crop":
            handle = self._hit_handle(self._crop_handles(), x, y)
            if handle:
                self.drag = {"mode": "crop-handle", "name": handle}
            elif self.crop_draft and QRectF(*self.crop_draft).contains(QPointF(x, y)):
                self.drag = {"mode": "crop-move", "last": (x, y)}
            else:
                self.drag = {"mode": "crop-new", "start": (x, y)}
                self.crop_draft = [x, y, 0, 0]
            return
        if tool == "text":
            self.add_text(x, y)
            return
        if tool == "step":
            self._checkpoint()
            item = self._new_item("step", x, y)
            item["n"] = self.doc.next_step_number()
            self.doc.items.append(item)
            self.selected = item
            self._commit()
            return
        self.active = self._new_item(tool, x, y)
        self.drag = {"mode": "create", "start": (x, y)}

    def _begin_live(self, item):
        self.active = item
        self._render_items(exclude=item)

    def _crop_handles(self):
        if not self.crop_draft:
            return []
        x, y, w, h = self.crop_draft
        return ann.handles({"type": "rect", "x": x, "y": y, "w": w, "h": h})

    @staticmethod
    def _constrain(sx, sy, x, y, shift, kind):
        if not shift:
            return x, y
        dx, dy = x - sx, y - sy
        if kind in ann.LINE_KINDS:
            ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            length = math.hypot(dx, dy)
            return sx + math.cos(ang) * length, sy + math.sin(ang) * length
        side = max(abs(dx), abs(dy))
        return sx + math.copysign(side, dx or 1), sy + math.copysign(side, dy or 1)

    def _clamp(self, rect, keep_size=False):
        tw, th = self.base.width(), self.base.height()
        x, y, w, h = rect
        if keep_size:
            return [max(0, min(x, tw - w)), max(0, min(y, th - h)), w, h]
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(tw, x + w), min(th, y + h)
        return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]

    def on_motion(self, ev):
        x, y = self.canvas.to_doc(ev.position())
        if self._pan is not None:
            start, hv, vv = self._pan
            d = ev.globalPosition() - start
            self.scroll.horizontalScrollBar().setValue(int(hv - d.x()))
            self.scroll.verticalScrollBar().setValue(int(vv - d.y()))
            return
        if not self.drag:
            if self.tool == "select":
                over = self._item_at(x, y) is not None or (
                    self.selected is not None and self._hit_handle(ann.handles(self.selected), x, y))
                self.canvas.setCursor(Qt.CursorShape.SizeAllCursor if over else Qt.CursorShape.ArrowCursor)
            return
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        mode = self.drag["mode"]
        if mode == "create" and self.active is not None:
            sx, sy = self.drag["start"]
            kind = self.active["type"]
            if kind in ann.LINE_KINDS:
                self.active["x2"], self.active["y2"] = self._constrain(sx, sy, x, y, shift, kind)
            elif kind in ann.RECT_KINDS:
                ex, ey = self._constrain(sx, sy, x, y, shift, kind)
                self.active.update(dict(zip("xywh", ann._norm_rect(sx, sy, ex, ey), strict=True)))
            elif kind in ann.PATH_KINDS:
                last = self.active["points"][-1]
                if math.hypot(x - last[0], y - last[1]) * self.zoom >= 2:
                    self.active["points"].append([x, y])
            elif kind == "callout":
                self.active["x"], self.active["y"] = x, y
        elif mode == "move":
            lx, ly = self.drag["last"]
            ann.move_item(self.selected, x - lx, y - ly)
            self.drag["last"], self.drag["moved"] = (x, y), True
        elif mode == "handle":
            ann.drag_handle(self.selected, self.drag["name"], x, y)
        elif mode == "crop-new":
            sx, sy = self.drag["start"]
            ex, ey = x, y
            if self.aspect:
                ey = sy + math.copysign(abs(ex - sx) / self.aspect, (ey - sy) or 1)
            self.crop_draft = self._clamp(ann._norm_rect(sx, sy, ex, ey))
        elif mode == "crop-move":
            lx, ly = self.drag["last"]
            cx, cy, cw, ch = self.crop_draft
            self.crop_draft = self._clamp([cx + x - lx, cy + y - ly, cw, ch], keep_size=True)
            self.drag["last"] = (x, y)
        elif mode == "crop-handle":
            box = {"type": "rect", "x": self.crop_draft[0], "y": self.crop_draft[1],
                   "w": self.crop_draft[2], "h": self.crop_draft[3]}
            ann.drag_handle(box, self.drag["name"], x, y)
            rect = [box["x"], box["y"], box["w"], box["h"]]
            if self.aspect:
                rect[3] = rect[2] / self.aspect
            self.crop_draft = self._clamp(rect)
        self.canvas.update()

    def on_release(self, ev):
        if self._pan is not None:
            self._pan = None
            return
        if not self.drag:
            return
        mode = self.drag["mode"]
        moved = self.drag.get("moved", True)
        self.drag = None
        if mode == "create" and self.active is not None:
            item, self.active = self.active, None
            if not self._big_enough(item):
                self.canvas.update()
                return
            if item["type"] == "callout":
                self._finish_callout(item)
                return
            self._checkpoint()
            self.doc.items.append(item)
            self.selected = item
            self._commit()
        elif mode in ("move", "handle"):
            item, self.active = self.active, None
            if mode == "move" and not moved and self.undo_stack:
                self.undo_stack.pop()
            if item is not None and item["type"] in ann.BOX_KINDS:
                render.measure_text(item)
            self._commit()

    def on_double_click(self, ev):
        x, y = self.canvas.to_doc(ev.position())
        item = self._item_at(x, y)
        if item is not None and item["type"] in ann.BOX_KINDS:
            self.edit_text(item)
        elif self.tool == "crop":
            self.apply_crop()

    def _big_enough(self, item):
        min_px = 3 / self.zoom
        if item["type"] in ann.LINE_KINDS:
            return math.hypot(item["x2"] - item["x1"], item["y2"] - item["y1"]) >= min_px
        if item["type"] in ann.RECT_KINDS:
            return item["w"] >= min_px and item["h"] >= min_px
        return True

    def context_menu(self, ev):
        x, y = self.canvas.to_doc(QPointF(ev.pos()))
        item = self._item_at(x, y)
        menu = QMenu(self)
        if item is not None:
            self.set_tool("select")
            self.selected = item
            self._sync_props()
            menu.addAction("Duplicate", self.duplicate)
            menu.addAction("Bring to front", lambda: self.reorder(1))
            menu.addAction("Send to back", lambda: self.reorder(-1))
            if item["type"] in ann.BOX_KINDS:
                menu.addAction("Edit text…", lambda: self.edit_text(item))
            menu.addSeparator()
            menu.addAction("Delete", self.delete_selected)
        else:
            menu.addAction("Copy image", self.copy)
            menu.addAction("Export…", self.export)
            menu.addAction("Pin to screen", lambda: self.app.pin_image(self.render_output()))
        self.canvas.update()
        menu.exec(ev.globalPos())

    # ------------------------------------------------------------ keyboard helpers
    def _escape(self):
        if self.tool == "crop":
            self.set_tool("select")
        elif self.active is not None:
            self.active, self.drag = None, None
            self._render_items()
        else:
            self.selected = None
            self._sync_props()
            self.canvas.update()

    def _enter(self):
        if self.tool == "crop":
            self.apply_crop()

    def nudge(self, dx, dy):
        if self.selected is None:
            return
        self._checkpoint()
        ann.move_item(self.selected, dx, dy)
        self._commit()

    # ------------------------------------------------------------ commands
    def add_text(self, x, y):
        text = ui.prompt_text(self, "Add text", "Text:", "", multiline=True)
        if not text:
            return
        self._checkpoint()
        item = self._new_item("text", x, y)
        item["text"] = text
        render.measure_text(item)
        self.doc.items.append(item)
        self.selected = item
        self._commit()

    def _finish_callout(self, item):
        text = ui.prompt_text(self, "Add callout", "Callout text:", "", multiline=True)
        if not text:
            self._render_items()
            return
        item["text"] = text
        render.measure_text(item)
        item["x"] -= item["w"] / 2
        item["y"] -= item["h"] / 2
        self._checkpoint()
        self.doc.items.append(item)
        self.selected = item
        self._commit()

    def edit_text(self, item):
        text = ui.prompt_text(self, "Edit text", "Text:", item.get("text", ""), multiline=True)
        if text is None:
            return
        self._checkpoint()
        if not text:
            self.doc.items.remove(item)
            self.selected = None
        else:
            item["text"] = text
            render.measure_text(item)
        self._commit()

    def delete_selected(self):
        if self.selected is None or self.selected not in self.doc.items:
            return
        self._checkpoint()
        was_step = self.selected["type"] == "step"
        self.doc.items.remove(self.selected)
        self.selected = None
        if was_step:
            self.doc.renumber_steps()
        self._sync_props()
        self._commit()

    def duplicate(self):
        if self.selected is None:
            return
        self._checkpoint()
        item = copy.deepcopy(self.selected)
        ann.move_item(item, 16, 16)
        if item["type"] == "step":
            item["n"] = self.doc.next_step_number()
        self.doc.items.append(item)
        self.selected = item
        self._commit()

    def reorder(self, direction):
        if self.selected is None or self.selected not in self.doc.items:
            return
        self._checkpoint()
        self.doc.items.remove(self.selected)
        if direction > 0:
            self.doc.items.append(self.selected)
        else:
            self.doc.items.insert(0, self.selected)
        self.doc.renumber_steps()
        self._commit()

    def image_op(self, op):
        ow, oh = self.src.width(), self.src.height()
        self._checkpoint()
        index = self.doc.items.index(self.selected) if self.selected in self.doc.items else None
        {"rotate_cw": self.doc.rotate_cw, "rotate_ccw": self.doc.rotate_ccw,
         "flip_h": self.doc.flip_horizontal, "flip_v": self.doc.flip_vertical}[op](ow, oh)
        self.selected = self.doc.items[index] if index is not None else None
        self.crop_draft = None
        if self.tool == "crop":
            self.set_tool("select")
        self.fit_mode = True
        self._rebuild_base()
        self._schedule_save()

    def resize_dialog(self):
        tw, th = self.doc.transformed_size(self.src.width(), self.src.height())
        dlg = QDialog(self)
        dlg.setWindowTitle("Resize image")
        form = QFormLayout(dlg)
        pct, wspin, hspin = QSpinBox(), QSpinBox(), QSpinBox()
        pct.setRange(1, 1600)
        pct.setSuffix(" %")
        pct.setValue(100)
        for s in (wspin, hspin):
            s.setRange(1, 40000)
            s.setSuffix(" px")
        wspin.setValue(tw)
        hspin.setValue(th)
        busy = [False]

        def link(src):
            if busy[0]:
                return
            busy[0] = True
            if src is pct:
                wspin.setValue(round(tw * pct.value() / 100))
                hspin.setValue(round(th * pct.value() / 100))
            elif src is wspin:
                pct.setValue(round(100 * wspin.value() / tw))
                hspin.setValue(round(th * wspin.value() / tw))
            else:
                pct.setValue(round(100 * hspin.value() / th))
                wspin.setValue(round(tw * hspin.value() / th))
            busy[0] = False

        for s in (pct, wspin, hspin):
            s.valueChanged.connect(lambda _v, s=s: link(s))
        form.addRow("Scale", pct)
        form.addRow("Width", wspin)
        form.addRow("Height", hspin)
        note = QLabel("Aspect ratio is kept. Annotations scale with the image.")
        note.setProperty("muted", True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.addButton("Resize", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setProperty("accent", True)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec():
            factor = wspin.value() / tw
            if abs(factor - 1) > 1e-6:
                self._checkpoint()
                self.doc.set_scale(self.doc.scale * factor)
                self.fit_mode = True
                self._rebuild_base()
                self._schedule_save()

    def apply_crop(self):
        if not self.crop_draft or self.crop_draft[2] < 2 or self.crop_draft[3] < 2:
            return
        self._checkpoint()
        tw, th = self.base.width(), self.base.height()
        x, y, w, h = [round(v) for v in self.crop_draft]
        self.doc.crop = None if (x, y, w, h) == (0, 0, tw, th) else [x, y, w, h]
        self.crop_draft = None
        self.set_tool("select")
        self.fit_mode = True
        self._commit()
        self._update_canvas_size()
        QTimer.singleShot(0, self.zoom_fit)

    def reset_crop(self):
        if self.doc.crop:
            self._checkpoint()
            self.doc.crop = None
            self._commit()
        self.crop_draft = [0, 0, self.base.width(), self.base.height()]
        self.canvas.update()

    def revert(self):
        if self.doc.is_empty():
            return
        if not ui.ask(self, "Revert to original?", "All annotations and image changes are removed. You can undo this.",
                      "Revert", destructive=True):
            return
        self._checkpoint()
        self.doc = Document()
        self.selected = None
        self.fit_mode = True
        self._rebuild_base()
        self._schedule_save()

    def copy(self):
        ui.copy_image(self.render_output())
        self.status.setText("Copied to the clipboard")

    def export(self):
        self._flush_save()
        ui.export_dialog(self, self.library, self.config, [self.capture_id])

    def closeEvent(self, ev):
        self._flush_save()
        super().closeEvent(ev)

    def _on_library_event(self, kind, ids):
        if self.capture_id not in ids:
            return
        if kind == "changed":
            try:
                self.cap = self.library.get(self.capture_id)
                self._update_title()
            except KeyError:
                pass
        elif kind == "deleted":
            self.close()
