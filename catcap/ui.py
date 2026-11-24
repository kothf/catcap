"""Shared UI helpers: clipboard, export dialog, filenames, drag-and-drop, file manager."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import paths, render  # noqa: E402

URI_TARGET = Gtk.TargetEntry.new("text/uri-list", 0, 0)
IDS_TARGET = Gtk.TargetEntry.new("application/x-catcap-ids", Gtk.TargetFlags.SAME_APP, 1)

_SAFE = re.compile(r"[^\w\-. ]+", re.UNICODE)


def safe_name(text: str, fallback: str = "capture") -> str:
    text = _SAFE.sub("_", text).strip(" ._")
    return (text or fallback)[:120]


def format_filename(template: str, cap) -> str:
    """Expand a filename template (OUT-05).

    Named tokens: {app} {title} {name} {mode} {width} {height} {id}
    Any other {...} is a date pattern using yyyy MM dd HH mm ss.
    """
    t = time.localtime(cap.created)
    named = {
        "app": cap.app or "capture", "title": cap.title, "name": cap.name, "mode": cap.mode,
        "width": str(cap.width), "height": str(cap.height), "id": cap.id[:8],
        "date": time.strftime("%Y-%m-%d", t), "time": time.strftime("%H%M%S", t),
    }

    def expand(match):
        token = match.group(1)
        if token in named:
            return named[token]
        out = token
        for pat, fmt in (("yyyy", "%Y"), ("MM", "%m"), ("dd", "%d"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S")):
            out = out.replace(pat, time.strftime(fmt, t))
        return out

    return safe_name(re.sub(r"\{([^{}]+)\}", expand, template or "{app}_{yyyy-MM-dd}_{HHmmss}"))


def copy_surface(surface) -> None:
    pixbuf = render.surface_to_pixbuf(surface)
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    clipboard.set_image(pixbuf)
    clipboard.store()


def copy_capture(library, capture_id: str) -> None:
    copy_surface(render.render_capture(library, capture_id))


def temp_render(library, capture_id: str, config) -> Path:
    """Render to $XDG_RUNTIME_DIR/catcap for drag-and-drop / open-with / email."""
    cap = library.get(capture_id)
    name = format_filename(config.get("output", "filename_template"), cap)
    out = paths.runtime_dir() / f"{name}.png"
    render.save_surface(render.render_capture(library, capture_id), out, "png")
    return out


def cleanup_temp(max_age: float = 3600) -> None:
    now = time.time()
    try:
        for f in paths.runtime_dir().iterdir():
            if now - f.stat().st_mtime > max_age:
                f.unlink()
    except OSError:
        pass


def export_dialog(parent, library, config, ids: list[str]) -> None:
    """Export one capture (file dialog) or several (folder dialog)."""
    if not ids:
        return
    fmt = config.get("output", "default_format")
    quality = config.get("output", "jpg_quality")
    last_dir = config.get("output", "last_export_dir") or str(paths.pictures_dir())
    if len(ids) == 1:
        cap = library.get(ids[0])
        dialog = Gtk.FileChooserNative.new("Export capture", parent, Gtk.FileChooserAction.SAVE, "_Export", "_Cancel")
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_folder(last_dir)
        dialog.set_current_name(format_filename(config.get("output", "filename_template"), cap)
                                + render.EXPORT_FORMATS.get(fmt, ("", ".png"))[1])
        for key, (label, ext) in render.EXPORT_FORMATS.items():
            flt = Gtk.FileFilter()
            flt.set_name(f"{label} (*{ext})")
            flt.add_pattern(f"*{ext}")
            if key == "jpg":
                flt.add_pattern("*.jpeg")
            dialog.add_filter(flt)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            path = Path(dialog.get_filename())
            ext = path.suffix.lower().lstrip(".")
            if ext == "jpeg":
                ext = "jpg"
            if ext not in render.EXPORT_FORMATS:
                path = path.with_suffix(render.EXPORT_FORMATS.get(fmt, ("", ".png"))[1])
                ext = fmt
            try:
                render.save_surface(render.render_capture(library, ids[0]), path, ext, quality)
                config.set("output", "last_export_dir", str(path.parent))
            except Exception as exc:
                error_dialog(parent, "Export failed", str(exc))
        dialog.destroy()
        return
    dialog = Gtk.FileChooserNative.new(f"Export {len(ids)} captures to folder", parent,
                                       Gtk.FileChooserAction.SELECT_FOLDER, "_Export", "_Cancel")
    dialog.set_current_folder(last_dir)
    if dialog.run() == Gtk.ResponseType.ACCEPT:
        folder = Path(dialog.get_filename())
        ext = render.EXPORT_FORMATS.get(fmt, ("", ".png"))[1]
        try:
            used = set()
            for cid in ids:
                cap = library.get(cid)
                base = format_filename(config.get("output", "filename_template"), cap)
                name, n = base, 2
                while name in used or (folder / f"{name}{ext}").exists():
                    name, n = f"{base}_{n}", n + 1
                used.add(name)
                render.save_surface(render.render_capture(library, cid), folder / f"{name}{ext}", fmt, quality)
            config.set("output", "last_export_dir", str(folder))
        except Exception as exc:
            error_dialog(parent, "Export failed", str(exc))
    dialog.destroy()


def export_pdf_dialog(parent, library, config, ids: list[str]) -> None:
    dialog = Gtk.FileChooserNative.new("Export as PDF", parent, Gtk.FileChooserAction.SAVE, "_Export", "_Cancel")
    dialog.set_do_overwrite_confirmation(True)
    dialog.set_current_folder(config.get("output", "last_export_dir") or str(paths.pictures_dir()))
    dialog.set_current_name("captures.pdf" if len(ids) > 1 else
                            format_filename(config.get("output", "filename_template"), library.get(ids[0])) + ".pdf")
    if dialog.run() == Gtk.ResponseType.ACCEPT:
        path = Path(dialog.get_filename()).with_suffix(".pdf")
        try:
            render.save_pdf([render.render_capture(library, i) for i in ids], path)
            config.set("output", "last_export_dir", str(path.parent))
        except Exception as exc:
            error_dialog(parent, "Export failed", str(exc))
    dialog.destroy()


def show_in_folder(path: Path) -> None:
    uri = Gio.File.new_for_path(str(path)).get_uri()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                      "org.freedesktop.FileManager1", "ShowItems",
                      GLib.Variant("(ass)", ([uri], "")), None, Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(str(path.parent)).get_uri(), None)


def open_with(parent, path: Path) -> None:
    gfile = Gio.File.new_for_path(str(path))
    dialog = Gtk.AppChooserDialog.new(parent, Gtk.DialogFlags.MODAL, gfile)
    if dialog.run() == Gtk.ResponseType.OK:
        app = dialog.get_app_info()
        if app is not None:
            app.launch([gfile], None)
    dialog.destroy()


def send_email(path: Path) -> bool:
    try:
        subprocess.Popen(["xdg-email", "--attach", str(path)])
        return True
    except OSError:
        return False


def error_dialog(parent, title: str, text: str) -> None:
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.ERROR,
                            buttons=Gtk.ButtonsType.CLOSE, text=title)
    dlg.format_secondary_text(text)
    dlg.run()
    dlg.destroy()


def ask(parent, title: str, text: str, ok_label: str = "_OK", destructive: bool = False) -> bool:
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.QUESTION,
                            buttons=Gtk.ButtonsType.NONE, text=title)
    dlg.format_secondary_text(text)
    dlg.add_button("_Cancel", Gtk.ResponseType.CANCEL)
    btn = dlg.add_button(ok_label, Gtk.ResponseType.OK)
    if destructive:
        btn.get_style_context().add_class("destructive-action")
    dlg.set_default_response(Gtk.ResponseType.CANCEL)
    ok = dlg.run() == Gtk.ResponseType.OK
    dlg.destroy()
    return ok


def prompt_text(parent, title: str, label: str, value: str = "", multiline: bool = False) -> str | None:
    dlg = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dlg.add_button("_Cancel", Gtk.ResponseType.CANCEL)
    dlg.add_button("_OK", Gtk.ResponseType.OK)
    dlg.set_default_response(Gtk.ResponseType.OK)
    box = dlg.get_content_area()
    box.set_spacing(8)
    box.set_border_width(12)
    box.add(Gtk.Label(label=label, xalign=0))
    if multiline:
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(value)
        sw = Gtk.ScrolledWindow()
        sw.set_size_request(360, 120)
        sw.add(view)
        box.add(sw)

        def on_key(_w, ev):
            if ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and ev.state & Gdk.ModifierType.CONTROL_MASK:
                dlg.response(Gtk.ResponseType.OK)
                return True
            return False

        view.connect("key-press-event", on_key)
        box.add(Gtk.Label(label="Ctrl+Enter to confirm", xalign=0, sensitive=False))
    else:
        entry = Gtk.Entry(text=value, activates_default=True)
        box.add(entry)
    dlg.show_all()
    result = None
    if dlg.run() == Gtk.ResponseType.OK:
        if multiline:
            buf = view.get_buffer()
            result = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        else:
            result = entry.get_text()
    dlg.destroy()
    return result


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def human_date(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def setup_css() -> None:
    css = b"""
    .catcap-toolbar button { padding: 4px; min-width: 28px; min-height: 28px; }
    .catcap-sidebar-header { font-weight: bold; opacity: 0.7; padding: 8px 10px 2px 10px; font-size: 0.9em; }
    .catcap-sidebar row { padding: 4px 6px; }
    .catcap-preview-title { font-size: 1.15em; font-weight: bold; }
    .catcap-dim { opacity: 0.7; }
    .catcap-countdown { font-size: 64px; font-weight: bold; color: white; background-color: rgba(20,20,24,0.85);
                        border-radius: 16px; padding: 12px 36px; }
    .catcap-quickbar { background-color: rgba(30,30,34,0.95); border-radius: 10px; padding: 8px; }
    .catcap-quickbar-text { color: white; }
    .catcap-canvas { background-color: #3a3a3f; }
    .catcap-drag { padding: 2px 8px; border-radius: 4px; }
    .catcap-drag:hover { background-color: alpha(@theme_fg_color, 0.1); }
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def file_uri(path: Path) -> str:
    return Gio.File.new_for_path(str(path)).get_uri()


def is_image_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
