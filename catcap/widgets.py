"""Small windows: pin-to-screen, quick toolbar, delay countdown, shortcuts help."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import render, ui  # noqa: E402
from .capture import is_wayland  # noqa: E402


class PinWindow(Gtk.Window):
    """Always-on-top borderless image (OUT-08). Scroll to zoom, drag to move, Esc to close."""

    def __init__(self, app, surface: cairo.ImageSurface):
        super().__init__(title="CatCap pin")
        self.app = app
        self.surface = surface
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(False)
        self.set_icon_name("io.github.kothf.CatCap")
        mon = Gdk.Display.get_default().get_monitor(0).get_workarea()
        self.scale = min(1.0, mon.width * 0.6 / surface.get_width(), mon.height * 0.6 / surface.get_height())
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._draw)
        self.add(self.area)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("button-press-event", self._press)
        self.connect("scroll-event", self._scroll)
        self.connect("key-press-event", self._key)
        self.set_tooltip_text("Drag to move · scroll to zoom · double-click or Esc to close · right-click for menu")
        self._resize()
        self.show_all()

    def _resize(self):
        self.resize(max(24, int(self.surface.get_width() * self.scale) + 2),
                    max(24, int(self.surface.get_height() * self.scale) + 2))
        self.area.queue_draw()

    def _draw(self, _w, cr):
        cr.set_source_rgb(0.35, 0.6, 0.95)
        cr.paint()
        cr.translate(1, 1)
        cr.scale(self.scale, self.scale)
        cr.set_source_surface(self.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()

    def _press(self, _w, ev):
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            self.destroy()
        elif ev.button == 1:
            self.begin_move_drag(ev.button, int(ev.x_root), int(ev.y_root), ev.time)
        elif ev.button == 3:
            menu = Gtk.Menu()
            for label, cb in (("Copy", lambda: ui.copy_surface(self.surface)),
                              ("Actual size", lambda: self._set_scale(1.0)),
                              ("Opacity 100%", lambda: self.set_opacity(1.0)),
                              ("Opacity 70%", lambda: self.set_opacity(0.7)),
                              ("Opacity 40%", lambda: self.set_opacity(0.4)),
                              ("Close", self.destroy)):
                item = Gtk.MenuItem(label=label)
                item.connect("activate", lambda _i, c=cb: c())
                menu.append(item)
            menu.show_all()
            menu.attach_to_widget(self, None)
            menu.popup_at_pointer(ev)
        return True

    def _set_scale(self, s):
        self.scale = max(0.05, min(s, 8.0))
        self._resize()

    def _scroll(self, _w, ev):
        ok, _dx, dy = ev.get_scroll_deltas()
        up = (dy < 0) if ok and dy else ev.direction == Gdk.ScrollDirection.UP
        self._set_scale(self.scale * (1.1 if up else 1 / 1.1))
        return True

    def _key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.destroy()
        return True


class QuickBar(Gtk.Window):
    """Post-capture toolbar (OVL-08): Copy, Edit, Pin, Library, Discard. Closes after a few seconds."""

    TIMEOUT = 8

    def __init__(self, app, capture_id: str):
        super().__init__(type=Gtk.WindowType.TOPLEVEL, title="CatCap")
        self.app = app
        self.capture_id = capture_id
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self._hover = False
        self._timer = 0
        box = Gtk.Box(spacing=8)
        box.get_style_context().add_class("catcap-quickbar")
        cap = app.library.get(capture_id)
        try:
            thumb = render.render_thumbnail(app.library, capture_id, 72)
            box.add(Gtk.Image.new_from_pixbuf(thumb))
        except Exception:
            pass
        text = Gtk.Label(label=f"Saved · {cap.width} × {cap.height}", xalign=0)
        text.get_style_context().add_class("catcap-quickbar-text")
        box.add(text)
        for label, cb, suggested in (("Copy", self._copy, True), ("Edit", self._edit, False),
                                     ("Pin", self._pin, False), ("Library", self._library, False),
                                     ("Discard", self._discard, False)):
            btn = Gtk.Button(label=label)
            if suggested:
                btn.get_style_context().add_class("suggested-action")
            if label == "Discard":
                btn.set_tooltip_text("Delete this capture")
            btn.connect("clicked", lambda _b, c=cb: c())
            box.add(btn)
        close = Gtk.Button(image=Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON),
                           relief=Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda *_: self.destroy())
        box.add(close)
        ev = Gtk.EventBox()
        ev.add(box)
        ev.connect("enter-notify-event", lambda *_: self._set_hover(True))
        ev.connect("leave-notify-event", lambda *_: self._set_hover(False))
        self.add(ev)
        self.show_all()
        GLib.idle_add(self._place)
        self._timer = GLib.timeout_add_seconds(self.TIMEOUT, self._expire)
        self.connect("destroy", lambda *_: self._timer and GLib.source_remove(self._timer))

    def _place(self):
        area = Gdk.Display.get_default().get_monitor(0).get_workarea()
        w, h = self.get_size()
        self.move(area.x + area.width - w - 24, area.y + area.height - h - 24)
        return False

    def _set_hover(self, value):
        self._hover = value
        return False

    def _expire(self):
        if self._hover:
            return True
        self._timer = 0
        self.destroy()
        return False

    def _copy(self):
        ui.copy_capture(self.app.library, self.capture_id)
        self.destroy()

    def _edit(self):
        self.destroy()
        self.app.open_editor(self.capture_id)

    def _pin(self):
        self.app.pin_surface(render.render_capture(self.app.library, self.capture_id))
        self.destroy()

    def _library(self):
        self.destroy()
        self.app.show_library(self.capture_id)

    def _discard(self):
        self.app.library.delete_permanently([self.capture_id])
        self.destroy()


class Countdown(Gtk.Window):
    """Visible delay countdown (OPT-01)."""

    def __init__(self, seconds: int, on_done, on_cancel=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL if is_wayland() else Gtk.WindowType.POPUP)
        self.remaining = seconds
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.connect("draw", lambda _w, cr: (cr.set_source_rgba(0, 0, 0, 0), cr.set_operator(cairo.OPERATOR_SOURCE),
                                              cr.paint(), False)[-1])
        self.label = Gtk.Label()
        self.label.get_style_context().add_class("catcap-countdown")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add(self.label)
        self.add(box)
        self.connect("key-press-event", self._key)
        self._update()
        self.show_all()
        area = Gdk.Display.get_default().get_monitor(0).get_geometry()
        w, _h = self.get_size()
        self.move(area.x + (area.width - w) // 2, area.y + 60)
        GLib.timeout_add_seconds(1, self._tick)

    def _update(self):
        self.label.set_text(str(self.remaining))

    def _tick(self):
        self.remaining -= 1
        if self.remaining <= 0:
            self.destroy()
            GLib.timeout_add(250, lambda: (self.on_done(), False)[1])
            return False
        self._update()
        return True

    def _key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.destroy()
            if self.on_cancel:
                self.on_cancel()
        return True


SHORTCUTS = [
    ("Capture overlay", [
        ("Drag", "Select a region"), ("Click", "Capture the window under the cursor"),
        ("Arrow keys", "Move pointer or selection by 1 px (Shift: 10 px)"),
        ("Ctrl+Arrow keys", "Resize the selection"), ("Enter / double-click", "Capture"),
        ("F or Ctrl+A", "Select the whole screen"), ("C", "Copy colour under cursor"),
        ("M", "Toggle magnifier"), ("Esc / right-click", "Cancel")]),
    ("Editor", [
        ("V A L R E T O H P S B X G C", "Tools: select, arrow, line, rect, ellipse, text, callout, "
                                        "highlighter, pen, step, blur, pixelate, spotlight, crop"),
        ("Ctrl+Z / Ctrl+Shift+Z", "Undo / redo"), ("Delete", "Delete selected annotation"),
        ("Ctrl+D", "Duplicate"), ("Ctrl+] / Ctrl+[", "Bring to front / send to back"),
        ("Shift while drawing", "Straight 45° lines, squares, circles"),
        ("Ctrl+C", "Copy image"), ("Ctrl+S", "Export"), ("Ctrl+scroll, Ctrl+0, Ctrl+1", "Zoom, fit, 100%"),
        ("Middle-drag / Alt+drag", "Pan"), ("Enter / Esc (crop)", "Apply / cancel crop")]),
    ("Library", [
        ("Ctrl+F", "Search"), ("Ctrl+N", "New region capture"), ("Enter / double-click", "Edit"),
        ("Ctrl+C", "Copy image"), ("Ctrl+V", "New capture from clipboard"), ("Ctrl+E", "Export"),
        ("Delete / Shift+Delete", "Move to trash / delete permanently"), ("F2", "Rename")]),
]


def show_shortcuts(parent):
    dlg = Gtk.Dialog(title="Keyboard shortcuts", transient_for=parent, modal=False)
    dlg.add_button("_Close", Gtk.ResponseType.CLOSE)
    dlg.connect("response", lambda d, _r: d.destroy())
    sw = Gtk.ScrolledWindow()
    sw.set_size_request(620, 520)
    grid = Gtk.Grid(column_spacing=18, row_spacing=6, margin=16)
    row = 0
    for section, items in SHORTCUTS:
        head = Gtk.Label(xalign=0, margin_top=8 if row else 0)
        head.set_markup(f"<b>{section}</b>")
        grid.attach(head, 0, row, 2, 1)
        row += 1
        for keys, desc in items:
            k = Gtk.Label(xalign=0)
            k.set_markup(f"<tt>{GLib.markup_escape_text(keys)}</tt>")
            grid.attach(k, 0, row, 1, 1)
            grid.attach(Gtk.Label(label=desc, xalign=0, wrap=True, max_width_chars=50), 1, row, 1, 1)
            row += 1
    sw.add(grid)
    dlg.get_content_area().pack_start(sw, True, True, 0)
    dlg.show_all()
    return dlg
