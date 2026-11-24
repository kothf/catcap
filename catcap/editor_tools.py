"""Editor tool table: names, shortcuts, default styles, property panels and hints."""

from __future__ import annotations

TOOLS = [
    ("select", "Select and move", "V"),
    ("arrow", "Arrow", "A"),
    ("line", "Line", "L"),
    ("rect", "Rectangle", "R"),
    ("ellipse", "Ellipse", "E"),
    ("text", "Text", "T"),
    ("callout", "Callout", "O"),
    ("highlighter", "Highlighter", "H"),
    ("pen", "Pen", "P"),
    ("step", "Numbered step", "S"),
    ("blur", "Blur", "B"),
    ("pixelate", "Pixelate", "X"),
    ("redact", "Redact (solid box)", "K"),
    ("spotlight", "Spotlight", "G"),
    ("crop", "Crop", "C"),
]
GROUP_BREAKS = {"select", "callout", "step", "spotlight"}

DEFAULT_STYLES = {
    "arrow": {"color": "#e53935", "width": 5, "shadow": True},
    "line": {"color": "#e53935", "width": 4, "dashed": False},
    "rect": {"color": "#e53935", "width": 4, "fill": False, "radius": 4},
    "ellipse": {"color": "#e53935", "width": 4, "fill": False},
    "text": {"color": "#e53935", "font": "Sans Bold", "font_size": 22, "bg": False},
    "callout": {"color": "#e53935", "font": "Sans Bold", "font_size": 20, "width": 3, "bg_color": "#ffffff"},
    "highlighter": {"color": "#ffeb3b", "width": 20},
    "pen": {"color": "#e53935", "width": 4},
    "step": {"color": "#e53935", "size": 34, "style": "number"},
    "blur": {"strength": 12},
    "pixelate": {"strength": 12},
    "redact": {"color": "#000000"},
    "spotlight": {"dim": 0.55},
}
PROPS_FOR = {
    "arrow": ["color", "width", "shadow", "opacity"],
    "line": ["color", "width", "dashed", "shadow", "opacity"],
    "rect": ["color", "width", "fill", "shadow", "opacity"],
    "ellipse": ["color", "width", "fill", "shadow", "opacity"],
    "text": ["color", "font", "bg", "shadow", "opacity"],
    "callout": ["color", "font", "opacity"],
    "highlighter": ["color", "width"],
    "pen": ["color", "width", "shadow", "opacity"],
    "step": ["color", "size", "style", "opacity"],
    "blur": ["strength"],
    "pixelate": ["strength"],
    "redact": ["color"],
    "spotlight": [],
    "select": [],
    "crop": ["aspect"],
}
HINTS = {
    "select": "Click an annotation to select it. Drag to move, Delete removes it, double-click edits text.",
    "arrow": "Drag to draw. Hold Shift for 45° steps.",
    "line": "Drag to draw. Hold Shift for 45° steps.",
    "rect": "Drag to draw. Hold Shift for a square.",
    "ellipse": "Drag to draw. Hold Shift for a circle.",
    "text": "Click where the text should start.",
    "callout": "Drag from the point of interest to where the bubble goes.",
    "highlighter": "Drag across what you want to highlight.",
    "pen": "Drag to draw freehand.",
    "step": "Click to place the next number.",
    "blur": "Drag over anything private.",
    "pixelate": "Drag over anything private. For passwords and other text, Redact is safer: "
                "pixelated text can sometimes be guessed back.",
    "redact": "Drag over anything private. The area is covered with a solid box in the exported image.",
    "spotlight": "Drag the areas to keep bright; the rest is dimmed.",
    "crop": "Drag to choose the area. Enter applies, Esc cancels.",
}
SWATCHES = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa", "#212121", "#ffffff"]
ASPECTS = [("Free", None), ("1:1", 1.0), ("4:3", 4 / 3), ("3:2", 3 / 2), ("16:9", 16 / 9), ("9:16", 9 / 16)]
BAR_ICON = "#E9ECEA"
