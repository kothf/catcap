"""CatCap: screenshot capture, annotation and library app for Linux Mint."""

__version__ = "0.2.4"
APP_ID = "io.github.kothf.CatCap"
APP_NAME = "CatCap"

AUTHOR = "Andrey"
AUTHOR_URL = "https://github.com/kothf"
# (name, what it is used for, homepage) shown under Credits in the About dialog.
CREDITS = [
    ("Qt 6 and PyQt6", "user interface, imaging and D-Bus", "https://www.riverbankcomputing.com/software/pyqt/"),
    ("python-xlib", "window detection on X11", "https://github.com/python-xlib/python-xlib"),
    ("xdg-desktop-portal", "screenshots on Wayland", "https://flatpak.github.io/xdg-desktop-portal/"),
    ("SQLite", "the capture library", "https://sqlite.org/"),
    ("Claude Code by Anthropic", "development help", "https://claude.com/claude-code"),
]
