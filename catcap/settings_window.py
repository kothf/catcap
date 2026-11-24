"""Settings window (SET-01..05)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
                             QKeySequenceEdit, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget, QToolButton,
                             QVBoxLayout, QWidget)

from . import hotkeys, ui

AFTER_ACTIONS = [("toolbar", "Show the quick bar"), ("editor", "Open the editor"),
                 ("clipboard", "Copy to the clipboard"), ("library", "Just save to the library")]
FORMATS = [("png", "PNG"), ("jpg", "JPG"), ("webp", "WebP"), ("bmp", "BMP")]
THEMES = [("system", "Follow the system"), ("light", "Light"), ("dark", "Dark")]


class SettingsWindow(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.config = app.config
        self.setWindowTitle("CatCap settings")
        self.setWindowIcon(app.app_icon)
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        tabs = QTabWidget()
        for title, builder in (("General", self._general), ("Capture", self._capture), ("Hotkeys", self._hotkeys),
                               ("Library", self._library), ("Export", self._output)):
            page = builder()
            page.setContentsMargins(6, 16, 6, 6)
            tabs.addTab(page, title)
        lay.addWidget(tabs)
        close = QPushButton("Done")
        close.setProperty("accent", True)
        close.clicked.connect(self.close)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    # ------------------------------------------------------------ helpers
    def _form(self):
        w = QWidget()
        form = QFormLayout(w)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        return w, form

    def _check(self, form, label, section, key, on_change=None, hint=None):
        box = QCheckBox(label)
        box.setChecked(bool(self.config.get(section, key)))

        def changed(v):
            self.config.set(section, key, bool(v))
            if on_change:
                on_change()

        box.toggled.connect(changed)
        form.addRow(box)
        if hint:
            h = QLabel(hint)
            h.setObjectName("muted")
            h.setWordWrap(True)
            h.setContentsMargins(28, 0, 0, 0)
            form.addRow(h)
        return box

    def _combo(self, form, label, section, key, options, on_change=None):
        combo = QComboBox()
        for k, text in options:
            combo.addItem(text, k)
        combo.setCurrentIndex(max(0, combo.findData(str(self.config.get(section, key)))))

        def changed(_i):
            self.config.set(section, key, combo.currentData())
            if on_change:
                on_change()

        combo.currentIndexChanged.connect(changed)
        form.addRow(label, combo)
        return combo

    def _spin(self, form, label, section, key, lo, hi, suffix=""):
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setSuffix(suffix)
        spin.setValue(int(self.config.get(section, key)))
        spin.valueChanged.connect(lambda v: self.config.set(section, key, int(v)))
        form.addRow(label, spin)
        return spin

    def _note(self, form, text):
        note = QLabel(text)
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow(note)

    # ------------------------------------------------------------ pages
    def _general(self):
        w, form = self._form()
        self._check(form, "Start CatCap when I log in", "general", "start_at_login", self.app.update_autostart)
        self._check(form, "Show a notification after each capture", "general", "notifications")
        self._check(form, "Play a shutter sound", "general", "sound")
        self._combo(form, "Appearance", "general", "theme", THEMES,
                    lambda: self._note_restart(form))
        self._note(form, f"Settings are stored in {self.config.path}")
        return w

    def _note_restart(self, form):
        self.app.apply_theme()

    def _capture(self):
        w, form = self._form()
        self._combo(form, "After a capture", "capture", "after_action", AFTER_ACTIONS)
        self._spin(form, "Default delay", "capture", "delay", 0, 60, " s")
        self._check(form, "Include the mouse pointer", "capture", "include_cursor",
                    hint="X11 sessions only. On Wayland the screenshot portal decides.")
        self._check(form, "Include window title bars and borders", "capture", "window_decorations")
        self._check(form, "Show the magnifier while selecting", "capture", "magnifier")
        self._spin(form, "Magnifier zoom", "capture", "magnifier_zoom", 2, 20, "×")
        self._check(form, "Highlight windows under the pointer", "capture", "snap_to_windows")
        if self.app.backend.name == "wayland":
            self._note(form, "Wayland session: window and active-window captures use the portal's picker, "
                             "region selection works on the primary monitor, and the source app isn't recorded.")
        return w

    def _hotkeys(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        mgr = self.app.hotkeys
        top, form = self._form()
        self._check(form, "Turn on global hotkeys", "hotkeys", "enabled", self._apply_hotkeys)
        if hotkeys.is_cinnamon():
            self._check(form, "Use Print Screen for CatCap", "hotkeys", "take_over_print", self._apply_hotkeys,
                        hint="Frees Print Screen from Cinnamon's built-in screenshot tool. Turning this off, or "
                             "turning hotkeys off, gives the keys back to Cinnamon.")
        lay.addWidget(top)
        desc = QLabel(mgr.backend_description())
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        lay.addWidget(desc)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for col, head in enumerate(("Action", "Shortcut", "", "Command")):
            h = QLabel(head)
            h.setObjectName("muted")
            grid.addWidget(h, 0, col)
        for row, (action, label, flag) in enumerate(hotkeys.ACTIONS, start=1):
            grid.addWidget(QLabel(label), row, 0)
            edit = QKeySequenceEdit(QKeySequence(hotkeys.gtk_to_qt(self.config.get("hotkeys", action))))
            edit.setMaximumSequenceLength(1) if hasattr(edit, "setMaximumSequenceLength") else None
            edit.editingFinished.connect(lambda e=edit, a=action: self._set_key(a, e))
            grid.addWidget(edit, row, 1)
            clear = QToolButton()
            clear.setProperty("flat", True)
            clear.setIcon(self.app.theme.icon("close", size=14))
            clear.setToolTip("No shortcut")
            clear.clicked.connect(lambda _c=False, e=edit, a=action: (e.clear(), self._set_key(a, e)))
            grid.addWidget(clear, row, 2)
            cmd = QLineEdit(mgr.command(flag))
            cmd.setReadOnly(True)
            grid.addWidget(cmd, row, 3)
        lay.addLayout(grid)
        self.hk_status = QLabel()
        self.hk_status.setWordWrap(True)
        lay.addWidget(self.hk_status)
        lay.addStretch(1)
        return w

    def _set_key(self, action, edit):
        seq = edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText).split(",")[0].strip()
        self.config.set("hotkeys", action, hotkeys.qt_to_gtk(seq))
        self._apply_hotkeys()

    def _apply_hotkeys(self):
        warnings = self.app.hotkeys.apply()
        if warnings:
            self.hk_status.setText("Check these:\n" + "\n".join(warnings))
        elif self.config.get("hotkeys", "enabled"):
            self.hk_status.setText("Hotkeys are on.")
        else:
            self.hk_status.setText("")

    def _library(self):
        w, form = self._form()
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.folder = QLineEdit(str(self.config.images_dir()))
        self.folder.setReadOnly(True)
        self.folder.setMinimumWidth(320)
        change = QPushButton("Change…")
        change.clicked.connect(self._move_library)
        rl.addWidget(self.folder, 1)
        rl.addWidget(change)
        form.addRow("Image folder", row)
        self._spin(form, "Empty trash items after", "library", "trash_days", 0, 3650, " days")
        self._note(form, "Set to 0 to keep items in the trash until you empty it.")
        self._note(form, "Each original is kept as a PNG with a small .json file next to it that holds your edits. "
                         "Edited images are rendered when you copy, export or drag them, so they don't take extra space.")
        return w

    def _move_library(self):
        new_dir = QFileDialog.getExistingDirectory(self, "Choose the image folder", str(self.app.library.images_dir))
        if not new_dir or Path(new_dir) == self.app.library.images_dir:
            return
        if not ui.ask(self, "Move the library?", f"All captures move to {new_dir}.", "Move"):
            return
        try:
            self.app.library.move_images(Path(new_dir))
            self.config.set("library", "images_dir", new_dir)
            self.folder.setText(new_dir)
        except Exception as exc:
            ui.error(self, "Couldn't move the library", str(exc))

    def _output(self):
        w, form = self._form()
        name = QLineEdit(self.config.get("output", "filename_template"))
        name.setMinimumWidth(300)
        name.textChanged.connect(lambda t: self.config.set("output", "filename_template", t))
        form.addRow("File names", name)
        self._note(form, "Use {app}, {title}, {name}, {mode}, {width}, {height}, {id} and date patterns such as "
                         "{yyyy-MM-dd} or {HHmmss}.")
        self._combo(form, "Default format", "output", "default_format", FORMATS)
        self._spin(form, "JPG and WebP quality", "output", "jpg_quality", 10, 100, " %")
        return w
