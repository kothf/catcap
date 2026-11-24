"""Small vector tool icons drawn with cairo, so they look the same in every theme."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

SIZE = 20


def _arrow(cr):
    cr.move_to(4, 16)
    cr.line_to(14, 6)
    cr.stroke()
    cr.move_to(16, 4)
    cr.line_to(9, 5.5)
    cr.line_to(14.5, 11)
    cr.close_path()
    cr.fill()


def _select(cr):
    cr.move_to(5, 3)
    cr.line_to(5, 16)
    cr.line_to(8.5, 12.5)
    cr.line_to(11, 17.5)
    cr.line_to(13, 16.5)
    cr.line_to(10.5, 11.5)
    cr.line_to(15, 11.5)
    cr.close_path()
    cr.fill()


def _line(cr):
    cr.move_to(4, 16)
    cr.line_to(16, 4)
    cr.stroke()


def _rect(cr):
    cr.rectangle(3.5, 5.5, 13, 9)
    cr.stroke()


def _ellipse(cr):
    cr.save()
    cr.translate(10, 10)
    cr.scale(7, 5)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()
    cr.stroke()


def _text(cr):
    cr.move_to(4, 4.5)
    cr.line_to(16, 4.5)
    cr.move_to(10, 4.5)
    cr.line_to(10, 16.5)
    cr.stroke()


def _callout(cr):
    cr.move_to(3.5, 4)
    cr.line_to(16.5, 4)
    cr.line_to(16.5, 12)
    cr.line_to(9, 12)
    cr.line_to(5, 16.5)
    cr.line_to(6, 12)
    cr.line_to(3.5, 12)
    cr.close_path()
    cr.stroke()


def _highlighter(cr):
    cr.save()
    cr.set_source_rgba(1, 0.85, 0.1, 0.9)
    cr.rectangle(2, 11, 16, 5)
    cr.fill()
    cr.restore()
    cr.move_to(7, 13)
    cr.line_to(12, 4)
    cr.line_to(15, 6)
    cr.line_to(10, 14)
    cr.close_path()
    cr.stroke()


def _pen(cr):
    cr.move_to(3, 15)
    cr.curve_to(6, 8, 9, 18, 12, 10)
    cr.curve_to(13.5, 6, 15, 6, 17, 5)
    cr.stroke()


def _step(cr):
    cr.arc(10, 10, 7.5, 0, 2 * math.pi)
    cr.fill()
    cr.save()
    cr.set_source_rgb(1, 1, 1)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(11)
    ext = cr.text_extents("1")
    cr.move_to(10 - ext.width / 2 - ext.x_bearing, 10 + ext.height / 2)
    cr.show_text("1")
    cr.restore()


def _blur(cr):
    for i, a in enumerate((0.9, 0.6, 0.35)):
        cr.save()
        cr.set_source_rgba(*_fg, a)
        cr.arc(10, 10, 3 + i * 2.5, 0, 2 * math.pi)
        cr.stroke()
        cr.restore()


def _pixelate(cr):
    for row in range(4):
        for col in range(4):
            if (row + col) % 2 == 0:
                cr.rectangle(2 + col * 4, 2 + row * 4, 4, 4)
    cr.fill()


def _spotlight(cr):
    cr.save()
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.rectangle(2, 3, 16, 14)
    cr.arc(10, 10, 4.5, 0, 2 * math.pi)
    cr.set_source_rgba(*_fg, 0.55)
    cr.fill()
    cr.restore()


def _crop(cr):
    cr.move_to(6, 2)
    cr.line_to(6, 14)
    cr.line_to(18, 14)
    cr.move_to(2, 6)
    cr.line_to(14, 6)
    cr.line_to(14, 18)
    cr.stroke()


def _drag(cr):
    cr.rectangle(4, 3, 12, 14)
    cr.stroke()
    cr.move_to(7, 8)
    cr.line_to(13, 8)
    cr.move_to(7, 11)
    cr.line_to(13, 11)
    cr.move_to(7, 14)
    cr.line_to(11, 14)
    cr.stroke()


DRAW = {
    "select": _select, "arrow": _arrow, "line": _line, "rect": _rect, "ellipse": _ellipse,
    "text": _text, "callout": _callout, "highlighter": _highlighter, "pen": _pen,
    "step": _step, "blur": _blur, "pixelate": _pixelate, "spotlight": _spotlight, "crop": _crop,
    "drag": _drag,
}

_fg = (0.2, 0.2, 0.2)


def tool_image(name: str, widget: Gtk.Widget | None = None) -> Gtk.Image:
    global _fg
    scale = widget.get_scale_factor() if widget is not None else 1
    color = Gdk.RGBA(0.2, 0.2, 0.2, 1)
    if widget is not None and widget.get_realized():
        color = widget.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        color.alpha = 1.0
    _fg = (color.red, color.green, color.blue)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE * scale, SIZE * scale)
    surface.set_device_scale(scale, scale)
    cr = cairo.Context(surface)
    cr.set_source_rgb(*_fg)
    cr.set_line_width(1.8)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    DRAW.get(name, _rect)(cr)
    return Gtk.Image.new_from_surface(surface)
