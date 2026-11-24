"""Non-destructive annotation document (EDT-02).

A Document describes how to turn the untouched original into the output:
  1. orientation: optional horizontal flip, then rotation (0/90/180/270, clockwise)
  2. uniform scale
  3. annotation items, drawn in the coordinates of the transformed image
  4. optional crop rectangle (also in transformed coordinates)

Everything is plain JSON, so annotations stay editable forever.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field

DOC_VERSION = 1

LINE_KINDS = {"arrow", "line"}
RECT_KINDS = {"rect", "ellipse", "blur", "pixelate", "spotlight"}
PATH_KINDS = {"pen", "highlighter"}
BOX_KINDS = {"text", "callout"}
POINT_KINDS = {"step"}
EFFECT_KINDS = {"blur", "pixelate"}
ALL_KINDS = LINE_KINDS | RECT_KINDS | PATH_KINDS | BOX_KINDS | POINT_KINDS

LENGTH_KEYS = ("width", "size", "font_size", "strength", "radius")


@dataclass
class Document:
    rotate: int = 0
    flip: bool = False
    scale: float = 1.0
    crop: list | None = None
    items: list = field(default_factory=list)

    # -------------------------------------------------------------- serialise
    def to_dict(self) -> dict:
        return {
            "version": DOC_VERSION,
            "rotate": self.rotate,
            "flip": self.flip,
            "scale": self.scale,
            "crop": self.crop,
            "items": self.items,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1)

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        if not isinstance(data, dict):
            raise ValueError("annotation document must be an object")
        if int(data.get("version", 1)) > DOC_VERSION:
            raise ValueError("annotation document is from a newer CatCap")
        items = [i for i in data.get("items", []) if isinstance(i, dict) and i.get("type") in ALL_KINDS]
        crop = data.get("crop")
        return cls(
            rotate=int(data.get("rotate", 0)) % 360,
            flip=bool(data.get("flip", False)),
            scale=float(data.get("scale", 1.0)) or 1.0,
            crop=[float(v) for v in crop] if crop else None,
            items=items,
        )

    @classmethod
    def from_json(cls, text: str) -> "Document":
        return cls.from_dict(json.loads(text))

    def copy(self) -> "Document":
        return Document.from_dict(copy.deepcopy(self.to_dict()))

    def is_empty(self) -> bool:
        return (not self.items and self.rotate == 0 and not self.flip
                and abs(self.scale - 1.0) < 1e-9 and not self.crop)

    # -------------------------------------------------------------- geometry
    def transformed_size(self, ow: int, oh: int) -> tuple[int, int]:
        w, h = (oh, ow) if self.rotate in (90, 270) else (ow, oh)
        return max(1, round(w * self.scale)), max(1, round(h * self.scale))

    def output_size(self, ow: int, oh: int) -> tuple[int, int]:
        if self.crop:
            return max(1, round(self.crop[2])), max(1, round(self.crop[3]))
        return self.transformed_size(ow, oh)

    def crop_offset(self) -> tuple[float, float]:
        return (self.crop[0], self.crop[1]) if self.crop else (0.0, 0.0)

    # -------------------------------------------------------------- image operations
    def _remap(self, fn, k: float = 1.0) -> None:
        self.items = [map_item(i, fn, k) for i in self.items]
        if self.crop:
            self.crop = _map_rect(self.crop, fn)

    def rotate_cw(self, ow: int, oh: int) -> None:
        _w, h = self.transformed_size(ow, oh)
        self._remap(lambda x, y: (h - y, x))
        self.rotate = (self.rotate + 90) % 360

    def rotate_ccw(self, ow: int, oh: int) -> None:
        w, _h = self.transformed_size(ow, oh)
        self._remap(lambda x, y: (y, w - x))
        self.rotate = (self.rotate - 90) % 360

    def flip_horizontal(self, ow: int, oh: int) -> None:
        w, _h = self.transformed_size(ow, oh)
        self._remap(lambda x, y: (w - x, y))
        self.rotate = (-self.rotate) % 360
        self.flip = not self.flip

    def flip_vertical(self, ow: int, oh: int) -> None:
        _w, h = self.transformed_size(ow, oh)
        self._remap(lambda x, y: (x, h - y))
        self.rotate = (180 - self.rotate) % 360
        self.flip = not self.flip

    def set_scale(self, scale: float) -> None:
        scale = max(0.01, min(scale, 16.0))
        k = scale / self.scale
        self._remap(lambda x, y: (x * k, y * k), k)
        self.scale = scale

    def renumber_steps(self) -> None:
        n = 1
        for item in self.items:
            if item["type"] == "step":
                item["n"] = n
                n += 1

    def next_step_number(self) -> int:
        return 1 + sum(1 for i in self.items if i["type"] == "step")


# ---------------------------------------------------------------- item geometry
def _norm_rect(x1, y1, x2, y2) -> list:
    return [min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)]


def _map_rect(rect, fn) -> list:
    x, y, w, h = rect
    ax, ay = fn(x, y)
    bx, by = fn(x + w, y + h)
    return _norm_rect(ax, ay, bx, by)


def map_item(item: dict, fn, k: float = 1.0) -> dict:
    item = copy.deepcopy(item)
    kind = item["type"]
    if kind in LINE_KINDS:
        item["x1"], item["y1"] = fn(item["x1"], item["y1"])
        item["x2"], item["y2"] = fn(item["x2"], item["y2"])
    elif kind in RECT_KINDS:
        item["x"], item["y"], item["w"], item["h"] = _map_rect(
            (item["x"], item["y"], item["w"], item["h"]), fn)
    elif kind in PATH_KINDS:
        item["points"] = [list(fn(px, py)) for px, py in item["points"]]
    elif kind in BOX_KINDS:
        w, h = item.get("w", 0) * k, item.get("h", 0) * k
        cx, cy = fn(item["x"] + item.get("w", 0) / 2, item["y"] + item.get("h", 0) / 2)
        item["x"], item["y"] = cx - w / 2, cy - h / 2
        item["w"], item["h"] = w, h
        if item.get("tail"):
            item["tail"] = list(fn(*item["tail"]))
    elif kind in POINT_KINDS:
        item["x"], item["y"] = fn(item["x"], item["y"])
    if k != 1.0:
        for key in LENGTH_KEYS:
            if key in item:
                item[key] = item[key] * k
    return item


def bbox(item: dict) -> tuple[float, float, float, float]:
    kind = item["type"]
    if kind in LINE_KINDS:
        return tuple(_norm_rect(item["x1"], item["y1"], item["x2"], item["y2"]))
    if kind in RECT_KINDS:
        return item["x"], item["y"], item["w"], item["h"]
    if kind in PATH_KINDS:
        xs = [p[0] for p in item["points"]] or [0]
        ys = [p[1] for p in item["points"]] or [0]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    if kind in BOX_KINDS:
        return item["x"], item["y"], item.get("w", 0), item.get("h", 0)
    if kind in POINT_KINDS:
        r = item.get("size", 32) / 2
        return item["x"] - r, item["y"] - r, 2 * r, 2 * r
    return 0, 0, 0, 0


def _dist_to_segment(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def hit_test(item: dict, x: float, y: float, tol: float = 6.0) -> bool:
    kind = item["type"]
    width = item.get("width", 4)
    if kind in LINE_KINDS:
        return _dist_to_segment(x, y, item["x1"], item["y1"], item["x2"], item["y2"]) <= width / 2 + tol
    if kind in PATH_KINDS:
        pts = item["points"]
        if len(pts) == 1:
            return math.hypot(x - pts[0][0], y - pts[0][1]) <= width / 2 + tol
        return any(
            _dist_to_segment(x, y, *pts[i], *pts[i + 1]) <= width / 2 + tol for i in range(len(pts) - 1)
        )
    bx, by, bw, bh = bbox(item)
    inside = bx - tol <= x <= bx + bw + tol and by - tol <= y <= by + bh + tol
    if kind in ("rect", "ellipse") and not item.get("fill") and inside:
        inner = bx + tol + width <= x <= bx + bw - tol - width and by + tol + width <= y <= by + bh - tol - width
        return not inner
    if kind == "callout" and not inside and item.get("tail"):
        tx, ty = item["tail"]
        return _dist_to_segment(x, y, bx + bw / 2, by + bh / 2, tx, ty) <= tol + 4
    return inside


def handles(item: dict) -> list[tuple[str, float, float]]:
    kind = item["type"]
    if kind in LINE_KINDS:
        return [("p1", item["x1"], item["y1"]), ("p2", item["x2"], item["y2"])]
    if kind in RECT_KINDS:
        x, y, w, h = bbox(item)
        return [("nw", x, y), ("ne", x + w, y), ("sw", x, y + h), ("se", x + w, y + h)]
    if kind == "callout" and item.get("tail"):
        return [("tail", *item["tail"])]
    return []


def drag_handle(item: dict, name: str, x: float, y: float) -> None:
    kind = item["type"]
    if kind in LINE_KINDS:
        if name == "p1":
            item["x1"], item["y1"] = x, y
        else:
            item["x2"], item["y2"] = x, y
    elif kind in RECT_KINDS:
        x1, y1 = item["x"], item["y"]
        x2, y2 = x1 + item["w"], y1 + item["h"]
        if "n" in name:
            y1 = y
        if "s" in name:
            y2 = y
        if "w" in name:
            x1 = x
        if "e" in name:
            x2 = x
        item["x"], item["y"], item["w"], item["h"] = _norm_rect(x1, y1, x2, y2)
    elif name == "tail":
        item["tail"] = [x, y]


def move_item(item: dict, dx: float, dy: float) -> None:
    kind = item["type"]
    if kind in LINE_KINDS:
        item["x1"] += dx
        item["x2"] += dx
        item["y1"] += dy
        item["y2"] += dy
    elif kind in PATH_KINDS:
        item["points"] = [[px + dx, py + dy] for px, py in item["points"]]
    else:
        item["x"] += dx
        item["y"] += dy
        if item.get("tail"):
            item["tail"] = [item["tail"][0] + dx, item["tail"][1] + dy]


def step_label(n: int, style: str = "number") -> str:
    if style == "letter":
        label = ""
        n = max(1, int(n))
        while n:
            n, rem = divmod(n - 1, 26)
            label = chr(65 + rem) + label
        return label
    return str(int(n))
