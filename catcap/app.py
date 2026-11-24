"""CatCap application: single instance, CLI, tray, capture workflow."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, __version__, paths, ui  # noqa: E402
from .capture import Snapshot, get_backend, is_wayland  # noqa: E402
from .config import Config  # noqa: E402
from .library import Library  # noqa: E402

CAPTURE_FLAGS = {"region": "region", "window": "window", "full": "full", "active": "active",
                 "monitor": "monitor", "last-region": "last_region"}


class CatCapApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        GLib.set_application_name(APP_NAME)
        GLib.set_prgname(APP_ID)
        self.config = None
        self.library = None
        self.backend = None
        self.tray = None
        self.hotkeys = None
        self.library_window = None
        self.settings_window = None
        self.editors = {}
        self._capturing = False
        self._hidden = []
        self._add_options()

    # ================================================================ CLI
    def _add_options(self):
        none = GLib.OptionArg.NONE
        for name, desc in (("region", "Capture a region or window"),
                           ("window", "Capture a window"),
                           ("full", "Capture the whole screen (all monitors)"),
                           ("monitor", "Capture the monitor under the pointer"),
                           ("active", "Capture the active window"),
                           ("last-region", "Capture the last selected region again"),
                           ("library", "Open the library"),
                           ("settings", "Open settings"),
                           ("background", "Start in the tray without opening a window"),
                           ("clipboard", "After capture: copy to clipboard"),
                           ("edit", "After capture: open in the editor"),
                           ("save-only", "After capture: only save to the library"),
                           ("cursor", "Include the mouse pointer"),
                           ("no-cursor", "Do not include the mouse pointer"),
                           ("quit", "Quit the running CatCap"),
                           ("version", "Show version")):
            self.add_main_option(name, 0, GLib.OptionFlags.NONE, none, desc, None)
        self.add_main_option("delay", ord("d"), GLib.OptionFlags.NONE, GLib.OptionArg.INT,
                             "Wait N seconds before capturing", "N")
        self.add_main_option(GLib.OPTION_REMAINING, 0, GLib.OptionFlags.NONE, GLib.OptionArg.FILENAME_ARRAY,
                             "Image files to open in the editor", "[FILE…]")

    def do_handle_local_options(self, options):
        if options.contains("version"):
            print(f"{APP_NAME} {__version__}")
            return 0
        return -1

    def do_command_line(self, command_line):
        opts = command_line.get_options_dict().end().unpack()
        if opts.get("quit"):
            self.quit()
            return 0
        action = None
        if opts.get("clipboard"):
            action = "clipboard"
        elif opts.get("edit"):
            action = "editor"
        elif opts.get("save-only"):
            action = "library"
        cursor = True if opts.get("cursor") else (False if opts.get("no-cursor") else None)
        delay = opts.get("delay")
        files = opts.get(GLib.OPTION_REMAINING) or []
        cwd = command_line.get_cwd() or os.getcwd()
        did_something = False
        for flag, mode in CAPTURE_FLAGS.items():
            if opts.get(flag):
                self.start_capture(mode, delay=delay, action=action, cursor=cursor)
                did_something = True
                break
        for f in files:
            path = f.decode() if isinstance(f, bytes) else f
            path = os.path.join(cwd, path)
            try:
                cid = self.library.import_file(path)
                self.open_editor(cid)
            except Exception as exc:
                print(f"catcap: cannot open {path}: {exc}", file=sys.stderr)
            did_something = True
        if opts.get("settings"):
            self.show_settings()
            did_something = True
        if opts.get("library"):
            self.show_library()
            did_something = True
        if not did_something and not opts.get("background"):
            self.show_library()
            self._maybe_first_run()
        return 0

    # ================================================================ lifecycle
    def do_startup(self):
        Gtk.Application.do_startup(self)
        ui.setup_css()
        root = paths.source_root()
        if root is not None:
            Gtk.IconTheme.get_default().append_search_path(str(root / "data" / "icons"))
        Gtk.Window.set_default_icon_name(APP_ID)
        self.config = Config()
        self.library = Library(self.config.images_dir())
        try:
            self.library.purge_trash(int(self.config.get("library", "trash_days")))
        except Exception as exc:
            print(f"catcap: trash purge failed: {exc}")
        ui.cleanup_temp()
        self.backend = get_backend()
        from .hotkeys import HotkeyManager
        from .tray import Tray

        self.hotkeys = HotkeyManager(self)
        if self.config.get("hotkeys", "enabled"):
            try:
                self.hotkeys.apply()
            except Exception as exc:
                print(f"catcap: hotkeys: {exc}")
        if os.environ.get("CATCAP_NO_TRAY") != "1":
            self.tray = Tray(self)
        show = Gio.SimpleAction.new("show-capture", GLib.VariantType.new("s"))
        show.connect("activate", lambda _a, v: self.show_library(v.get_string()))
        self.add_action(show)
        self.hold()

    def do_shutdown(self):
        if self.library is not None:
            self.library.close()
        Gtk.Application.do_shutdown(self)

    # ================================================================ capture workflow
    def start_capture(self, mode: str, delay: int | None = None, action: str | None = None,
                      hide=None, cursor: bool | None = None) -> None:
        if self._capturing:
            return
        self._capturing = True
        if delay is None:
            delay = int(self.config.get("capture", "delay"))
        self._hidden = []
        if hide is not None:
            for win in self.get_windows():
                if win.get_visible() and win.get_window_type() == Gtk.WindowType.TOPLEVEL:
                    win.hide()
                    self._hidden.append(win)
        wait = 350 if self._hidden else 180

        def go():
            GLib.timeout_add(wait, self._grab, mode, action, cursor)

        if delay and delay > 0:
            from .widgets import Countdown

            Countdown(delay, go, on_cancel=self._end_capture)
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
        return False

    def _handle_snapshot(self, snap: Snapshot, mode: str, action):
        if mode == "full" or (self.backend.name == "wayland" and mode in ("window", "active")):
            info = snap.active
            self._store(snap.pixbuf, "full" if mode == "full" else mode, info, action)
        elif mode == "monitor":
            mon = snap.monitor_at(*snap.pointer)
            name, x, y, w, h = mon if mon else ("", 0, 0, snap.pixbuf.get_width(), snap.pixbuf.get_height())
            self._store(snap.crop(x, y, w, h), "monitor", None, action, monitor=name)
        elif mode == "active":
            win = snap.active
            if win is None:
                self._store(snap.pixbuf, "full", None, action)
            else:
                x, y, w, h = win.rect(bool(self.config.get("capture", "window_decorations")))
                self._store(snap.crop(x, y, w, h), "active", win, action)
        elif mode == "last_region" and self._last_region():
            x, y, w, h = self._last_region()
            self._store(snap.crop(x, y, w, h), "last_region", snap.window_at(x + w / 2, y + h / 2), action)
        else:
            self._open_overlay(snap, "window" if mode == "window" else "region", action)

    def _last_region(self):
        raw = self.config.get("state", "last_region")
        try:
            x, y, w, h = [int(v) for v in raw.split(",")]
            return (x, y, w, h) if w > 0 and h > 0 else None
        except ValueError:
            return None

    def _open_overlay(self, snap: Snapshot, mode: str, action):
        from .overlay import Overlay

        monitor_index = 0
        offset = (0, 0)
        if snap.backend == "wayland":
            display = Gdk.Display.get_default()
            primary = display.get_primary_monitor() or display.get_monitor(0)
            for i in range(display.get_n_monitors()):
                if display.get_monitor(i) == primary:
                    monitor_index = i
            _n, x, y, w, h = snap.monitors[monitor_index] if snap.monitors else (
                "", 0, 0, snap.pixbuf.get_width(), snap.pixbuf.get_height())
            snap = Snapshot(pixbuf=snap.crop(x, y, w, h), scale=snap.scale, backend="wayland")
            offset = (x, y)

        def done(result):
            if result is None:
                self._end_capture()
                return
            x, y, w, h = result.rect
            self.config.set("state", "last_region", f"{x + offset[0]},{y + offset[1]},{w},{h}")
            info = type("Info", (), {"app": result.app, "title": result.title})()
            chosen = {"copy": "clipboard", "edit": "editor", "pin": "pin", "save": "library"}.get(result.action)
            self._store(result.pixbuf, result.mode, info, chosen or action)

        Overlay(snap, mode, self.config, done, monitor_index).start()

    def _store(self, pixbuf, mode, info, action, monitor=""):
        app_name = getattr(info, "app", "") if info else ""
        title = getattr(info, "title", "") if info else ""
        cid = self.library.add_capture(pixbuf, mode=mode, app=app_name, title=title, monitor=monitor)
        self._play_sound()
        action = action or self.config.get("capture", "after_action")
        self._end_capture(keep_hidden=action == "editor")
        if action == "editor":
            self.open_editor(cid)
        elif action == "clipboard":
            ui.copy_capture(self.library, cid)
            self.notify("Copied to clipboard", f"{pixbuf.get_width()} × {pixbuf.get_height()} · saved in library", cid)
        elif action == "pin":
            from . import render

            self.pin_surface(render.render_capture(self.library, cid))
        elif action == "library":
            self.notify("Capture saved", f"{pixbuf.get_width()} × {pixbuf.get_height()}", cid)
        else:
            from .widgets import QuickBar

            QuickBar(self, cid)
        return cid

    def _end_capture(self, keep_hidden=False):
        self._capturing = False
        for win in self._hidden:
            try:
                win.show()
                if not keep_hidden:
                    win.present()
            except Exception:
                pass
        self._hidden = []

    def _play_sound(self):
        if not self.config.get("general", "sound"):
            return
        if shutil.which("canberra-gtk-play"):
            subprocess.Popen(["canberra-gtk-play", "-i", "camera-shutter"], stderr=subprocess.DEVNULL)
            return
        oga = "/usr/share/sounds/freedesktop/stereo/camera-shutter.oga"
        if os.path.exists(oga) and shutil.which("paplay"):
            subprocess.Popen(["paplay", oga], stderr=subprocess.DEVNULL)

    def notify(self, title, body, capture_id=None, error=False):
        if not error and not self.config.get("general", "notifications"):
            return
        n = Gio.Notification.new(title)
        n.set_body(body)
        if capture_id:
            n.set_default_action(f"app.show-capture('{capture_id}')")
        self.send_notification(None, n)
        if error:
            print(f"catcap: {title}: {body}", file=sys.stderr)

    # ================================================================ windows
    def show_library(self, select_id: str | None = None):
        from .library_window import LibraryWindow

        if self.library_window is None:
            self.library_window = LibraryWindow(self)
            self.library_window.connect("destroy", self._on_library_destroy)
        self.library_window.present()
        if select_id:
            GLib.timeout_add(150, lambda: (self.library_window and self.library_window.select_capture(select_id), False)[1])

    def _on_library_destroy(self, *_):
        self.library_window = None

    def open_editor(self, capture_id: str):
        from .editor import Editor

        editor = self.editors.get(capture_id)
        if editor is None:
            editor = Editor(self, capture_id)
            self.editors[capture_id] = editor
            editor.connect("destroy", lambda *_: self.editors.pop(capture_id, None))
        editor.present()
        return editor

    def pin_surface(self, surface):
        from .widgets import PinWindow

        win = PinWindow(self, surface)
        self.add_window(win)
        win.present()

    def show_settings(self, parent=None):
        from .settings_window import SettingsWindow

        if self.settings_window is None:
            self.settings_window = SettingsWindow(self, parent)
            self.add_window(self.settings_window)
            self.settings_window.connect("destroy", lambda *_: setattr(self, "settings_window", None))
        self.settings_window.present()

    def show_shortcuts(self, parent=None):
        from .widgets import show_shortcuts

        show_shortcuts(parent)

    def show_about(self, parent=None):
        dlg = Gtk.AboutDialog(transient_for=parent, modal=True)
        dlg.set_program_name(APP_NAME)
        dlg.set_version(__version__)
        dlg.set_logo_icon_name(APP_ID)
        dlg.set_comments("Screenshot capture, annotation and library for Linux Mint.")
        dlg.set_website("https://github.com/kothf/catcap")
        dlg.set_license_type(Gtk.License.MIT_X11)
        dlg.run()
        dlg.destroy()

    def update_autostart(self):
        target = paths.autostart_dir() / f"{APP_ID}.desktop"
        if self.config.get("general", "start_at_login"):
            paths.ensure_dir(target.parent, private=False)
            target.write_text(
                "[Desktop Entry]\nType=Application\nName=CatCap\nComment=Screenshot tool (tray)\n"
                f"Exec={paths.launcher()} --background\nIcon={APP_ID}\nTerminal=false\n"
                "X-GNOME-Autostart-enabled=true\nNoDisplay=false\n", encoding="utf-8")
        else:
            try:
                target.unlink()
            except FileNotFoundError:
                pass

    # ================================================================ first run
    def _maybe_first_run(self):
        if self.config.get("general", "first_run_done"):
            return
        self.config.set("general", "first_run_done", True)
        from .hotkeys import is_cinnamon

        parent = self.library_window
        dlg = Gtk.Dialog(title="Welcome to CatCap", transient_for=parent, modal=True)
        dlg.add_button("_Start", Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(12)
        box.set_border_width(18)
        title = Gtk.Label(xalign=0)
        title.set_markup("<big><b>Welcome to CatCap</b></big>")
        box.add(title)
        box.add(Gtk.Label(label="A few choices to get started. You can change them later in Settings.",
                          xalign=0, wrap=True))
        print_check = Gtk.CheckButton(label="Use Print Screen and the other screenshot keys for CatCap",
                                      active=True)
        if not is_cinnamon():
            print_check.set_label("Enable global hotkeys (Print Screen and friends)")
        box.add(print_check)
        login_check = Gtk.CheckButton(label="Start CatCap at login (tray icon)", active=True)
        box.add(login_check)
        row = Gtk.Box(spacing=10)
        row.add(Gtk.Label(label="After a capture:"))
        combo = Gtk.ComboBoxText()
        from .settings_window import AFTER_ACTIONS

        for k, text in AFTER_ACTIONS:
            combo.append(k, text)
        combo.set_active_id(self.config.get("capture", "after_action"))
        row.add(combo)
        box.add(row)
        if is_wayland():
            note = Gtk.Label(label="You are in a Wayland session: screenshots go through the desktop's "
                                   "screenshot portal, which may ask for permission the first time.",
                             xalign=0, wrap=True, max_width_chars=60)
            box.add(note)
        dlg.show_all()
        dlg.run()
        self.config.set("hotkeys", "enabled", print_check.get_active())
        self.config.set("general", "start_at_login", login_check.get_active())
        self.config.set("capture", "after_action", combo.get_active_id() or "toolbar")
        dlg.destroy()
        self.update_autostart()
        warnings = self.hotkeys.apply()
        if warnings:
            ui.error_dialog(parent, "Hotkeys need attention", "\n".join(warnings))


def main(argv=None) -> int:
    app = CatCapApp()
    return app.run(argv if argv is not None else sys.argv)
