"""Visual identity: palette, stylesheet and the line-icon set.

The one recurring motif is the viewfinder bracket from the app icon: selections,
crop frames and focus rings use corner brackets instead of square handles.
"""

from __future__ import annotations

import shutil
import subprocess

from PyQt6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer

ACCENT = "#4FA83D"
ACCENT_HOVER = "#45953A"
CANVAS = "#1F2226"
RED = "#E53935"

LIGHT = {
    "bg": "#F5F6F4", "surface": "#FFFFFF", "raised": "#ECEEEA", "line": "#DCDFDA",
    "ink": "#1E2226", "muted": "#6A7077", "hover": "#E6E9E4", "select": "#DDEFD7",
}
DARK = {
    "bg": "#25282C", "surface": "#2C3035", "raised": "#33373C", "line": "#3B4046",
    "ink": "#E9ECEA", "muted": "#9AA1A8", "hover": "#363B41", "select": "#2F4429",
}


def _system_prefers_dark() -> bool:
    if shutil.which("gsettings"):
        for schema, key in (("org.cinnamon.desktop.interface", "gtk-theme"),
                            ("org.gnome.desktop.interface", "color-scheme"),
                            ("org.gnome.desktop.interface", "gtk-theme")):
            try:
                out = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True,
                                     timeout=2).stdout.lower()
            except (OSError, subprocess.SubprocessError):
                continue
            if "dark" in out:
                return True
            if out.strip() and "default" not in out and schema.startswith("org.cinnamon"):
                return False
    return False


class Theme:
    def __init__(self, mode: str = "system"):
        self.dark = _system_prefers_dark() if mode == "system" else mode == "dark"
        self.c = dict(DARK if self.dark else LIGHT)
        self.c["accent"] = ACCENT
        self._icon_cache: dict = {}
        self.check_path = ""

    # ------------------------------------------------------------ application
    def apply(self, app) -> None:
        from . import paths

        check = paths.ensure_dir(paths.cache_dir()) / "check.svg"
        check.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="white" '
                         'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10"/></svg>')
        self.check_path = str(check)
        app.setStyle("Fusion")
        c = self.c
        pal = QPalette()
        roles = {
            QPalette.ColorRole.Window: c["bg"], QPalette.ColorRole.WindowText: c["ink"],
            QPalette.ColorRole.Base: c["surface"], QPalette.ColorRole.AlternateBase: c["raised"],
            QPalette.ColorRole.Text: c["ink"], QPalette.ColorRole.Button: c["raised"],
            QPalette.ColorRole.ButtonText: c["ink"], QPalette.ColorRole.Highlight: ACCENT,
            QPalette.ColorRole.HighlightedText: "#FFFFFF", QPalette.ColorRole.ToolTipBase: c["ink"],
            QPalette.ColorRole.ToolTipText: c["bg"], QPalette.ColorRole.PlaceholderText: c["muted"],
            QPalette.ColorRole.Link: ACCENT, QPalette.ColorRole.Mid: c["line"],
        }
        for role, value in roles.items():
            pal.setColor(role, QColor(value))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["muted"]))
        app.setPalette(pal)
        app.setStyleSheet(self.stylesheet())

    def stylesheet(self) -> str:
        c = self.c
        return f"""
        QWidget {{ font-size: 10pt; }}
        QToolTip {{ background: {c['ink']}; color: {c['bg']}; border: none; padding: 5px 8px; border-radius: 6px; }}
        QMainWindow, QDialog {{ background: {c['bg']}; }}

        QPushButton, QToolButton {{
            background: {c['raised']}; color: {c['ink']}; border: 1px solid transparent;
            border-radius: 8px; padding: 6px 12px; }}
        QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
        QPushButton:pressed, QToolButton:pressed {{ background: {c['line']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; }}
        QPushButton[accent="true"] {{ background: {ACCENT}; color: white; font-weight: 600; }}
        QPushButton[accent="true"]:hover {{ background: {ACCENT_HOVER}; }}
        QPushButton[danger="true"] {{ color: {RED}; }}
        QToolButton[flat="true"] {{ background: transparent; padding: 6px; }}
        QToolButton[flat="true"]:hover {{ background: {c['hover']}; }}
        QToolButton[flat="true"]:checked {{ background: {c['select']}; }}
        QToolButton::menu-indicator {{ image: none; width: 0; }}

        QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{
            background: {c['surface']}; color: {c['ink']}; border: 1px solid {c['line']};
            border-radius: 8px; padding: 5px 8px; selection-background-color: {ACCENT}; }}
        QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
        QLineEdit#search {{ border-radius: 17px; padding: 7px 14px 7px 34px; font-size: 10.5pt;
                            background: {c['surface']}; }}
        QComboBox::drop-down {{ border: none; width: 22px; }}
        QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['line']};
                                       selection-background-color: {c['select']}; selection-color: {c['ink']}; }}

        QListView#sidebar {{ background: transparent; border: none; outline: none; padding: 6px 8px; }}
        QListView#sidebar::item {{ padding: 7px 10px; border-radius: 8px; color: {c['ink']}; }}
        QListView#sidebar::item:hover {{ background: {c['hover']}; }}
        QListView#sidebar::item:selected {{ background: {c['select']}; color: {c['ink']}; font-weight: 600; }}

        QListView#grid {{ background: {c['bg']}; border: none; outline: none; }}
        QTreeView {{ background: {c['bg']}; border: none; outline: none; alternate-background-color: {c['raised']}; }}
        QTreeView::item {{ padding: 5px 4px; }}
        QTreeView::item:selected {{ background: {c['select']}; color: {c['ink']}; }}
        QHeaderView::section {{ background: {c['bg']}; color: {c['muted']}; border: none;
                                border-bottom: 1px solid {c['line']}; padding: 6px 4px; }}

        QFrame#pane {{ background: {c['surface']}; border-left: 1px solid {c['line']}; }}
        QFrame#rail {{ background: {c['bg']}; border-right: 1px solid {c['line']}; }}
        QFrame#topbar {{ background: {c['bg']}; border-bottom: 1px solid {c['line']}; }}
        QLabel#title {{ font-size: 12.5pt; font-weight: 600; }}
        QLabel#muted, QLabel[muted="true"] {{ color: {c['muted']}; }}
        QLabel#section {{ color: {c['muted']}; font-weight: 600; padding: 10px 12px 2px 12px; }}
        QLabel#empty-title {{ font-size: 15pt; font-weight: 600; }}

        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {c['line']}; border-radius: 4px; min-height: 30px; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
        QScrollBar::handle:horizontal {{ background: {c['line']}; border-radius: 4px; min-width: 30px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

        QMenu {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 10px; padding: 6px; }}
        QMenu::item {{ padding: 7px 18px 7px 12px; border-radius: 6px; color: {c['ink']}; }}
        QMenu::item:selected {{ background: {c['hover']}; }}
        QMenu::separator {{ height: 1px; background: {c['line']}; margin: 5px 8px; }}

        QTabWidget::pane {{ border: none; }}
        QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 8px 14px; border: none;
                        border-bottom: 2px solid transparent; }}
        QTabBar::tab:selected {{ color: {c['ink']}; border-bottom: 2px solid {ACCENT}; }}
        QCheckBox {{ spacing: 8px; }}
        QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {c['line']};
                                background: {c['surface']}; }}
        QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
                                        image: url({self.check_path}); }}

        /* editor chrome stays dark in both modes: the image is the content */
        QWidget#editor-root {{ background: {CANVAS}; }}
        QFrame#editor-bar {{ background: #26292E; border-bottom: 1px solid #33373D; }}
        QFrame#editor-bar QLabel {{ color: #E9ECEA; }}
        QFrame#editor-bar QLabel#muted {{ color: #8E959C; }}
        QFrame#editor-bar QToolButton, QFrame#editor-bar QPushButton {{ background: transparent; color: #E9ECEA; }}
        QFrame#editor-bar QToolButton:hover, QFrame#editor-bar QPushButton:hover {{ background: #353A40; }}
        QFrame#editor-bar QPushButton[accent="true"] {{ background: {ACCENT}; color: white; }}
        QFrame#editor-bar QPushButton[accent="true"]:hover {{ background: {ACCENT_HOVER}; }}
        QFrame#dock {{ background: rgba(38, 41, 46, 0.96); border: 1px solid #3A3F45; border-radius: 16px; }}
        QFrame#dock QToolButton {{ background: transparent; border-radius: 10px; padding: 7px; }}
        QFrame#dock QToolButton:hover {{ background: #383D43; }}
        QFrame#dock QToolButton:checked {{ background: {ACCENT}; }}
        QFrame#props {{ background: rgba(38, 41, 46, 0.96); border: 1px solid #3A3F45; border-radius: 12px; }}
        QFrame#props QLabel, QFrame#props QCheckBox {{ color: #D9DDDA; }}
        QFrame#props QSpinBox, QFrame#props QComboBox {{ background: #2F3338; color: #E9ECEA;
                                                         border: 1px solid #41464C; padding: 3px 6px; }}
        QFrame#props QToolButton {{ background: transparent; color: #E9ECEA; border-radius: 8px; padding: 5px 8px; }}
        QFrame#props QToolButton:hover {{ background: #383D43; }}
        QFrame#props QCheckBox::indicator {{ background: #2F3338; border-color: #4A5057; }}
        QFrame#props QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
        QFrame#statusline QLabel {{ color: #8E959C; }}
        QFrame#statusline QToolButton {{ background: transparent; color: #C9CDD0; padding: 3px 6px; }}
        QFrame#statusline QToolButton:hover {{ background: #33373D; }}

        QFrame#quickbar {{ background: #26292E; border: 1px solid #3A3F45; border-radius: 14px; }}
        QFrame#quickbar QLabel {{ color: #E9ECEA; }}
        QFrame#quickbar QPushButton {{ background: #33373D; color: #E9ECEA; }}
        QFrame#quickbar QPushButton:hover {{ background: #3D4248; }}
        QFrame#quickbar QPushButton[accent="true"] {{ background: {ACCENT}; color: white; }}
        """

    # ------------------------------------------------------------ icons
    def icon(self, name: str, color: str | None = None, size: int = 20) -> QIcon:
        color = color or self.c["ink"]
        key = (name, color, size)
        if key not in self._icon_cache:
            self._icon_cache[key] = QIcon(svg_pixmap(name, color, size))
        return self._icon_cache[key]


def svg_pixmap(name: str, color: str, size: int = 20, ratio: float = 2.0) -> QPixmap:
    body = ICONS.get(name, ICONS["rect"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
           + body.replace("currentColor", color) + "</svg>")
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(QSize(int(size * ratio), int(size * ratio)))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def draw_brackets(painter: QPainter, rect: QRectF, color: QColor, length: float = 14.0, width: float = 3.0) -> None:
    """The viewfinder corner brackets used for every selection."""
    length = min(length, rect.width() / 2.5, rect.height() / 2.5)
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    l, t, r, b = rect.left(), rect.top(), rect.right(), rect.bottom()
    for (x, y, dx, dy) in ((l, t, 1, 1), (r, t, -1, 1), (r, b, -1, -1), (l, b, 1, -1)):
        path = QPainterPath(QPointF(x, y + dy * length))
        path.lineTo(QPointF(x, y))
        path.lineTo(QPointF(x + dx * length, y))
        painter.drawPath(path)


# 24×24 line icons (original artwork for CatCap)
ICONS = {
    "select": '<path d="M6 4l11 6.5-4.6 1.2 3 5.3-2.3 1.3-3-5.3L6.6 16.5z" fill="currentColor" stroke="none"/>',
    "arrow": '<path d="M5 19L18 6"/><path d="M10 6h8v8"/>',
    "line": '<path d="M5 19L19 5"/>',
    "rect": '<rect x="4" y="6" width="16" height="12" rx="2"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="8" ry="6"/>',
    "text": '<path d="M5 6V5h14v1"/><path d="M12 5v14"/><path d="M9 19h6"/>',
    "callout": '<path d="M4 5h16v10H11l-4 4v-4H4z"/>',
    "highlighter": '<path d="M9 15l-2 2 1 1h4l1-1"/><path d="M8 14l7-9 4 3-7 9z"/>'
                   '<path d="M3 21h18" stroke="#F4D03F" stroke-width="2.6"/>',
    "pen": '<path d="M4 17c3-6 5 2 8-3s4-6 8-7"/>',
    "step": '<circle cx="12" cy="12" r="8"/><path d="M11 9l2-1v8"/>',
    "blur": '<circle cx="12" cy="12" r="2"/><circle cx="12" cy="12" r="5" stroke-dasharray="2 2"/>'
            '<circle cx="12" cy="12" r="8.5" stroke-dasharray="1.5 3"/>',
    "pixelate": '<path d="M4 4h5v5H4zM15 4h5v5h-5zM9.5 9.5h5v5h-5zM4 15h5v5H4zM15 15h5v5h-5z" '
                'fill="currentColor" stroke="none"/>',
    "spotlight": '<path d="M3 5h18v14H3z" fill="currentColor" fill-opacity="0.35" stroke="none"/>'
                 '<circle cx="12" cy="12" r="4.5"/>',
    "crop": '<path d="M7 3v14h14"/><path d="M3 7h14v14"/>',
    "undo": '<path d="M9 7L5 11l4 4"/><path d="M5 11h9a5 5 0 010 10h-2"/>',
    "redo": '<path d="M15 7l4 4-4 4"/><path d="M19 11h-9a5 5 0 000 10h2"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2"/>',
    "export": '<path d="M12 4v11"/><path d="M7 9l5-5 5 5"/><path d="M5 15v3a2 2 0 002 2h10a2 2 0 002-2v-3"/>',
    "pin": '<path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 13v7"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
            '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="5" cy="6" r="1"/><circle cx="5" cy="12" r="1"/>'
            '<circle cx="5" cy="18" r="1"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "filter": '<path d="M4 6h16M7 12h10M10 18h4"/>',
    "more": '<circle cx="5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/>'
            '<circle cx="19" cy="12" r="1.3" fill="currentColor"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "import": '<path d="M4 7a2 2 0 012-2h4l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H6a2 2 0 01-2-2z"/><path d="M12 10v6M9 13l3 3 3-3"/>',
    "star": '<path d="M12 4l2.4 5 5.4.7-4 3.8 1 5.4L12 16.3 7.2 18.9l1-5.4-4-3.8 5.4-.7z"/>',
    "star-filled": '<path d="M12 4l2.4 5 5.4.7-4 3.8 1 5.4L12 16.3 7.2 18.9l1-5.4-4-3.8 5.4-.7z" '
                   'fill="currentColor"/>',
    "trash": '<path d="M5 7h14"/><path d="M10 4h4"/><path d="M7 7l1 12a1.5 1.5 0 001.5 1.4h5a1.5 1.5 0 001.5-1.4L17 7"/>',
    "rotate-left": '<path d="M5 10a7 7 0 112 6"/><path d="M5 4v6h6"/>',
    "rotate-right": '<path d="M19 10a7 7 0 10-2 6"/><path d="M19 4v6h-6"/>',
    "flip-h": '<path d="M12 3v18" stroke-dasharray="2 2"/><path d="M9 7L4 17h5zM15 7l5 10h-5z"/>',
    "flip-v": '<path d="M3 12h18" stroke-dasharray="2 2"/><path d="M7 9L17 4v5zM7 15l10 5v-5z"/>',
    "resize": '<path d="M4 14v6h6"/><path d="M20 10V4h-6"/><path d="M4 20l7-7M20 4l-7 7"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "check": '<path d="M5 12l5 5 9-10"/>',
    "capture": '<path d="M4 9V5a1 1 0 011-1h4M15 4h4a1 1 0 011 1v4M20 15v4a1 1 0 01-1 1h-4M9 20H5a1 1 0 01-1-1v-4"/>'
               '<circle cx="12" cy="12" r="3"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1'
                'M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    "tag": '<path d="M4 12V5a1 1 0 011-1h7l8 8-8 8z"/><circle cx="8.5" cy="8.5" r="1.3"/>',
    "folder": '<path d="M4 7a2 2 0 012-2h4l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H6a2 2 0 01-2-2z"/>',
    "clock": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    "images": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/>'
              '<path d="M21 16l-5-5-8 8"/>',
    "drag": '<path d="M12 3v18M3 12h18"/><path d="M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    "zoom-in": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M11 8v6M8 11h6"/>',
    "zoom-out": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5M8 11h6"/>',
    "fit": '<path d="M4 9V5a1 1 0 011-1h4M15 4h4a1 1 0 011 1v4M20 15v4a1 1 0 01-1 1h-4M9 20H5a1 1 0 01-1-1v-4"/>',
    "edit": '<path d="M5 19h4L19 9l-4-4L5 15z"/><path d="M13 7l4 4"/>',
    "window": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18"/>',
    "screen": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "timer": '<circle cx="12" cy="13" r="7"/><path d="M12 13V9M10 3h4"/>',
    "repeat": '<path d="M4 11V9a3 3 0 013-3h11"/><path d="M15 3l3 3-3 3"/><path d="M20 13v2a3 3 0 01-3 3H6"/>'
              '<path d="M9 21l-3-3 3-3"/>',
}
