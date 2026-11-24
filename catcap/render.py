"""Render original + Document to pixels on demand (LIB-03), and export files. Pure QPainter."""

from __future__ import annotations

import math
import os
from pathlib import Path

from PyQt6.QtCore import QMarginsF, QPointF, QRectF, QSize, QSizeF, Qt
from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QImageReader, QImageWriter, QPageSize, QPainter,
                         QPainterPath, QPdfWriter, QPen)

from .annotations import Document, EFFECT_KINDS, step_label

FMT = QImage.Format.Format_ARGB32_Premultiplied

EXPORT_FORMATS = {
    "png": ("PNG", ".png"),
    "jpg": ("JPEG", ".jpg"),
    "webp": ("WebP", ".webp"),
    "bmp": ("BMP", ".bmp"),
    "pdf": ("PDF", ".pdf"),
}


# ------------------------------------------------------------------ helpers
def parse_color(spec: str, alpha: float = 1.0) -> QColor:
    color = QColor(spec or "#e53935")
    if not color.isValid():
        color = QColor("#e53935")
    color.setAlphaF(max(0.0, min(1.0, color.alphaF() * alpha)))
    return color


def load_original(path) -> QImage:
    image = QImage(str(path))
    if image.isNull():
        raise ValueError(f"cannot read {path}")
    return image.convertToFormat(FMT)


def blank(w: int, h: int) -> QImage:
    image = QImage(max(1, w), max(1, h), FMT)
    image.fill(Qt.GlobalColor.transparent)
    return image


def item_font(item: dict) -> QFont:
    spec = (item.get("font") or "Sans Bold").split()
    font = QFont()
    styles = {"bold": QFont.Weight.Bold, "semibold": QFont.Weight.DemiBold, "medium": QFont.Weight.Medium,
              "light": QFont.Weight.Light, "regular": QFont.Weight.Normal}
    while spec and spec[-1].lower() in (*styles, "italic", "oblique"):
        word = spec.pop().lower()
        if word in ("italic", "oblique"):
            font.setItalic(True)
        else:
            font.setWeight(styles[word])
    font.setFamily(" ".join(spec) or "Sans")
    font.setPixelSize(max(4, int(round(item.get("font_size", 22)))))
    return font


# ------------------------------------------------------------------ base image
def transformed_image(src: QImage, doc: Document) -> QImage:
    ow, oh = src.width(), src.height()
    w, h = doc.transformed_size(ow, oh)
    out = blank(w, h)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.scale(doc.scale, doc.scale)
    if doc.rotate == 90:
        p.translate(oh, 0)
        p.rotate(90)
    elif doc.rotate == 180:
        p.translate(ow, oh)
        p.rotate(180)
    elif doc.rotate == 270:
        p.translate(0, ow)
        p.rotate(-90)
    if doc.flip:
        p.translate(ow, 0)
        p.scale(-1, 1)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    p.drawImage(0, 0, src)
    p.end()
    return out


# ------------------------------------------------------------------ text boxes
def _box_padding(item: dict) -> float:
    if item["type"] == "callout" or item.get("bg"):
        return max(6.0, item.get("font_size", 22) * 0.4)
    return 2.0


def _text_rect(item: dict) -> QRectF:
    fm = QFontMetricsF(item_font(item))
    return fm.boundingRect(QRectF(0, 0, 100000, 100000), int(Qt.AlignmentFlag.AlignLeft), item.get("text", "") or " ")


def measure_text(item: dict) -> tuple[float, float]:
    rect = _text_rect(item)
    pad = _box_padding(item)
    item["w"], item["h"] = float(rect.width() + 2 * pad), float(rect.height() + 2 * pad)
    return item["w"], item["h"]


def _rounded(rect: QRectF, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    return path


def _draw_box(p: QPainter, item: dict, override: QColor | None) -> None:
    measure_text(item)
    rect = QRectF(item["x"], item["y"], item["w"], item["h"])
    fg = override or parse_color(item.get("color"), item.get("opacity", 1.0))
    callout = item["type"] == "callout"
    if callout or item.get("bg"):
        bg = override or parse_color(item.get("bg_color", "#ffffff"), item.get("opacity", 1.0))
        shapes = [_rounded(rect, min(12.0, rect.height() / 3))]
        if callout and item.get("tail"):
            tx, ty = item["tail"]
            c = rect.center()
            ang = math.atan2(ty - c.y(), tx - c.x())
            base = min(rect.width(), rect.height()) * 0.22
            tail = QPainterPath(QPointF(c.x() + math.cos(ang + math.pi / 2) * base,
                                        c.y() + math.sin(ang + math.pi / 2) * base))
            tail.lineTo(tx, ty)
            tail.lineTo(c.x() + math.cos(ang - math.pi / 2) * base, c.y() + math.sin(ang - math.pi / 2) * base)
            tail.closeSubpath()
            shapes.append(tail)
        if callout:
            pen = QPen(fg, 2 * max(1.5, item.get("width", 3) / 1.5))
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for shape in shapes:
                p.drawPath(shape)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        for shape in shapes:
            p.drawPath(shape)
        if override is not None:
            return
    pad = _box_padding(item)
    p.setFont(item_font(item))
    p.setPen(fg)
    p.drawText(rect.adjusted(pad, pad, -pad + 1000, -pad + 1000), int(Qt.AlignmentFlag.AlignLeft),
               item.get("text", ""))


# ------------------------------------------------------------------ shapes
def _pen(color: QColor, width: float, cap=Qt.PenCapStyle.RoundCap) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(cap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw_arrow(p: QPainter, item: dict, color: QColor) -> None:
    x1, y1, x2, y2 = item["x1"], item["y1"], item["x2"], item["y2"]
    width = item.get("width", 4)
    ang = math.atan2(y2 - y1, x2 - x1)
    length = math.hypot(x2 - x1, y2 - y1)
    head = min(max(12.0, width * 4.0), length * 0.6 if length else 12.0)
    half = head * 0.5
    bx, by = x2 - math.cos(ang) * head * 0.9, y2 - math.sin(ang) * head * 0.9
    p.setPen(_pen(color, width))
    p.drawLine(QPointF(x1, y1), QPointF(bx, by))
    tri = QPainterPath(QPointF(x2, y2))
    tri.lineTo(x2 - math.cos(ang) * head + math.sin(ang) * half, y2 - math.sin(ang) * head - math.cos(ang) * half)
    tri.lineTo(x2 - math.cos(ang) * head - math.sin(ang) * half, y2 - math.sin(ang) * head + math.cos(ang) * half)
    tri.closeSubpath()
    p.setPen(_pen(color, max(1.0, width * 0.4)))
    p.setBrush(color)
    p.drawPath(tri)


def _path_of(points, smooth: bool) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    if len(points) == 1:
        path.lineTo(points[0][0] + 0.1, points[0][1])
    for i in range(1, len(points)):
        if smooth and i + 1 < len(points):
            mx, my = (points[i][0] + points[i + 1][0]) / 2, (points[i][1] + points[i + 1][1]) / 2
            path.quadTo(QPointF(*points[i]), QPointF(mx, my))
        else:
            path.lineTo(QPointF(*points[i]))
    return path


def draw_item(p: QPainter, item: dict, override: QColor | None = None) -> None:
    """Draw one non-effect item (blur/pixelate/spotlight are handled by draw_items)."""
    kind = item["type"]
    color = override or parse_color(item.get("color"), item.get("opacity", 1.0))
    width = item.get("width", 4)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "arrow":
        _draw_arrow(p, item, color)
    elif kind == "line":
        pen = _pen(color, width)
        if item.get("dashed"):
            pen.setDashPattern([3, 2])
        p.setPen(pen)
        p.drawLine(QPointF(item["x1"], item["y1"]), QPointF(item["x2"], item["y2"]))
    elif kind in ("rect", "ellipse"):
        rect = QRectF(item["x"], item["y"], item["w"], item["h"])
        if item.get("fill"):
            p.setBrush(override or parse_color(item.get("fill_color", item.get("color")), item.get("opacity", 1.0)))
        p.setPen(_pen(color, width))
        if kind == "rect":
            r = min(item.get("radius", 0), rect.width() / 2, rect.height() / 2)
            p.drawRoundedRect(rect, r, r)
        else:
            p.drawEllipse(rect)
    elif kind in ("pen", "highlighter"):
        pts = item["points"]
        if pts:
            if kind == "highlighter" and override is None:
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
                p.setPen(_pen(color, width, Qt.PenCapStyle.SquareCap))
            else:
                p.setPen(_pen(color, width))
            p.drawPath(_path_of(pts, kind == "pen"))
    elif kind in ("text", "callout"):
        _draw_box(p, item, override)
    elif kind == "step":
        r = item.get("size", 32) / 2
        center = QPointF(item["x"], item["y"])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(center, r, r)
        if override is None:
            p.setPen(QPen(QColor(255, 255, 255, int(230 * item.get("opacity", 1.0))), max(1.5, r * 0.12)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center, r, r)
            label = step_label(item.get("n", 1), item.get("style", "number"))
            font = QFont()
            font.setBold(True)
            font.setPixelSize(max(6, int(r * (1.1 if len(label) < 2 else 0.85))))
            p.setFont(font)
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r), int(Qt.AlignmentFlag.AlignCenter), label)
    p.restore()


def _draw_with_shadow(p: QPainter, item: dict) -> None:
    if item.get("shadow") and item["type"] != "highlighter":
        p.save()
        off = max(2.0, item.get("width", 4) * 0.6)
        p.translate(off, off)
        draw_item(p, item, QColor(0, 0, 0, 90))
        p.restore()
    draw_item(p, item)


def _apply_effect(image: QImage, item: dict) -> None:
    x0 = max(0, int(math.floor(item["x"])))
    y0 = max(0, int(math.floor(item["y"])))
    x1 = min(image.width(), int(math.ceil(item["x"] + item["w"])))
    y1 = min(image.height(), int(math.ceil(item["y"] + item["h"])))
    w, h = x1 - x0, y1 - y0
    if w < 1 or h < 1:
        return
    if item["type"] == "redact":
        # Solid and fully opaque whatever the opacity setting, so nothing underneath survives.
        p = QPainter(image)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        color = parse_color(item.get("color", "#000000"))
        color.setAlpha(255)
        p.fillRect(x0, y0, w, h, color)
        p.end()
        return
    strength = max(2, int(item.get("strength", 12)))
    region = image.copy(x0, y0, w, h)
    if item["type"] == "pixelate":
        small = region.scaled(max(1, w // strength), max(1, h // strength), Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        region = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    else:
        # Shrink hard (destroys detail, so text cannot be recovered), then blow up smoothly twice.
        factor = max(3, strength)
        small = region.scaled(max(1, w // factor), max(1, h // factor), Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        mid = small.scaled(max(1, w // 2), max(1, h // 2), Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        region = mid.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    p = QPainter(image)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    p.drawImage(x0, y0, region)
    p.end()


def _draw_spotlights(image: QImage, spots: list) -> None:
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    path.addRect(QRectF(0, 0, image.width(), image.height()))
    for s in spots:
        rect = QRectF(s["x"], s["y"], s["w"], s["h"])
        if s.get("shape") == "ellipse":
            path.addEllipse(rect)
        else:
            path.addRect(rect)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.fillPath(path, QColor(0, 0, 0, int(255 * spots[0].get("dim", 0.55))))
    p.end()


def draw_items(image: QImage, items: list) -> None:
    """Effects first, then the spotlight dim, then drawn items, so obfuscation always hits the
    screenshot and annotations stay bright."""
    for item in items:
        if item["type"] in EFFECT_KINDS:
            _apply_effect(image, item)
    spots = [i for i in items if i["type"] == "spotlight"]
    if spots:
        _draw_spotlights(image, spots)
    p = QPainter(image)
    for item in items:
        if item["type"] in EFFECT_KINDS or item["type"] == "spotlight":
            continue
        _draw_with_shadow(p, item)
    p.end()


def render(src: QImage, doc: Document, *, crop: bool = True, items=None) -> QImage:
    out = transformed_image(src, doc)
    draw_items(out, doc.items if items is None else items)
    if crop and doc.crop:
        x, y, w, h = [round(v) for v in doc.crop]
        return out.copy(x, y, max(1, w), max(1, h))
    return out


def render_capture(library, capture_id: str) -> QImage:
    return render(load_original(library.original_path(capture_id)), library.load_doc(capture_id))


def scale_to_fit(image: QImage, max_w: int, max_h: int, upscale: bool = False) -> QImage:
    if not upscale and image.width() <= max_w and image.height() <= max_h:
        return image
    return image.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def render_preview(library, capture_id: str, max_w: int, max_h: int) -> QImage:
    """Capture scaled to fit max_w × max_h. Unedited captures are decoded straight at the small
    size, which is much faster than loading the full image and scaling it down."""
    doc = library.load_doc(capture_id)
    if not doc.is_empty():
        return scale_to_fit(render_capture(library, capture_id), max_w, max_h)
    reader = QImageReader(str(library.original_path(capture_id)))
    size = reader.size()
    if size.isValid() and (size.width() > max_w or size.height() > max_h):
        factor = min(max_w / size.width(), max_h / size.height())
        reader.setScaledSize(QSize(max(1, round(size.width() * factor)), max(1, round(size.height() * factor))))
    image = reader.read()
    if image.isNull():
        raise ValueError(f"cannot read {library.original_path(capture_id)}: {reader.errorString()}")
    return image


def render_thumbnail(library, capture_id: str, size: int = 200) -> QImage:
    """Load or build the cached thumbnail (with annotations)."""
    thumb = library.thumb_path(capture_id)
    original = library.original_path(capture_id)
    doc_file = library.doc_path(capture_id)
    try:
        newest = max(original.stat().st_mtime, doc_file.stat().st_mtime if doc_file.exists() else 0)
        if thumb.stat().st_mtime >= newest:
            img = QImage(str(thumb))
            if not img.isNull() and (max(img.width(), img.height()) == size
                                     or (img.width() < size and img.height() < size)):
                return img
    except FileNotFoundError:
        pass
    img = render_preview(library, capture_id, size, size)
    if img.save(str(thumb), "PNG"):
        os.chmod(thumb, 0o600)
    return img


# ------------------------------------------------------------------ export
def supported_formats() -> list[str]:
    have = {bytes(f).decode().lower() for f in QImageWriter.supportedImageFormats()}
    return [k for k in EXPORT_FORMATS if k == "pdf" or k in have or (k == "jpg" and "jpeg" in have)]


def save_image(image: QImage, path, fmt: str | None = None, quality: int = 90) -> Path:
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".") or "png").lower()
    fmt = "jpg" if fmt == "jpeg" else fmt
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unsupported format: {fmt}")
    if fmt == "pdf":
        return save_pdf([image], path)
    if fmt not in supported_formats():
        raise ValueError(f"{fmt.upper()} export needs the Qt image format plugins (qt6-image-formats-plugins)")
    out = image
    if fmt in ("jpg", "bmp"):
        out = QImage(image.size(), QImage.Format.Format_RGB32)
        out.fill(QColor("white"))
        p = QPainter(out)
        p.drawImage(0, 0, image)
        p.end()
    if not out.save(str(path), EXPORT_FORMATS[fmt][0].upper() if fmt != "jpg" else "JPEG",
                    int(quality) if fmt in ("jpg", "webp") else -1):
        raise OSError(f"could not write {path}")
    return path


def save_pdf(images, path) -> Path:
    """One page per image, page size = image size at 96 dpi (OUT-03)."""
    path = Path(path)
    writer = QPdfWriter(str(path))
    writer.setResolution(96)
    writer.setCreator("CatCap")
    painter = None
    for i, image in enumerate(images):
        size = QPageSize(QSizeF(image.width() * 72 / 96, image.height() * 72 / 96), QPageSize.Unit.Point)
        writer.setPageSize(size)
        writer.setPageMargins(QMarginsF(0, 0, 0, 0))
        if painter is None:
            painter = QPainter(writer)
        else:
            writer.newPage()
        target = painter.viewport()
        painter.drawImage(QRectF(0, 0, target.width(), target.height()), image)
    if painter is not None:
        painter.end()
    return path
