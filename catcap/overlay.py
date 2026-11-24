"""Frozen-screen selection overlay (CAP-03/CAP-04, OVL-01..08)."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .capture import Snapshot, WindowInfo  # noqa: E402
from .render import pixbuf_to_surface  # noqa: E402

HANDLE = 9          # logical px
MIN_DRAG = 4        # logical px
ACCENT = (0.33, 0.66, 0.97)   # Mint-ish blue
ACTIONS = [("capture", "✔  Capture"), ("copy", "Copy"), ("edit", "Edit"), ("pin", "Pin"), ("cancel", "✕")]


class SelectionResult:
    def __init__(self, pixbuf, rect, app="", title="", action=None, mode="region"):
        self.pixbuf = pixbuf
        self.rect = rect
        self.app = app
        self.title = title
        self.action = action
        self.mode = mode


class Overlay(Gtk.Window):
    """Full-screen window that shows the frozen snapshot and lets the user pick an area.

    `snap` must cover exactly the area this window covers (whole X11 screen, or one
    monitor on Wayland). All selection math is in image pixels; drawing converts.
    """

    def __init__(self, snap: Snapshot, mode: str, config, on_done, monitor_index: int = 0):
        wayland = snap.backend == "wayland"
        super().__init__(type=Gtk.WindowType.TOPLEVEL if wayland else Gtk.WindowType.POPUP)
        self.snap = snap
        self.mode = mode
        self.config = config
        self.on_done = on_done
        self.s = snap.scale or 1.0
        self.surface = pixbuf_to_surface(snap.pixbuf)
        self.img_w, self.img_h = snap.pixbuf.get_width(), snap.pixbuf.get_height()
        self._pixels = snap.pixbuf.get_pixels()
        self._stride = snap.pixbuf.get_rowstride()
        self._nch = snap.pixbuf.get_n_channels()

        self.state = "idle"
        self.sel: list[float] | None = None
        self.anchor = (0.0, 0.0)
        self.drag_from = (0.0, 0.0)
        self.drag_sel = None
        self.handle = None
        self.hover: WindowInfo | None = None
        self.chosen_window: WindowInfo | None = None
        self.pointer = tuple(snap.pointer)
        self.magnifier = bool(config.get("capture", "magnifier"))
        self.zoom = max(2, int(config.get("capture", "magnifier_zoom")))
        self.snap_windows = bool(config.get("capture", "snap_to_windows")) or mode == "window"
        self.decorations = bool(config.get("capture", "window_decorations"))
        self.toast = ""
        self.action_rects: list[tuple[str, tuple]] = []
        self.hover_action = None
        self._finished = False
        self._grab_seat = None

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_title("CatCap selection")
        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.KEY_PRESS_MASK
                        | Gdk.EventMask.SCROLL_MASK)
        self.connect("draw", self._on_draw)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("key-press-event", self._on_key)
        self.connect("map-event", self._on_map)
        self.connect("delete-event", lambda *_: (self._cancel(), True)[1])

        lw, lh = round(self.img_w / self.s), round(self.img_h / self.s)
        if wayland:
            self.fullscreen_on_monitor(self.get_screen(), monitor_index)
        else:
            self.move(0, 0)
            self.set_default_size(lw, lh)
            self.resize(lw, lh)
        if self.mode == "window" and self.snap_windows:
            self.hover = snap.window_at(*self.pointer)

    # ------------------------------------------------------------ setup / teardown
    def start(self) -> None:
        self.show_all()
        self.present()

    def _on_map(self, *_):
        cursor = Gdk.Cursor.new_from_name(self.get_display(), "crosshair")
        self.get_window().set_cursor(cursor)
        GLib.timeout_add(30, self._try_grab, 0)

    def _try_grab(self, attempt: int):
        if self.snap.backend == "wayland" or self._finished:
            self.present()
            return False
        seat = self.get_display().get_default_seat()
        status = seat.grab(self.get_window(), Gdk.SeatCapabilities.ALL, True, None, None, None, None)
        if status == Gdk.GrabStatus.SUCCESS:
            self._grab_seat = seat
            return False
        return attempt < 20 and (GLib.timeout_add(50, self._try_grab, attempt + 1) and False)

    def _close(self) -> None:
        self._finished = True
        if self._grab_seat is not None:
            self._grab_seat.ungrab()
            self._grab_seat = None
        self.hide()
        self.destroy()

    def _cancel(self) -> None:
        if self._finished:
            return
        self._close()
        GLib.idle_add(self.on_done, None)

    def _finish(self, rect, action=None, window: WindowInfo | None = None) -> None:
        if self._finished:
            return
        x, y, w, h = [int(round(v)) for v in rect]
        x, y = max(0, x), max(0, y)
        w, h = min(w, self.img_w - x), min(h, self.img_h - y)
        if w < 1 or h < 1:
            return
        pixbuf = self.snap.pixbuf.new_subpixbuf(x, y, w, h).copy()
        info = window or self.snap.window_at(x + w / 2, y + h / 2) or self.snap.active
        mode = "window" if window is not None else self.mode
        result = SelectionResult(pixbuf, (x, y, w, h), app=info.app if info else "",
                                 title=info.title if info else "", action=action, mode=mode)
        self._close()
        GLib.timeout_add(60, lambda: (self.on_done(result), False)[1])

    # ------------------------------------------------------------ coordinates
    def _img(self, ev) -> tuple[float, float]:
        return ev.x * self.s, ev.y * self.s

    def _clamp_sel(self, sel):
        x, y, w, h = sel
        w, h = min(w, self.img_w), min(h, self.img_h)
        x = max(0, min(x, self.img_w - w))
        y = max(0, min(y, self.img_h - h))
        return [x, y, w, h]

    def _handles(self):
        if not self.sel:
            return []
        x, y, w, h = [v / self.s for v in self.sel]
        return [("nw", x, y), ("n", x + w / 2, y), ("ne", x + w, y), ("e", x + w, y + h / 2),
                ("se", x + w, y + h), ("s", x + w / 2, y + h), ("sw", x, y + h), ("w", x, y + h / 2)]

    def _handle_at(self, lx, ly):
        for name, hx, hy in self._handles():
            if abs(lx - hx) <= HANDLE and abs(ly - hy) <= HANDLE:
                return name
        return None

    def _inside_sel(self, ix, iy) -> bool:
        if not self.sel:
            return False
        x, y, w, h = self.sel
        return x <= ix <= x + w and y <= iy <= y + h

    def _action_at(self, lx, ly):
        for name, (ax, ay, aw, ah) in self.action_rects:
            if ax <= lx <= ax + aw and ay <= ly <= ay + ah:
                return name
        return None

    # ------------------------------------------------------------ events
    def _on_motion(self, _w, ev):
        ix, iy = self._img(ev)
        self.pointer = (ix, iy)
        if self.state == "drawing" and self.mode == "region":
            if math.hypot(ev.x - self.anchor[0] / self.s, ev.y - self.anchor[1] / self.s) >= MIN_DRAG:
                ax, ay = self.anchor
                self.sel = self._clamp_sel([min(ax, ix), min(ay, iy), abs(ix - ax), abs(iy - ay)])
                self.hover = None
        elif self.state == "moving" and self.drag_sel:
            dx, dy = ix - self.drag_from[0], iy - self.drag_from[1]
            x, y, w, h = self.drag_sel
            self.sel = self._clamp_sel([x + dx, y + dy, w, h])
        elif self.state == "resizing" and self.drag_sel:
            self._resize_to(ix, iy)
        elif self.state == "idle" and self.snap_windows:
            self.hover = self.snap.window_at(ix, iy)
        elif self.state == "selected":
            self.hover_action = self._action_at(ev.x, ev.y)
            self._update_cursor(ev)
        self.queue_draw()
        return True

    def _update_cursor(self, ev):
        names = {"nw": "nw-resize", "ne": "ne-resize", "sw": "sw-resize", "se": "se-resize",
                 "n": "n-resize", "s": "s-resize", "e": "e-resize", "w": "w-resize"}
        handle = self._handle_at(ev.x, ev.y)
        if self.hover_action:
            name = "pointer"
        elif handle:
            name = names[handle]
        elif self._inside_sel(*self._img(ev)):
            name = "move"
        else:
            name = "crosshair"
        self.get_window().set_cursor(Gdk.Cursor.new_from_name(self.get_display(), name))

    def _resize_to(self, ix, iy):
        x, y, w, h = self.drag_sel
        x1, y1, x2, y2 = x, y, x + w, y + h
        name = self.handle
        if "n" in name:
            y1 = iy
        if "s" in name:
            y2 = iy
        if "w" in name:
            x1 = ix
        if "e" in name:
            x2 = ix
        nx1, nx2 = sorted((max(0, x1), min(self.img_w, x2)))
        ny1, ny2 = sorted((max(0, y1), min(self.img_h, y2)))
        self.sel = [nx1, ny1, max(1, nx2 - nx1), max(1, ny2 - ny1)]

    def _on_press(self, _w, ev):
        if ev.button == 3:
            self._cancel()
            return True
        if ev.button != 1:
            return True
        ix, iy = self._img(ev)
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            if self.sel and self._inside_sel(ix, iy):
                self._finish(self.sel)
            return True
        if self.state == "selected":
            action = self._action_at(ev.x, ev.y)
            if action:
                if action == "cancel":
                    self._cancel()
                else:
                    self._finish(self.sel, None if action == "capture" else action, self.chosen_window)
                return True
            handle = self._handle_at(ev.x, ev.y)
            if handle:
                self.state, self.handle, self.drag_sel = "resizing", handle, list(self.sel)
                return True
            if self._inside_sel(ix, iy):
                self.state, self.drag_from, self.drag_sel = "moving", (ix, iy), list(self.sel)
                return True
        self.state = "drawing"
        self.anchor = (ix, iy)
        self.sel = None
        self.chosen_window = None
        self.queue_draw()
        return True

    def _on_release(self, _w, ev):
        if ev.button != 1:
            return True
        if self.state == "drawing":
            if self.sel is None or self.sel[2] < 2 or self.sel[3] < 2:
                ix, iy = self._img(ev)
                win = self.snap.window_at(ix, iy) if self.snap_windows else None
                if win is not None:
                    self._finish(self._window_rect(win), None, win)
                    return True
                self.sel = None
                self.state = "idle"
            else:
                self.state = "selected"
        elif self.state in ("moving", "resizing"):
            self.state = "selected"
        self.queue_draw()
        return True

    def _window_rect(self, win: WindowInfo):
        x, y, w, h = win.rect(self.decorations)
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(self.img_w, x + w), min(self.img_h, y + h)
        return [x1, y1, x2 - x1, y2 - y1]

    def _on_key(self, _w, ev):
        key = ev.keyval
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        if key == Gdk.KEY_Escape:
            self._cancel()
        elif key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.sel:
                self._finish(self.sel, None, self.chosen_window)
            elif self.hover is not None:
                self._finish(self._window_rect(self.hover), None, self.hover)
        elif key in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            step = (10 if shift else 1) * self.s
            dx = {Gdk.KEY_Left: -step, Gdk.KEY_Right: step}.get(key, 0)
            dy = {Gdk.KEY_Up: -step, Gdk.KEY_Down: step}.get(key, 0)
            if self.sel and self.state == "selected":
                x, y, w, h = self.sel
                if ctrl:
                    self.sel = self._clamp_sel([x, y, max(1, w + dx), max(1, h + dy)])
                else:
                    self.sel = self._clamp_sel([x + dx, y + dy, w, h])
            else:
                self._warp(dx, dy)
        elif key in (Gdk.KEY_c, Gdk.KEY_C):
            hexcol = self._color_at(*self.pointer)
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(hexcol, -1)
            self._show_toast(f"Copied {hexcol}")
        elif key in (Gdk.KEY_m, Gdk.KEY_M):
            self.magnifier = not self.magnifier
        elif key in (Gdk.KEY_a, Gdk.KEY_A) and ctrl or key in (Gdk.KEY_f, Gdk.KEY_F):
            self.sel = [0, 0, self.img_w, self.img_h]
            self.state = "selected"
        self.queue_draw()
        return True

    def _warp(self, dx, dy):
        x = min(max(0, self.pointer[0] + dx), self.img_w - 1)
        y = min(max(0, self.pointer[1] + dy), self.img_h - 1)
        self.pointer = (x, y)
        if self.snap.backend == "x11":
            pointer = self.get_display().get_default_seat().get_pointer()
            pointer.warp(self.get_screen(), int(round(x / self.s)), int(round(y / self.s)))
        if self.state == "drawing" and self.mode == "region":
            ax, ay = self.anchor
            self.sel = self._clamp_sel([min(ax, x), min(ay, y), abs(x - ax), abs(y - ay)])

    def _show_toast(self, text):
        self.toast = text
        GLib.timeout_add(1400, self._clear_toast)

    def _clear_toast(self):
        self.toast = ""
        self.queue_draw()
        return False

    def _color_at(self, ix, iy) -> str:
        x = int(min(max(0, ix), self.img_w - 1))
        y = int(min(max(0, iy), self.img_h - 1))
        off = y * self._stride + x * self._nch
        r, g, b = self._pixels[off], self._pixels[off + 1], self._pixels[off + 2]
        return f"#{r:02X}{g:02X}{b:02X}"

    # ------------------------------------------------------------ drawing
    def _on_draw(self, _w, cr):
        s = self.s
        cr.save()
        cr.scale(1 / s, 1 / s)
        cr.set_source_surface(self.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_FAST)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        hole = None
        if self.sel:
            hole = self.sel
        elif self.hover is not None and self.state in ("idle", "drawing"):
            hole = self._window_rect(self.hover)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, self.img_w, self.img_h)
        if hole:
            cr.rectangle(*hole)
        cr.set_source_rgba(0, 0, 0, 0.45 if hole else 0.25)
        cr.fill()
        cr.restore()

        lw, lh = self.img_w / s, self.img_h / s
        px, py = self.pointer[0] / s, self.pointer[1] / s
        if hole:
            x, y, w, h = [v / s for v in hole]
            cr.set_source_rgb(*ACCENT)
            cr.set_line_width(2)
            cr.rectangle(x - 1, y - 1, w + 2, h + 2)
            cr.stroke()
            label = f"{int(hole[2])} × {int(hole[3])}"
            if self.hover is not None and not self.sel and self.hover.title:
                label = f"{self.hover.title[:60]}  ·  {label}"
            self._label(cr, label, x, y - 26 if y > 30 else y + 6)
        if self.state == "idle" and not self.sel:
            cr.set_source_rgba(1, 1, 1, 0.5)
            cr.set_line_width(1)
            cr.move_to(0, int(py) + 0.5)
            cr.line_to(lw, int(py) + 0.5)
            cr.move_to(int(px) + 0.5, 0)
            cr.line_to(int(px) + 0.5, lh)
            cr.stroke()
        if self.sel and self.state != "drawing":
            for _name, hx, hy in self._handles():
                cr.rectangle(hx - 4, hy - 4, 8, 8)
                cr.set_source_rgb(1, 1, 1)
                cr.fill_preserve()
                cr.set_source_rgb(*ACCENT)
                cr.set_line_width(1.5)
                cr.stroke()
        if self.state == "selected":
            self._draw_actions(cr)
        else:
            self.action_rects = []
        if self.magnifier and self.state in ("idle", "drawing", "resizing"):
            self._draw_magnifier(cr, px, py, lw, lh)
        hint = {
            "region": "Drag to select · Click a window · Arrows nudge · C copies colour · M magnifier · Esc cancels",
            "window": "Click a window to capture it · Enter captures the highlighted window · Esc cancels",
        }[self.mode if self.mode in ("region", "window") else "region"]
        if self.state == "selected":
            hint = "Drag handles to adjust · Arrows move (Ctrl resizes) · Enter or double-click captures · Esc cancels"
        self._label(cr, self.toast or hint, None, 12, center_width=lw)

    def _layout(self, cr, text, size=10.5, bold=False):
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription.from_string(f"Sans {'Bold ' if bold else ''}{size}")
        layout.set_font_description(desc)
        layout.set_text(text, -1)
        return layout

    def _label(self, cr, text, x, y, center_width=None):
        layout = self._layout(cr, text)
        _ink, log = layout.get_pixel_extents()
        w, h = log.width + 16, log.height + 8
        if center_width is not None:
            x = (center_width - w) / 2
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.85)
        _round_rect(cr, x, y, w, h, 6)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(x + 8, y + 4)
        PangoCairo.show_layout(cr, layout)

    def _draw_actions(self, cr):
        x, y, w, h = [v / self.s for v in self.sel]
        layouts = [(name, self._layout(cr, text, 10.5, name == "capture")) for name, text in ACTIONS]
        sizes = [(n, lay, lay.get_pixel_extents()[1]) for n, lay in layouts]
        bw = sum(log.width + 24 for _n, _l, log in sizes) + 6 * (len(sizes) - 1) + 12
        bh = 38
        lw, lh = self.img_w / self.s, self.img_h / self.s
        bx = min(max(4, x + w - bw), lw - bw - 4)
        by = y + h + 10
        if by + bh > lh - 4:
            by = y - bh - 10 if y - bh - 10 > 4 else y + h - bh - 10
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.92)
        _round_rect(cr, bx, by, bw, bh, 8)
        cr.fill()
        cx = bx + 6
        self.action_rects = []
        for name, layout, log in sizes:
            btn_w = log.width + 24
            rect = (cx, by + 5, btn_w, bh - 10)
            if name == "capture":
                cr.set_source_rgb(*ACCENT)
                _round_rect(cr, *rect, 5)
                cr.fill()
            elif self.hover_action == name:
                cr.set_source_rgba(1, 1, 1, 0.15)
                _round_rect(cr, *rect, 5)
                cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.move_to(cx + 12, by + (bh - log.height) / 2)
            PangoCairo.show_layout(cr, layout)
            self.action_rects.append((name, rect))
            cx += btn_w + 6

    def _draw_magnifier(self, cr, px, py, lw, lh):
        size = 132
        mx, my = px + 24, py + 24
        if mx + size > lw:
            mx = px - 24 - size
        if my + size + 26 > lh:
            my = py - 24 - size - 26
        ix, iy = self.pointer
        cr.save()
        _round_rect(cr, mx, my, size, size, 8)
        cr.clip()
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        cr.translate(mx + size / 2, my + size / 2)
        cr.scale(self.zoom / self.s, self.zoom / self.s)
        cr.translate(-(int(ix) + 0.5), -(int(iy) + 0.5))
        cr.set_source_surface(self.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        cr.restore()
        cell = self.zoom / self.s
        cr.set_source_rgba(*ACCENT, 0.9)
        cr.set_line_width(1)
        cr.rectangle(mx + size / 2 - cell / 2, my + size / 2 - cell / 2, cell, cell)
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(2)
        _round_rect(cr, mx, my, size, size, 8)
        cr.stroke()
        hexcol = self._color_at(ix, iy)
        self._label(cr, f"{int(ix)}, {int(iy)}   {hexcol}", mx, my + size + 4)


def _round_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
