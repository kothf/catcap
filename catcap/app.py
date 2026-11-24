"""CatCap application: single instance, CLI, tray, capture workflow."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusPendingReply
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtNetwork import QLocalServer
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import APP_ID, APP_NAME, __version__, paths, ui
from .capture import Snapshot, get_backend, is_wayland
from .config import Config
from .library import Library
from .theme import Theme

CAPTURE_FLAGS = ["region", "window", "full", "active", "monitor", "last_region"]


def icon_file() -> str:
    root = paths.source_root()
    if root is not None:
        return str(root / "data/icons/hicolor/scalable/apps" / f"{APP_ID}.svg")
    return f"/usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg"


def app_icon() -> QIcon:
    """CatCap's icon as ready-made pixmaps.

    QIcon only reads SVG through Qt's SVG icon engine plugin (qt6-svg-plugins), which is often not
    installed; without it the tray shows the desktop's "missing icon" placeholder. QSvgRenderer
    (python3-pyqt6.qtsvg, a hard dependency) always works, so the SVG is rendered here.
    """
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QPainter, QPixmap
    from PyQt6.QtSvg import QSvgRenderer

    icon = QIcon()
    renderer = QSvgRenderer(icon_file())
    if renderer.isValid():
        for size in (16, 22, 24, 32, 48, 64, 128, 256):
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            renderer.render(painter, QRectF(0, 0, size, size))
            painter.end()
            icon.addPixmap(pm)
    if icon.isNull():
        icon = QIcon.fromTheme(APP_ID)
    return icon


class CatCap:
    def __init__(self, qapp: QApplication):
        self.qapp = qapp
        self.config = Config()
        self.theme = Theme(self.config.get("general", "theme"))
        self.theme.apply(qapp)
        self.app_icon = app_icon()
        self.tray_icon = self.app_icon
        qapp.setWindowIcon(self.app_icon)
        self.library = Library(self.config.images_dir())
        try:
            self.library.purge_trash(int(self.config.get("library", "trash_days")))
        except Exception as exc:
            print(f"catcap: trash purge failed: {exc}")
        ui.cleanup_temp()
        # The tray process can run for weeks: keep clearing old drag/open-with renders.
        self._cleanup_timer = QTimer(interval=3600 * 1000)
        self._cleanup_timer.timeout.connect(ui.cleanup_temp)
        self._cleanup_timer.start()
        self.backend = get_backend()
        from .hotkeys import HotkeyManager

        self.hotkeys = HotkeyManager(self.config)
        if self.config.get("hotkeys", "enabled"):
            self.hotkeys.apply()
        self.tray = None
        if os.environ.get("CATCAP_NO_TRAY") != "1":
            from .tray import Tray

            self.tray = Tray(self)
        if self.tray is None or not self.tray.available():
            # Without a tray there is nothing to come back to: quit with the last window.
            qapp.setQuitOnLastWindowClosed(True)
        self.update_autostart()
        self.library_window = None
        self.settings_window = None
        self.editors: dict = {}
        self._capturing = False
        self._hidden: list = []
        self._keep = []   # keep top-level helper windows alive

    def apply_theme(self):
        self.theme = Theme(self.config.get("general", "theme"))
        self.theme.apply(self.qapp)

    def quit(self):
        self.qapp.quit()

    # ================================================================ command handling
    def handle(self, args: dict, cwd: str):
        if args.get("quit"):
            self.quit()
            return
        if args.get("release_hotkeys"):
            self.config.set("hotkeys", "enabled", False)
            self.hotkeys.remove()
            return
        action = "clipboard" if args.get("clipboard") else "editor" if args.get("edit") else (
            "library" if args.get("save_only") else None)
        did = False
        for mode in CAPTURE_FLAGS:
            if args.get(mode):
                self.start_capture(mode, delay=args.get("delay"), action=action, cursor=args.get("cursor"))
                did = True
                break
        for f in args.get("files") or []:
            path = os.path.join(cwd, f)
            try:
                self.open_editor(self.library.import_file(path))
            except Exception as exc:
                print(f"catcap: cannot open {path}: {exc}", file=sys.stderr)
            did = True
        if args.get("settings"):
            self.show_settings()
            did = True
        if args.get("library"):
            self.show_library()
            did = True
        if not did and not args.get("background"):
            self.show_library()
            QTimer.singleShot(300, self._maybe_welcome)

    # ================================================================ capture workflow
    def start_capture(self, mode: str, delay: int | None = None, action: str | None = None,
                      hide=None, cursor: bool | None = None) -> None:
        if self._capturing:
            return
        self._capturing = True
        delay = int(self.config.get("capture", "delay")) if delay is None else delay
        self._hidden = []
        if hide is not None:
            for win in QApplication.topLevelWidgets():
                if win.isVisible() and win.isWindow() and not isinstance(win, QMessageBox):
                    win.hide()
                    self._hidden.append(win)
        wait = 380 if self._hidden else 200

        def go():
            QTimer.singleShot(wait, lambda: self._grab(mode, action, cursor))

        if delay and delay > 0:
            from .widgets import Countdown

            self._keep.append(Countdown(delay, go, on_cancel=self._end_capture))
        else:
            go()

    def _grab(self, mode, action, cursor):
        include_cursor = self.config.get("capture", "include_cursor") if cursor is None else cursor

        def done(snap, err):
            if snap is None:
                self._end_capture()
                if err and err != "cancelled":
                    self.notify("Capture failed", err, error=True)
                return
            try:
                self._handle_snapshot(snap, mode, action)
            except Exception as exc:
                self._end_capture()
                self.notify("Capture failed", str(exc), error=True)

        if self.backend.name == "wayland" and mode in ("window", "active"):
            self.backend.pick_window(done)
        else:
            self.backend.snapshot(done, include_cursor)

    def _handle_snapshot(self, snap: Snapshot, mode: str, action):
        if mode == "full" or (self.backend.name == "wayland" and mode in ("window", "active")):
            self._store(snap.image, "full" if mode == "full" else mode, snap.active, action)
        elif mode == "monitor":
            mon = snap.monitor_at(*snap.pointer)
            name, x, y, w, h = mon if mon else ("", 0, 0, snap.image.width(), snap.image.height())
            self._store(snap.crop(x, y, w, h), "monitor", None, action, monitor=name)
        elif mode == "active":
            win = snap.active
            if win is None:
                self._store(snap.image, "full", None, action)
            else:
                x, y, w, h = win.rect(bool(self.config.get("capture", "window_decorations")))
                self._store(snap.crop(x, y, w, h), "active", win, action)
        elif mode == "last_region" and self._last_region():
            x, y, w, h = self._last_region()
            self._store(snap.crop(x, y, w, h), "last_region", snap.window_at(x + w / 2, y + h / 2), action)
        else:
            self._open_overlay(snap, "window" if mode == "window" else "region", action)

    def _last_region(self):
        try:
            x, y, w, h = [int(v) for v in self.config.get("state", "last_region").split(",")]
            return (x, y, w, h) if w > 0 and h > 0 else None
        except ValueError:
            return None

    def _open_overlay(self, snap: Snapshot, mode: str, action):
        from .overlay import Overlay

        # On X11 one overlay spans every monitor. On Wayland a window can't be placed across
        # outputs, so each monitor gets its own full-screen overlay showing its part of the
        # snapshot; the first one to finish (or cancel) closes the others.
        if snap.backend == "wayland":
            parts = []
            screens = QGuiApplication.screens() or [QGuiApplication.primaryScreen()]
            for index, screen in enumerate(screens):
                _n, x, y, w, h = snap.monitors[index] if index < len(snap.monitors) else (
                    "", 0, 0, snap.image.width(), snap.image.height())
                parts.append((Snapshot(image=snap.crop(x, y, w, h), scale=snap.scale, backend="wayland"),
                              (x, y), screen))
        else:
            parts = [(snap, (0, 0), None)]

        overlays: list = []
        state = {"done": False}

        def finished(result, offset):
            if state["done"]:
                return
            state["done"] = True
            for other in overlays:
                if not other._done:
                    other._close()
            if result is None:
                self._end_capture()
                return
            x, y, w, h = result.rect
            self.config.set("state", "last_region", f"{x + offset[0]},{y + offset[1]},{w},{h}")
            info = type("Info", (), {"app": result.app, "title": result.title})()
            chosen = {"copy": "clipboard", "edit": "editor", "pin": "pin"}.get(result.action)
            self._store(result.image, result.mode, info, chosen or action, items=result.items)

        for part_snap, offset, screen in parts:
            overlay = Overlay(part_snap, mode, self.config, lambda r, o=offset: finished(r, o), screen)
            overlays.append(overlay)
            self._keep.append(overlay)
            overlay.destroyed.connect(lambda *_, ov=overlay: self._keep.remove(ov) if ov in self._keep else None)
        for overlay in overlays:
            overlay.start()

    def _store(self, image, mode, info, action, monitor="", items=None):
        cid = self.library.add_capture(image, mode=mode, app=getattr(info, "app", "") if info else "",
                                       title=getattr(info, "title", "") if info else "", monitor=monitor)
        if items:
            # Drawn in the overlay: kept as editable annotations over the untouched original.
            from .annotations import Document

            self.library.save_doc(cid, Document(items=items))
        self._play_sound()
        action = action or self.config.get("capture", "after_action")
        self._end_capture(restore=action != "editor")
        size = f"{image.width()} × {image.height()}"
        if action == "editor":
            self.open_editor(cid)
        elif action == "clipboard":
            ui.copy_capture(self.library, cid)
            self.notify("Copied to the clipboard", f"{size}, also saved in your library")
        elif action == "pin":
            from . import render

            self.pin_image(render.render_capture(self.library, cid))
        elif action == "library":
            self.notify("Capture saved", size)
        else:
            from .widgets import QuickBar

            bar = QuickBar(self, cid)
            self._keep.append(bar)
            bar.destroyed.connect(lambda *_: self._keep.remove(bar) if bar in self._keep else None)
        return cid

    def _end_capture(self, restore=True):
        self._capturing = False
        for win in self._hidden:
            win.show()
            if restore:
                win.raise_()
        self._hidden = []

    def _play_sound(self):
        if not self.config.get("general", "sound"):
            return
        if shutil.which("canberra-gtk-play"):
            subprocess.Popen(["canberra-gtk-play", "-i", "camera-shutter"], stderr=subprocess.DEVNULL)
        elif shutil.which("paplay") and os.path.exists("/usr/share/sounds/freedesktop/stereo/camera-shutter.oga"):
            subprocess.Popen(["paplay", "/usr/share/sounds/freedesktop/stereo/camera-shutter.oga"],
                             stderr=subprocess.DEVNULL)

    def notify(self, title, body, error=False):
        if error:
            print(f"catcap: {title}: {body}", file=sys.stderr)
        elif not self.config.get("general", "notifications"):
            return
        msg = QDBusMessage.createMethodCall("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                            "org.freedesktop.Notifications", "Notify")
        msg.setArguments([APP_NAME, 0, icon_file(), title, body, [], {}, 4000])
        # Asynchronous: a slow or missing notification daemon must not freeze the app.
        watcher = QDBusPendingCallWatcher(QDBusConnection.sessionBus().asyncCall(msg, 1500))
        self._keep.append(watcher)

        def finished(w):
            if w in self._keep:
                self._keep.remove(w)
            if QDBusPendingReply(w).isError() and not (self.tray and self.tray.message(title, body)) and error:
                ui.error(None, title, body)
            w.deleteLater()

        watcher.finished.connect(finished)

    # ================================================================ windows
    def show_library(self, select_id: str | None = None):
        from .library_window import LibraryWindow

        if self.library_window is None:
            self.library_window = LibraryWindow(self)
            self.library_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.library_window.destroyed.connect(lambda *_: setattr(self, "library_window", None))
            self.library_window.closing.connect(self._library_closing)
        self.library_window.show()
        self.library_window.raise_()
        self.library_window.activateWindow()
        if select_id:
            QTimer.singleShot(200, lambda: self.library_window and self.library_window.select_capture(select_id))

    def _library_closing(self):
        if self.tray is None or not self.tray.available() or self.config.get("general", "tray_hint_shown"):
            return
        self.config.set("general", "tray_hint_shown", True)
        self.tray.message("CatCap is still running",
                          "It stays in the panel tray. Click the icon to capture, right-click for more.")

    def copy_last(self):
        from .library import Query

        caps = self.library.query(Query())
        if caps:
            ui.copy_capture(self.library, caps[0].id)
            self.notify("Copied to the clipboard", caps[0].name)

    def open_editor(self, capture_id: str):
        from .editor import Editor

        editor = self.editors.get(capture_id)
        if editor is None:
            editor = Editor(self, capture_id)
            self.editors[capture_id] = editor
            editor.destroyed.connect(lambda *_: self.editors.pop(capture_id, None))
        editor.show()
        editor.raise_()
        editor.activateWindow()
        return editor

    def pin_image(self, image):
        from .widgets import PinWindow

        pin = PinWindow(self, image)
        self._keep.append(pin)
        pin.destroyed.connect(lambda *_: self._keep.remove(pin) if pin in self._keep else None)
        pin.show()

    def show_settings(self, parent=None):
        from .settings_window import SettingsWindow

        if self.settings_window is None:
            self.settings_window = SettingsWindow(self, parent)
            self.settings_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.settings_window.destroyed.connect(lambda *_: setattr(self, "settings_window", None))
        self.settings_window.show()
        self.settings_window.raise_()

    def show_shortcuts(self, parent=None):
        from .widgets import show_shortcuts

        self._keep.append(show_shortcuts(parent))

    def show_about(self, parent=None):
        from . import AUTHOR, AUTHOR_CALLSIGN, AUTHOR_URL, CREDITS

        credits = "".join(f"<li><a href='{url}'>{name}</a>: {what}</li>" for name, what, url in CREDITS)
        QMessageBox.about(parent, "About CatCap",
                          f"<h3>CatCap {__version__}</h3><p>Capture, annotate and organise screenshots on Linux.</p>"
                          f"<p>Created by <a href='{AUTHOR_URL}'>{AUTHOR}</a>, {AUTHOR_CALLSIGN}.</p>"
                          f"<p><b>Credits</b></p><ul>{credits}</ul>"
                          "<p>MIT licence · <a href='https://github.com/kothf/catcap'>github.com/kothf/catcap</a></p>")

    def update_autostart(self):
        target = paths.autostart_dir() / f"{APP_ID}.desktop"
        if self.config.get("general", "start_at_login"):
            paths.ensure_dir(target.parent, private=False)
            target.write_text("[Desktop Entry]\nType=Application\nName=CatCap\nComment=Screenshot tool (tray)\n"
                              f"Exec={paths.launcher()} --background\nIcon={APP_ID}\nTerminal=false\n"
                              "X-GNOME-Autostart-enabled=true\n", encoding="utf-8")
        else:
            try:
                target.unlink()
            except FileNotFoundError:
                pass

    def _maybe_welcome(self):
        if self.config.get("general", "first_run_done"):
            return
        self.config.set("general", "first_run_done", True)
        from .hotkeys import is_cinnamon
        from .widgets import WelcomeDialog

        dlg = WelcomeDialog(self, is_cinnamon(), is_wayland())
        dlg.exec()
        self.config.set("hotkeys", "enabled", dlg.print_check.isChecked())
        self.config.set("hotkeys", "take_over_print", dlg.print_check.isChecked())
        self.config.set("general", "start_at_login", dlg.login_check.isChecked())
        self.config.set("capture", "after_action", dlg.after.currentData() or "toolbar")
        self.update_autostart()
        warnings = self.hotkeys.apply()
        if warnings:
            ui.error(self.library_window, "Hotkeys need attention", "\n".join(warnings))


def run(args) -> int:
    """Start the CatCap instance (tray process) and handle the first command."""
    from .cli import socket_path

    QApplication.setApplicationName(APP_NAME)
    QApplication.setDesktopFileName(APP_ID)
    qapp = QApplication([sys.argv[0]])
    qapp.setQuitOnLastWindowClosed(False)
    if args.release_hotkeys:
        from .hotkeys import HotkeyManager

        config = Config()
        config.set("hotkeys", "enabled", False)
        HotkeyManager(config).remove()
        print("CatCap hotkeys removed; Cinnamon's screenshot keys are restored.")
        return 0
    from .library import NewerLibraryError

    try:
        app = CatCap(qapp)
    except NewerLibraryError as exc:
        print(f"catcap: {exc}", file=sys.stderr)
        ui.error(None, "CatCap is too old for this library", str(exc))
        return 1
    path = socket_path()
    QLocalServer.removeServer(path)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(path):
        print(f"catcap: cannot listen on {path}: {server.errorString()}", file=sys.stderr)

    def on_connection():
        conn = server.nextPendingConnection()
        buf = bytearray()

        def read():
            buf.extend(bytes(conn.readAll()))
            if not buf.endswith(b"\n"):
                return
            try:
                msg = json.loads(buf.decode())
            except ValueError:
                return
            conn.write(b"ok\n")
            conn.flush()
            QTimer.singleShot(0, lambda: app.handle(msg.get("args", {}), msg.get("cwd", str(Path.home()))))

        conn.readyRead.connect(read)
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(on_connection)
    qapp.aboutToQuit.connect(lambda: QLocalServer.removeServer(path))
    QTimer.singleShot(0, lambda: app.handle(vars(args), os.getcwd()))
    return qapp.exec()


def main(argv=None) -> int:
    from .cli import main as cli_main

    return cli_main(argv)
