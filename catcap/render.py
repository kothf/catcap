"""Render original + Document to pixels on demand (LIB-03), and export files."""

from __future__ import annotations

import math
import os
from pathlib import Path

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Pango, PangoCairo  # noqa: E402

from .annotations import Document, EFFECT_KINDS, step_label  # noqa: E402

try:
    from PIL import Image, ImageFilter
except ImportError:  # pragma: no cover - python3-pil is a package dependency
    Image = ImageFilter = None

EXPORT_FORMATS = {
    "png": ("PNG", ".png"),
    "jpg": ("JPEG", ".jpg"),
    "webp": ("WebP", ".webp"),
    "bmp": ("BMP", ".bmp"),
    "pdf": ("PDF", ".pdf"),
}


# ------------------------------------------------------------------ conversions
def parse_color(spec: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    rgba = Gdk.RGBA()
    if not rgba.parse(spec or "#000"):
        rgba.parse("#e53935")
    return rgba.red, rgba.green, rgba.blue, rgba.alpha * alpha


def pixbuf_to_surface(pixbuf: GdkPixbuf.Pixbuf) -> cairo.ImageSurface:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixbuf.get_width(), pixbuf.get_height())
    ctx = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
    ctx.set_operator(cairo.OPERATOR_SOURCE)
    ctx.paint()
    surface.flush()
    return surface


def surface_to_pixbuf(surface: cairo.ImageSurface) -> GdkPixbuf.Pixbuf:
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, surface.get_width(), surface.get_height())


def surface_to_pil(surface: cairo.ImageSurface):
    surface.flush()
    w, h = surface.get_width(), surface.get_height()
    data = bytes(surface.get_data())
    return Image.frombuffer("RGBA", (w, h), data, "raw", "BGRA", surface.get_stride(), 1)


def pil_to_surface(img) -> cairo.ImageSurface:
    img = img.convert("RGBA")
    w, h = img.size
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
    raw = img.tobytes("raw", "BGRA", stride)
    buf = bytearray(raw)
    return cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_ARGB32, w, h, stride)


def load_original(path: str | Path) -> cairo.ImageSurface:
    return pixbuf_to_surface(GdkPixbuf.Pixbuf.new_from_file(str(path)))


# ------------------------------------------------------------------ base image
def transformed_surface(src: cairo.ImageSurface, doc: Document) -> cairo.ImageSurface:
    ow, oh = src.get_width(), src.get_height()
    w, h = doc.transformed_size(ow, oh)
    if doc.rotate == 0 and not doc.flip and abs(doc.scale - 1.0) < 1e-9:
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        ctx = cairo.Context(out)
        ctx.set_source_surface(src, 0, 0)
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.paint()
        return out
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(out)
    ctx.scale(doc.scale, doc.scale)
    if doc.rotate == 90:
        ctx.translate(oh, 0)
        ctx.rotate(math.pi / 2)
    elif doc.rotate == 180:
        ctx.translate(ow, oh)
        ctx.rotate(math.pi)
    elif doc.rotate == 270:
        ctx.translate(0, ow)
        ctx.rotate(-math.pi / 2)
    if doc.flip:
        ctx.translate(ow, 0)
        ctx.scale(-1, 1)
    ctx.set_source_surface(src, 0, 0)
    pattern = ctx.get_source()
    pattern.set_filter(cairo.FILTER_GOOD if doc.scale < 1 else cairo.FILTER_BILINEAR)
    pattern.set_extend(cairo.EXTEND_PAD)
    ctx.set_operator(cairo.OPERATOR_SOURCE)
    ctx.paint()
    return out


# ------------------------------------------------------------------ items
def _rounded_rect(ctx, x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0:
        ctx.rectangle(x, y, w, h)
        return
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def _apply_effect(surface: cairo.ImageSurface, item: dict) -> None:
    if Image is None:
        return
    sw, sh = surface.get_width(), surface.get_height()
    x0 = max(0, int(math.floor(item["x"])))
    y0 = max(0, int(math.floor(item["y"])))
    x1 = min(sw, int(math.ceil(item["x"] + item["w"])))
    y1 = min(sh, int(math.ceil(item["y"] + item["h"])))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return
    strength = max(2, int(item.get("strength", 12)))
    region = surface_to_pil(surface).crop((x0, y0, x1, y1))
    if item["type"] == "pixelate":
        small = region.resize((max(1, (x1 - x0) // strength), max(1, (y1 - y0) // strength)), Image.BILINEAR)
        region = small.resize((x1 - x0, y1 - y0), Image.NEAREST)
    else:
        # Downscale first so blurred text cannot be recovered, then blur.
        small = region.resize((max(1, (x1 - x0) // 4), max(1, (y1 - y0) // 4)), Image.BILINEAR)
        region = small.resize((x1 - x0, y1 - y0), Image.BILINEAR).filter(ImageFilter.GaussianBlur(strength))
    patch = pil_to_surface(region)
    ctx = cairo.Context(surface)
    ctx.set_operator(cairo.OPERATOR_SOURCE)
    ctx.set_source_surface(patch, x0, y0)
    ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
    ctx.fill()
    surface.flush()


def _text_layout(ctx, item: dict):
    layout = PangoCairo.create_layout(ctx)
    desc = Pango.FontDescription.from_string(item.get("font", "Sans Bold"))
    desc.set_absolute_size(max(4.0, item.get("font_size", 22)) * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(item.get("text", ""), -1)
    return layout


def measure_text(item: dict) -> tuple[float, float]:
    """Size of a text/callout box including padding; stored on the item."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    ctx = cairo.Context(surface)
    layout = _text_layout(ctx, item)
    _ink, logical = layout.get_pixel_extents()
    pad = _box_padding(item)
    w, h = logical.width + 2 * pad, logical.height + 2 * pad
    item["w"], item["h"] = float(w), float(h)
    return w, h


def _box_padding(item: dict) -> float:
    if item["type"] == "callout" or item.get("bg"):
        return max(6.0, item.get("font_size", 22) * 0.4)
    return 2.0


def _draw_box(ctx, item: dict, color_override=None) -> None:
    measure_text(item)
    x, y, w, h = item["x"], item["y"], item["w"], item["h"]
    fg = color_override or parse_color(item.get("color", "#e53935"), item.get("opacity", 1.0))
    is_callout = item["type"] == "callout"
    if is_callout or item.get("bg"):
        bg = color_override or parse_color(item.get("bg_color", "#ffffff"), item.get("opacity", 1.0))
        radius = min(12.0, h / 3)

        def bubble():
            _rounded_rect(ctx, x, y, w, h, radius)

        def tail():
            tx, ty = item["tail"]
            cx, cy = x + w / 2, y + h / 2
            ang = math.atan2(ty - cy, tx - cx)
            base = min(w, h) * 0.22
            ctx.move_to(cx + math.cos(ang + math.pi / 2) * base, cy + math.sin(ang + math.pi / 2) * base)
            ctx.line_to(tx, ty)
            ctx.line_to(cx + math.cos(ang - math.pi / 2) * base, cy + math.sin(ang - math.pi / 2) * base)
            ctx.close_path()

        shapes = [bubble] + ([tail] if is_callout and item.get("tail") else [])
        if is_callout:
            ctx.set_source_rgba(*fg)
            ctx.set_line_width(2 * max(1.5, item.get("width", 3) / 1.5))
            for shape in shapes:
                shape()
                ctx.stroke()
        ctx.set_source_rgba(*bg)
        for shape in shapes:
            shape()
            ctx.fill()
    if color_override and (is_callout or item.get("bg")):
        return
    pad = _box_padding(item)
    layout = _text_layout(ctx, item)
    ctx.move_to(x + pad, y + pad)
    text_color = fg
    if is_callout and not color_override:
        text_color = parse_color(item.get("text_color", item.get("color", "#e53935")), item.get("opacity", 1.0))
    ctx.set_source_rgba(*text_color)
    PangoCairo.show_layout(ctx, layout)


def _draw_arrow(ctx, item: dict, rgba) -> None:
    x1, y1, x2, y2 = item["x1"], item["y1"], item["x2"], item["y2"]
    width = item.get("width", 4)
    ang = math.atan2(y2 - y1, x2 - x1)
    length = math.hypot(x2 - x1, y2 - y1)
    head = min(max(12.0, width * 4.0), length * 0.6 if length else 12.0)
    half = head * 0.5
    bx, by = x2 - math.cos(ang) * head * 0.9, y2 - math.sin(ang) * head * 0.9
    ctx.set_source_rgba(*rgba)
    ctx.set_line_width(width)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.move_to(x1, y1)
    ctx.line_to(bx, by)
    ctx.stroke()
    ctx.move_to(x2, y2)
    ctx.line_to(x2 - math.cos(ang) * head + math.sin(ang) * half, y2 - math.sin(ang) * head - math.cos(ang) * half)
    ctx.line_to(x2 - math.cos(ang) * head - math.sin(ang) * half, y2 - math.sin(ang) * head + math.cos(ang) * half)
    ctx.close_path()
    ctx.fill()


def draw_item(ctx, item: dict, color_override=None) -> None:
    """Draw one non-effect item (blur/pixelate/spotlight are handled in render)."""
    kind = item["type"]
    rgba = color_override or parse_color(item.get("color", "#e53935"), item.get("opacity", 1.0))
    width = item.get("width", 4)
    ctx.save()
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    if kind == "arrow":
        _draw_arrow(ctx, item, rgba)
    elif kind == "line":
        ctx.set_source_rgba(*rgba)
        ctx.set_line_width(width)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        if item.get("dashed"):
            ctx.set_dash([width * 3, width * 2])
        ctx.move_to(item["x1"], item["y1"])
        ctx.line_to(item["x2"], item["y2"])
        ctx.stroke()
    elif kind in ("rect", "ellipse"):
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        if kind == "rect":
            _rounded_rect(ctx, x, y, w, h, item.get("radius", 0))
        else:
            ctx.save()
            ctx.translate(x + w / 2, y + h / 2)
            ctx.scale(max(w, 0.5) / 2, max(h, 0.5) / 2)
            ctx.arc(0, 0, 1, 0, 2 * math.pi)
            ctx.restore()
        if item.get("fill"):
            fill = color_override or parse_color(item.get("fill_color", item.get("color")), item.get("opacity", 1.0))
            ctx.set_source_rgba(*fill)
            ctx.fill_preserve()
        ctx.set_source_rgba(*rgba)
        ctx.set_line_width(width)
        ctx.stroke()
    elif kind in ("pen", "highlighter"):
        pts = item["points"]
        if pts:
            if kind == "highlighter" and not color_override:
                ctx.set_operator(cairo.OPERATOR_MULTIPLY)
                ctx.set_line_cap(cairo.LINE_CAP_SQUARE)
            else:
                ctx.set_line_cap(cairo.LINE_CAP_ROUND)
            ctx.set_source_rgba(*rgba)
            ctx.set_line_width(width)
            ctx.move_to(*pts[0])
            if len(pts) == 1:
                ctx.line_to(pts[0][0] + 0.1, pts[0][1])
            for i in range(1, len(pts)):
                if i + 1 < len(pts) and kind == "pen":
                    mx, my = (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2
                    ctx.curve_to(pts[i][0], pts[i][1], pts[i][0], pts[i][1], mx, my)
                else:
                    ctx.line_to(*pts[i])
            ctx.stroke()
    elif kind in ("text", "callout"):
        _draw_box(ctx, item, color_override)
    elif kind == "step":
        r = item.get("size", 32) / 2
        ctx.arc(item["x"], item["y"], r, 0, 2 * math.pi)
        ctx.set_source_rgba(*rgba)
        ctx.fill()
        if not color_override:
            ctx.arc(item["x"], item["y"], r, 0, 2 * math.pi)
            ctx.set_source_rgba(1, 1, 1, 0.9 * item.get("opacity", 1.0))
            ctx.set_line_width(max(1.5, r * 0.12))
            ctx.stroke()
            layout = PangoCairo.create_layout(ctx)
            desc = Pango.FontDescription.from_string("Sans Bold")
            label = step_label(item.get("n", 1), item.get("style", "number"))
            desc.set_absolute_size(r * (1.1 if len(label) < 2 else 0.85) * Pango.SCALE)
            layout.set_font_description(desc)
            layout.set_text(label, -1)
            _ink, logical = layout.get_pixel_extents()
            ctx.move_to(item["x"] - logical.width / 2, item["y"] - logical.height / 2)
            ctx.set_source_rgba(1, 1, 1, item.get("opacity", 1.0))
            PangoCairo.show_layout(ctx, layout)
    ctx.restore()


def _draw_with_shadow(ctx, item: dict) -> None:
    if item.get("shadow") and item["type"] not in ("highlighter",):
        ctx.save()
        offset = max(2.0, item.get("width", 4) * 0.6)
        ctx.translate(offset, offset)
        draw_item(ctx, item, color_override=(0, 0, 0, 0.35))
        ctx.restore()
    draw_item(ctx, item)


def _draw_spotlights(surface, items) -> None:
    ctx = cairo.Context(surface)
    ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    ctx.rectangle(0, 0, surface.get_width(), surface.get_height())
    for s in items:
        if s.get("shape") == "ellipse":
            ctx.save()
            ctx.translate(s["x"] + s["w"] / 2, s["y"] + s["h"] / 2)
            ctx.scale(max(s["w"], 0.5) / 2, max(s["h"], 0.5) / 2)
            ctx.arc(0, 0, 1, 0, 2 * math.pi)
            ctx.restore()
        else:
            ctx.rectangle(s["x"], s["y"], s["w"], s["h"])
    ctx.set_source_rgba(0, 0, 0, items[0].get("dim", 0.55))
    ctx.fill()


def draw_items(surface: cairo.ImageSurface, items: list) -> None:
    """Effects first (blur/pixelate), then the spotlight dim, then drawn items,
    so obfuscation always applies to the screenshot and annotations stay bright."""
    for item in items:
        if item["type"] in EFFECT_KINDS:
            _apply_effect(surface, item)
    spotlights = [i for i in items if i["type"] == "spotlight"]
    if spotlights:
        _draw_spotlights(surface, spotlights)
    for item in items:
        if item["type"] in EFFECT_KINDS or item["type"] == "spotlight":
            continue
        ctx = cairo.Context(surface)
        _draw_with_shadow(ctx, item)
    surface.flush()


def render(src: cairo.ImageSurface, doc: Document, *, crop: bool = True, items=None) -> cairo.ImageSurface:
    """Flatten original + document. `items` overrides doc.items (editor previews)."""
    out = transformed_surface(src, doc)
    draw_items(out, doc.items if items is None else items)
    if crop and doc.crop:
        x, y, w, h = doc.crop
        cw, ch = max(1, round(w)), max(1, round(h))
        cropped = cairo.ImageSurface(cairo.FORMAT_ARGB32, cw, ch)
        ctx = cairo.Context(cropped)
        ctx.set_source_surface(out, -round(x), -round(y))
        ctx.paint()
        return cropped
    return out


def render_capture(library, capture_id: str) -> cairo.ImageSurface:
    src = load_original(library.original_path(capture_id))
    return render(src, library.load_doc(capture_id))


def render_thumbnail(library, capture_id: str, size: int = 160) -> GdkPixbuf.Pixbuf:
    """Load or build the cached thumbnail (with annotations)."""
    thumb = library.thumb_path(capture_id)
    original = library.original_path(capture_id)
    doc_file = library.doc_path(capture_id)
    try:
        t_mtime = thumb.stat().st_mtime
        newest = max(original.stat().st_mtime, doc_file.stat().st_mtime if doc_file.exists() else 0)
        if t_mtime >= newest:
            pb = GdkPixbuf.Pixbuf.new_from_file(str(thumb))
            if max(pb.get_width(), pb.get_height()) == size or (
                    pb.get_width() < size and pb.get_height() < size):
                return pb
    except (FileNotFoundError, gi.repository.GLib.Error):
        pass
    doc = library.load_doc(capture_id)
    if doc.is_empty():
        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(original), size, size)
    else:
        pb = scale_to_fit(surface_to_pixbuf(render_capture(library, capture_id)), size, size)
    try:
        pb.savev(str(thumb), "png", [], [])
        os.chmod(thumb, 0o600)
    except Exception:
        pass
    return pb


def scale_to_fit(pixbuf: GdkPixbuf.Pixbuf, max_w: int, max_h: int, upscale: bool = False) -> GdkPixbuf.Pixbuf:
    w, h = pixbuf.get_width(), pixbuf.get_height()
    k = min(max_w / w, max_h / h)
    if k >= 1 and not upscale:
        return pixbuf
    return pixbuf.scale_simple(max(1, int(w * k)), max(1, int(h * k)), GdkPixbuf.InterpType.BILINEAR)


# ------------------------------------------------------------------ export
def save_surface(surface: cairo.ImageSurface, path: str | Path, fmt: str | None = None, quality: int = 90) -> Path:
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".") or "png").lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unsupported format: {fmt}")
    if fmt == "png":
        surface.write_to_png(str(path))
    elif fmt == "pdf":
        save_pdf([surface], path)
    else:
        img = surface_to_pil(surface)
        if fmt in ("jpg", "bmp"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        kwargs = {"quality": int(quality)} if fmt in ("jpg", "webp") else {}
        img.save(str(path), EXPORT_FORMATS[fmt][0], **kwargs)
    return path


def save_pdf(surfaces, path: str | Path) -> Path:
    """One page per image, page size = image size at 96 dpi (OUT-03)."""
    path = Path(path)
    first = surfaces[0]
    k = 72.0 / 96.0
    pdf = cairo.PDFSurface(str(path), first.get_width() * k, first.get_height() * k)
    ctx = cairo.Context(pdf)
    for surface in surfaces:
        pdf.set_size(surface.get_width() * k, surface.get_height() * k)
        ctx.save()
        ctx.scale(k, k)
        ctx.set_source_surface(surface, 0, 0)
        ctx.paint()
        ctx.restore()
        ctx.show_page()
    pdf.finish()
    return path
