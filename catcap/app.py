"""CatCap application: single instance, CLI, tray, capture workflow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtDBus import QDBusConnection, QDBusMessage
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import APP_ID, APP_NAME, __version__, paths, ui
from .capture import Snapshot, get_backend, is_wayland
from .config import Config
from .library import Library
from .theme import Theme

CAPTURE_FLAGS = ["region", "window", "full", "active", "monitor", "last_region"]


def parse_args(argv):
    p = argparse.ArgumentParser(prog="catcap", description="Capture, annotate and organise screenshots.")
    g = p.add_argument_group("capture")
    g.add_argument("--region", action="store_true", help="capture a region or window")
    g.add_argument("--window", action="store_true", help="capture a window")
    g.add_argument("--full", action="store_true", help="capture the whole screen (all monitors)")
    g.add_argument("--monitor", action="store_true", help="capture the monitor under the pointer")
    g.add_argument("--active", action="store_true", help="capture the active window")
    g.add_argument("--last-region", dest="last_region", action="store_true", help="capture the last region again")
    g.add_argument("-d", "--delay", type=int, help="wait N seconds before capturing")
    g.add_argument("--cursor", dest="cursor", action="store_true", default=None, help="include the mouse pointer")
    g.add_argument("--no-cursor", dest="cursor", action="store_false", help="leave the mouse pointer out")
    a = p.add_argument_group("after capture")
    a.add_argument("--clipboard", action="store_true", help="copy to the clipboard")
    a.add_argument("--edit", action="store_true", help="open in the editor")
    a.add_argument("--save-only", dest="save_only", action="store_true", help="only save to the library")
    o = p.add_argument_group("other")
    o.add_argument("--library", action="store_true", help="open the library")
    o.add_argument("--settings", action="store_true", help="open settings")
    o.add_argument("--background", action="store_true", help="start in the tray without opening a window")
    o.add_argument("--release-hotkeys", dest="release_hotkeys", action="store_true",
                   help="remove CatCap's shortcuts and give Print Screen back to Cinnamon, then exit")
    o.add_argument("--quit", action="store_true", help="quit the running CatCap")
    o.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    p.add_argument("files", nargs="*", help="image files to open in the editor")
    return p.parse_args(argv)


def socket_name() -> str:
    return f"catcap-{os.getuid()}"


def icon_file() -> str:
    root = paths.source_root()
    if root is not None:
        return str(root / "data/icons/hicolor/scalable/apps" / f"{APP_ID}.svg")
    return f"/usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg"


class CatCap:
    def __init__(self, qapp: QApplication):
        self.qapp = qapp
        self.config = Config()
        self.theme = Theme(self.config.get("general", "theme"))
        self.theme.apply(qapp)
        self.app_icon = QIcon.fromTheme(APP_ID, QIcon(icon_file()))
        self.tray_icon = self.app_icon
        qapp.setWindowIcon(self.app_icon)
        self.library = Library(self.config.images_dir())
        try:
            self.library.purge_trash(int(self.config.get("library", "trash_days")))
        except Exception as exc:
            print(f"catcap: trash purge failed: {exc}")
        ui.cleanup_temp()
        self.backend = get_backend()
        from .hotkeys import HotkeyManager

        self.hotkeys = HotkeyManager(self.config)
        if self.config.get("hotkeys", "enabled"):
            self.hotkeys.apply()
        self.tray = None
        if os.environ.get("CATCAP_NO_TRAY") != "1":
            from .tray import Tray

            self.tray = Tray(self)
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

        offset = (0, 0)
        screen = None
        if snap.backend == "wayland":
            screen = QGuiApplication.primaryScreen()
            screens = QGuiApplication.screens()
            index = screens.index(screen) if screen in screens else 0
            _n, x, y, w, h = snap.monitors[index] if index < len(snap.monitors) else (
                "", 0, 0, snap.image.width(), snap.image.height())
            snap = Snapshot(image=snap.crop(x, y, w, h), scale=snap.scale, backend="wayland")
            offset = (x, y)

        def done(result):
            if result is None:
                self._end_capture()
                return
            x, y, w, h = result.rect
            self.config.set("state", "last_region", f"{x + offset[0]},{y + offset[1]},{w},{h}")
            info = type("Info", (), {"app": result.app, "title": result.title})()
            chosen = {"copy": "clipboard", "edit": "editor", "pin": "pin"}.get(result.action)
            self._store(result.image, result.mode, info, chosen or action)

        overlay = Overlay(snap, mode, self.config, done, screen)
        self._keep.append(overlay)
        overlay.destroyed.connect(lambda *_: self._keep.remove(overlay) if overlay in self._keep else None)
        overlay.start()

    def _store(self, image, mode, info, action, monitor=""):
        cid = self.library.add_capture(image, mode=mode, app=getattr(info, "app", "") if info else "",
                                       title=getattr(info, "title", "") if info else "", monitor=monitor)
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
        reply = QDBusConnection.sessionBus().call(msg, timeout=1500)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            if not (self.tray and self.tray.message(title, body)) and error:
                ui.error(None, title, body)

    # ================================================================ windows
    def show_library(self, select_id: str | None = None):
        from .library_window import LibraryWindow

        if self.library_window is None:
            self.library_window = LibraryWindow(self)
            self.library_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.library_window.destroyed.connect(lambda *_: setattr(self, "library_window", None))
        self.library_window.show()
        self.library_window.raise_()
        self.library_window.activateWindow()
        if select_id:
            QTimer.singleShot(200, lambda: self.library_window and self.library_window.select_capture(select_id))

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
        QMessageBox.about(parent, "About CatCap",
                          f"<h3>CatCap {__version__}</h3><p>Capture, annotate and organise screenshots on Linux Mint.</p>"
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


def _forward(payload: bytes) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(socket_name())
    if not sock.waitForConnected(400):
        return False
    sock.write(payload)
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    payload = json.dumps({"args": vars(args), "cwd": os.getcwd()}).encode()
    QApplication.setApplicationName(APP_NAME)
    QApplication.setDesktopFileName(APP_ID)
    qapp = QApplication([sys.argv[0]])
    qapp.setQuitOnLastWindowClosed(False)
    if _forward(payload):
        return 0
    if args.quit:
        return 0
    if args.release_hotkeys:
        from .hotkeys import HotkeyManager

        config = Config()
        config.set("hotkeys", "enabled", False)
        HotkeyManager(config).remove()
        print("CatCap hotkeys removed; Cinnamon's screenshot keys are restored.")
        return 0
    app = CatCap(qapp)
    server = QLocalServer()
    QLocalServer.removeServer(socket_name())
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    server.listen(socket_name())

    def on_connection():
        conn = server.nextPendingConnection()

        def read():
            if not conn.canReadLine() and conn.bytesAvailable() == 0:
                return
            data = bytes(conn.readAll())
            try:
                msg = json.loads(data.decode())
                app.handle(msg.get("args", {}), msg.get("cwd", str(Path.home())))
            except ValueError:
                pass

        conn.readyRead.connect(read)
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(on_connection)
    QTimer.singleShot(0, lambda: app.handle(vars(args), os.getcwd()))
    return qapp.exec()
