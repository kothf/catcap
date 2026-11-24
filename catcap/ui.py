"""Shared UI helpers: clipboard, export dialogs, filenames, drag-and-drop, file manager, Open with."""

from __future__ import annotations

import configparser
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from PyQt6.QtCore import QMimeData, QUrl
from PyQt6.QtDBus import QDBusConnection, QDBusMessage
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMenu, QMessageBox

from . import paths, render

IDS_MIME = "application/x-catcap-ids"
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


# ------------------------------------------------------------------ clipboard / temp files
def copy_image(image) -> None:
    QGuiApplication.clipboard().setImage(image)


def copy_capture(library, capture_id: str) -> None:
    copy_image(render.render_capture(library, capture_id))


def temp_render(library, capture_id: str, config) -> Path:
    """Render to $XDG_RUNTIME_DIR/catcap for drag-and-drop / open-with / email."""
    cap = library.get(capture_id)
    # One folder per capture: the file keeps the friendly template name for the drop target, and
    # two captures whose names come out the same (same app, same second) can't overwrite each other.
    folder = paths.ensure_dir(paths.runtime_dir() / "render" / capture_id)
    out = folder / f"{format_filename(config.get('output', 'filename_template'), cap)}.png"
    render.save_image(render.render_capture(library, capture_id), out, "png")
    return out


def drag_mime(library, ids, config, with_ids: bool = True) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(temp_render(library, i, config))) for i in ids])
    if with_ids:
        mime.setData(IDS_MIME, "\n".join(ids).encode())
    return mime


def cleanup_temp(max_age: float = 3600) -> None:
    """Delete rendered temp files older than max_age (the instance socket is left alone)."""
    now = time.time()
    root = paths.runtime_dir()
    try:
        for f in root.iterdir():   # files from older versions that rendered straight into root
            if f.suffix == ".png" and f.is_file() and now - f.stat().st_mtime > max_age:
                f.unlink()
        render_dir = root / "render"
        if render_dir.is_dir():
            for folder in render_dir.iterdir():
                for f in folder.iterdir():
                    if now - f.stat().st_mtime > max_age:
                        f.unlink()
                try:
                    folder.rmdir()
                except OSError:
                    pass
    except OSError:
        pass


# ------------------------------------------------------------------ export
def export_dialog(parent, library, config, ids: list[str]) -> None:
    if not ids:
        return
    fmt = config.get("output", "default_format")
    quality = config.get("output", "jpg_quality")
    last_dir = config.get("output", "last_export_dir") or str(paths.pictures_dir())
    template = config.get("output", "filename_template")
    formats = render.supported_formats()
    if len(ids) == 1:
        cap = library.get(ids[0])
        filters = [f"{render.EXPORT_FORMATS[k][0]} (*{render.EXPORT_FORMATS[k][1]})" for k in formats]
        default_filter = next((f for f, k in zip(filters, formats) if k == fmt), filters[0])
        suggested = os.path.join(last_dir, format_filename(template, cap) + render.EXPORT_FORMATS.get(fmt, ("", ".png"))[1])
        path, chosen = QFileDialog.getSaveFileName(parent, "Export capture", suggested, ";;".join(filters), default_filter)
        if not path:
            return
        path = Path(path)
        ext = path.suffix.lower().lstrip(".")
        ext = "jpg" if ext == "jpeg" else ext
        if ext not in render.EXPORT_FORMATS:
            ext = formats[filters.index(chosen)] if chosen in filters else fmt
            path = path.with_suffix(render.EXPORT_FORMATS[ext][1])
        try:
            render.save_image(render.render_capture(library, ids[0]), path, ext, quality)
            config.set("output", "last_export_dir", str(path.parent))
        except Exception as exc:
            error(parent, "Export failed", str(exc))
        return
    folder = QFileDialog.getExistingDirectory(parent, f"Export {len(ids)} captures to folder", last_dir)
    if not folder:
        return
    folder = Path(folder)
    ext = render.EXPORT_FORMATS.get(fmt, ("", ".png"))[1]
    try:
        used = set()
        for cid in ids:
            base = format_filename(template, library.get(cid))
            name, n = base, 2
            while name in used or (folder / f"{name}{ext}").exists():
                name, n = f"{base}_{n}", n + 1
            used.add(name)
            render.save_image(render.render_capture(library, cid), folder / f"{name}{ext}", fmt, quality)
        config.set("output", "last_export_dir", str(folder))
    except Exception as exc:
        error(parent, "Export failed", str(exc))


def export_pdf_dialog(parent, library, config, ids: list[str]) -> None:
    if not ids:
        return
    last_dir = config.get("output", "last_export_dir") or str(paths.pictures_dir())
    name = "captures.pdf" if len(ids) > 1 else format_filename(config.get("output", "filename_template"),
                                                                 library.get(ids[0])) + ".pdf"
    path, _ = QFileDialog.getSaveFileName(parent, "Export as PDF", os.path.join(last_dir, name), "PDF (*.pdf)")
    if not path:
        return
    path = Path(path).with_suffix(".pdf")
    try:
        render.save_pdf([render.render_capture(library, i) for i in ids], path)
        config.set("output", "last_export_dir", str(path.parent))
    except Exception as exc:
        error(parent, "Export failed", str(exc))


# ------------------------------------------------------------------ desktop integration
def show_in_folder(path: Path) -> None:
    msg = QDBusMessage.createMethodCall("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                                        "org.freedesktop.FileManager1", "ShowItems")
    msg.setArguments([[QUrl.fromLocalFile(str(path)).toString()], ""])
    reply = QDBusConnection.sessionBus().call(msg)
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def image_apps() -> list[tuple[str, str]]:
    """(name, Exec) of installed apps that open PNG images, for the Open with menu."""
    dirs = [Path(p) / "applications" for p in
            [os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))]
            + os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")]
    seen, apps = set(), []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.desktop")):
            if f.name in seen or f.name.startswith("io.github.kothf.CatCap"):
                continue
            seen.add(f.name)
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(f, encoding="utf-8")
                entry = cp["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if entry.get("NoDisplay", "false") == "true" or "image/png" not in entry.get("MimeType", ""):
                continue
            apps.append((entry.get("Name", f.stem), entry.get("Exec", "")))
    return sorted(apps, key=lambda a: a[0].lower())


def launch(exec_line: str, path: Path) -> None:
    args = []
    for token in shlex.split(exec_line):
        if token in ("%f", "%F", "%u", "%U"):
            args.append(str(path))
        elif token.startswith("%"):
            continue
        else:
            args.append(token)
    if str(path) not in args:
        args.append(str(path))
    subprocess.Popen(args, start_new_session=True)


def open_with_menu(parent, path_factory) -> QMenu:
    menu = QMenu("Open with", parent)
    apps = image_apps()
    default = menu.addAction("Default image viewer")
    default.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path_factory()))))
    if apps:
        menu.addSeparator()
    for name, exec_line in apps:
        act = menu.addAction(name)
        act.triggered.connect(lambda _c=False, e=exec_line: launch(e, path_factory()))
    return menu


def send_email(path: Path) -> bool:
    try:
        subprocess.Popen(["xdg-email", "--attach", str(path)], start_new_session=True)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ dialogs
def error(parent, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Icon.Warning, title, title, QMessageBox.StandardButton.Close, parent)
    box.setInformativeText(text)
    box.exec()


def ask(parent, title: str, text: str, ok_label: str = "OK", destructive: bool = False) -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, title, QMessageBox.StandardButton.NoButton, parent)
    box.setInformativeText(text)
    ok = box.addButton(ok_label, QMessageBox.ButtonRole.AcceptRole)
    if destructive:
        ok.setProperty("danger", True)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is ok


def prompt_text(parent, title: str, label: str, value: str = "", multiline: bool = False) -> str | None:
    if not multiline:
        text, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, value)
        return text if ok else None
    from PyQt6.QtGui import QKeySequence, QShortcut
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(420, 220)
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(label))
    edit = QPlainTextEdit(value)
    lay.addWidget(edit)
    hint = QLabel("Ctrl+Enter adds it. Enter starts a new line.")
    hint.setObjectName("muted")
    lay.addWidget(hint)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    ok = buttons.addButton("Add" if not value else "Save", QDialogButtonBox.ButtonRole.AcceptRole)
    ok.setProperty("accent", True)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    for seq in ("Ctrl+Return", "Ctrl+Enter"):
        QShortcut(QKeySequence(seq), dlg).activated.connect(dlg.accept)
    edit.setFocus()
    return edit.toPlainText() if dlg.exec() else None


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def human_date(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def is_image_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
