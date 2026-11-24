"""Editor window (EDT-*, ANN-*, IMG-*)."""

from __future__ import annotations

import copy
import json
import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import annotations as ann  # noqa: E402
from . import icons, render, ui  # noqa: E402
from .annotations import Document  # noqa: E402

TOOLS = [
    ("select", "Select and move (V)", "v"),
    ("arrow", "Arrow (A)", "a"),
    ("line", "Line (L)", "l"),
    ("rect", "Rectangle (R)", "r"),
    ("ellipse", "Ellipse (E)", "e"),
    ("text", "Text (T)", "t"),
    ("callout", "Callout: drag from the point to the bubble (O)", "o"),
    ("highlighter", "Highlighter (H)", "h"),
    ("pen", "Pen (P)", "p"),
    ("step", "Numbered step (S)", "s"),
    ("blur", "Blur (B)", "b"),
    ("pixelate", "Pixelate (X)", "x"),
    ("spotlight", "Spotlight (G)", "g"),
    ("crop", "Crop (C)", "c"),
]

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
    "spotlight": {"dim": 0.55},
}

PROPS_FOR = {
    "arrow": {"color", "width", "shadow", "opacity"},
    "line": {"color", "width", "dashed", "shadow", "opacity"},
    "rect": {"color", "width", "fill", "shadow", "opacity"},
    "ellipse": {"color", "width", "fill", "shadow", "opacity"},
    "text": {"color", "font", "bg", "shadow", "opacity"},
    "callout": {"color", "font", "opacity"},
    "highlighter": {"color", "width"},
    "pen": {"color", "width", "shadow", "opacity"},
    "step": {"color", "size", "style", "opacity"},
    "blur": {"strength"},
    "pixelate": {"strength"},
    "spotlight": set(),
    "select": set(),
    "crop": {"aspect"},
}

ASPECTS = [("Free", None), ("1:1", 1.0), ("4:3", 4 / 3), ("3:2", 3 / 2), ("16:9", 16 / 9), ("9:16", 9 / 16)]


class Editor(Gtk.ApplicationWindow):
    def __init__(self, app, capture_id: str):
        super().__init__(application=app)
        self.app = app
        self.library = app.library
        self.config = app.config
        self.capture_id = capture_id
        self.cap = self.library.get(capture_id)
        self.src = render.load_original(self.library.original_path(capture_id))
        self.doc: Document = self.library.load_doc(capture_id)
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self.tool = "select"
        self.selected: dict | None = None
        self.active: dict | None = None   # item being created or dragged
        self.drag = None                   # dict describing the current drag
        self.crop_draft: list | None = None
        self.aspect = None
        self.zoom = 1.0
        self.fit_mode = True
        self.base = None
        self.rendered = None
        self._save_source = 0
        self._updating_props = False
        self._pan = None
        self.styles = copy.deepcopy(DEFAULT_STYLES)
        try:
            saved = json.loads(self.config.get("editor", "styles") or "{}")
            for k, v in saved.items():
                if k in self.styles and isinstance(v, dict):
                    self.styles[k].update(v)
        except (ValueError, TypeError):
            pass

        self._build()
        self._rebuild_base()
        self._set_tool("select")
        self._update_title()
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_delete)
        self.library.connect(self._on_library_event)
        self.connect("destroy", lambda *_: self.library.disconnect(self._on_library_event))

    # ================================================================ build UI
    def _build(self):
        geom = Gdk.Display.get_default().get_monitor(0).get_workarea()
        self.set_default_size(min(1280, int(geom.width * 0.85)), min(860, int(geom.height * 0.85)))
        self.set_icon_name("io.github.kothf.CatCap")

        header = Gtk.HeaderBar(show_close_button=True)
        self.set_titlebar(header)
        self.undo_btn = self._icon_button("edit-undo-symbolic", "Undo (Ctrl+Z)", self.undo)
        self.redo_btn = self._icon_button("edit-redo-symbolic", "Redo (Ctrl+Shift+Z)", self.redo)
        header.pack_start(self.undo_btn)
        header.pack_start(self.redo_btn)

        library_btn = self._icon_button("view-grid-symbolic", "Open library", lambda *_: self.app.show_library(self.capture_id))
        header.pack_end(library_btn)
        menu_btn = Gtk.MenuButton(image=Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON),
                                  tooltip_text="More")
        menu_btn.set_popup(self._build_menu())
        header.pack_end(menu_btn)
        pin = self._icon_button("view-pin-symbolic", "Pin to screen", lambda *_: self.app.pin_surface(self.render_output()))
        header.pack_end(pin)
        export = Gtk.Button(label="Export…", tooltip_text="Save as PNG, JPG, WebP, BMP or PDF (Ctrl+Shift+S)")
        export.connect("clicked", lambda *_: self.export())
        header.pack_end(export)
        copy_btn = Gtk.Button(label="Copy", tooltip_text="Copy image to clipboard (Ctrl+C)")
        copy_btn.get_style_context().add_class("suggested-action")
        copy_btn.connect("clicked", lambda *_: self.copy())
        header.pack_end(copy_btn)

        drag = Gtk.EventBox(tooltip_text="Drag this into another app (file manager, browser, chat)")
        drag_box = Gtk.Box(spacing=4)
        drag_box.add(icons.tool_image("drag", header))
        drag_box.add(Gtk.Label(label="Drag"))
        drag.add(drag_box)
        drag.get_style_context().add_class("catcap-drag")
        drag.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [ui.URI_TARGET], Gdk.DragAction.COPY)
        drag.connect("drag-data-get", self._on_drag_data_get)
        drag.connect("drag-begin", lambda w, ctx: Gtk.drag_set_icon_name(ctx, "image-x-generic", 0, 0))
        header.pack_end(drag)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        root.add(self._build_props())
        root.add(Gtk.Separator())

        body = Gtk.Box()
        root.pack_start(body, True, True, 0)
        body.add(self._build_tools())
        body.add(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.canvas = Gtk.DrawingArea(can_focus=True)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                               | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK
                               | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.canvas.connect("draw", self._on_draw)
        self.canvas.connect("button-press-event", self._on_press)
        self.canvas.connect("button-release-event", self._on_release)
        self.canvas.connect("motion-notify-event", self._on_motion)
        self.canvas.connect("scroll-event", self._on_scroll)
        self.canvas.connect("size-allocate", self._on_canvas_allocate)
        self.canvas.get_style_context().add_class("catcap-canvas")
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.add(self.canvas)
        body.pack_start(self.scroller, True, True, 0)

        status = Gtk.Box(spacing=12, margin=4, margin_start=10, margin_end=10)
        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("catcap-dim")
        status.pack_start(self.status, True, True, 0)
        zoom_out = self._icon_button("zoom-out-symbolic", "Zoom out (Ctrl+-)", lambda *_: self.set_zoom(self.zoom / 1.25))
        zoom_in = self._icon_button("zoom-in-symbolic", "Zoom in (Ctrl++)", lambda *_: self.set_zoom(self.zoom * 1.25))
        self.zoom_label = Gtk.Button(label="100%", tooltip_text="Actual size (Ctrl+1)", relief=Gtk.ReliefStyle.NONE)
        self.zoom_label.connect("clicked", lambda *_: self.set_zoom(1.0))
        fit = self._icon_button("zoom-fit-best-symbolic", "Fit to window (Ctrl+0)", lambda *_: self.zoom_fit())
        for w in (fit, zoom_in, self.zoom_label, zoom_out):
            w.set_relief(Gtk.ReliefStyle.NONE)
            status.pack_end(w, False, False, 0)
        root.add(Gtk.Separator())
        root.add(status)
        root.show_all()
        header.show_all()
        self._sync_props_visibility()

    def _icon_button(self, icon, tip, cb):
        btn = Gtk.Button(image=Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), tooltip_text=tip)
        btn.connect("clicked", cb)
        return btn

    def _build_menu(self):
        menu = Gtk.Menu()
        entries = [
            ("Rotate left", lambda: self.image_op("rotate_ccw")),
            ("Rotate right", lambda: self.image_op("rotate_cw")),
            ("Flip horizontally", lambda: self.image_op("flip_h")),
            ("Flip vertically", lambda: self.image_op("flip_v")),
            ("Resize image…", self.resize_dialog),
            (None, None),
            ("Export as PDF…", lambda: ui.export_pdf_dialog(self, self.library, self.config, [self.capture_id])),
            ("Open with…", lambda: ui.open_with(self, ui.temp_render(self.library, self.capture_id, self.config))),
            ("Send by email…", lambda: ui.send_email(ui.temp_render(self.library, self.capture_id, self.config))),
            ("Show original in file manager", lambda: ui.show_in_folder(self.library.original_path(self.capture_id))),
            (None, None),
            ("Revert to original", self.revert),
            ("Keyboard shortcuts", lambda: self.app.show_shortcuts(self)),
        ]
        for label, cb in entries:
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _w, c=cb: c())
            menu.append(item)
        menu.show_all()
        return menu

    def _build_tools(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin=4)
        box.get_style_context().add_class("catcap-toolbar")
        self.tool_buttons = {}
        group = None
        for name, tip, _key in TOOLS:
            btn = Gtk.RadioButton.new_from_widget(group)
            group = group or btn
            btn.set_mode(False)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_tooltip_text(tip)
            btn.set_image(icons.tool_image(name, None))
            btn.connect("style-updated", lambda b, n=name: b.set_image(icons.tool_image(n, b)))
            btn.connect("toggled", self._on_tool_toggled, name)
            box.add(btn)
            self.tool_buttons[name] = btn
            if name in ("select", "callout", "step", "spotlight"):
                box.add(Gtk.Separator(margin_top=3, margin_bottom=3))
        return box

    def _build_props(self):
        bar = Gtk.Box(spacing=8, margin=6, margin_start=10)
        self.prop_widgets = {}

        def add(key, label, widget):
            wrap = Gtk.Box(spacing=4)
            if label:
                wrap.add(Gtk.Label(label=label))
            wrap.add(widget)
            bar.add(wrap)
            self.prop_widgets[key] = wrap
            return widget

        self.tool_label = Gtk.Label(xalign=0, width_chars=10)
        self.tool_label.get_style_context().add_class("catcap-preview-title")
        bar.add(self.tool_label)
        self.color_btn = add("color", "", Gtk.ColorButton(tooltip_text="Colour"))
        self.color_btn.connect("color-set", lambda *_: self._prop_changed("color"))
        self.width_spin = add("width", "Width", Gtk.SpinButton.new_with_range(1, 60, 1))
        self.width_spin.connect("value-changed", lambda *_: self._prop_changed("width"))
        self.size_spin = add("size", "Size", Gtk.SpinButton.new_with_range(12, 160, 2))
        self.size_spin.connect("value-changed", lambda *_: self._prop_changed("size"))
        self.style_combo = add("style", "", Gtk.ComboBoxText())
        self.style_combo.append("number", "1, 2, 3")
        self.style_combo.append("letter", "A, B, C")
        self.style_combo.connect("changed", lambda *_: self._prop_changed("style"))
        self.font_btn = add("font", "", Gtk.FontButton(tooltip_text="Font"))
        self.font_btn.set_use_size(False)
        self.font_btn.connect("font-set", lambda *_: self._prop_changed("font"))
        self.strength_spin = add("strength", "Strength", Gtk.SpinButton.new_with_range(2, 60, 1))
        self.strength_spin.connect("value-changed", lambda *_: self._prop_changed("strength"))
        self.fill_check = add("fill", "", Gtk.CheckButton(label="Fill"))
        self.fill_check.connect("toggled", lambda *_: self._prop_changed("fill"))
        self.bg_check = add("bg", "", Gtk.CheckButton(label="Background"))
        self.bg_check.connect("toggled", lambda *_: self._prop_changed("bg"))
        self.dashed_check = add("dashed", "", Gtk.CheckButton(label="Dashed"))
        self.dashed_check.connect("toggled", lambda *_: self._prop_changed("dashed"))
        self.shadow_check = add("shadow", "", Gtk.CheckButton(label="Shadow"))
        self.shadow_check.connect("toggled", lambda *_: self._prop_changed("shadow"))
        self.opacity_scale = add("opacity", "Opacity", Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 10, 100, 5))
        self.opacity_scale.set_size_request(90, -1)
        self.opacity_scale.set_draw_value(False)
        self.opacity_scale.set_value(100)
        self.opacity_scale.connect("value-changed", lambda *_: self._prop_changed("opacity"))

        crop_box = Gtk.Box(spacing=6)
        self.aspect_combo = Gtk.ComboBoxText(tooltip_text="Aspect ratio")
        for label, _v in ASPECTS:
            self.aspect_combo.append_text(label)
        self.aspect_combo.set_active(0)
        self.aspect_combo.connect("changed", self._on_aspect)
        apply_btn = Gtk.Button(label="Apply crop")
        apply_btn.get_style_context().add_class("suggested-action")
        apply_btn.connect("clicked", lambda *_: self.apply_crop())
        reset_btn = Gtk.Button(label="Remove crop")
        reset_btn.connect("clicked", lambda *_: self.reset_crop())
        crop_box.add(self.aspect_combo)
        crop_box.add(apply_btn)
        crop_box.add(reset_btn)
        bar.add(crop_box)
        self.prop_widgets["aspect"] = crop_box

        self.hint = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, width_chars=10)
        self.hint.get_style_context().add_class("catcap-dim")
        bar.pack_start(self.hint, True, True, 0)

        ops = Gtk.Box(spacing=0)
        ops.get_style_context().add_class("linked")
        for icon, tip, op in (("object-rotate-left-symbolic", "Rotate left", "rotate_ccw"),
                              ("object-rotate-right-symbolic", "Rotate right", "rotate_cw"),
                              ("object-flip-horizontal-symbolic", "Flip horizontally", "flip_h"),
                              ("object-flip-vertical-symbolic", "Flip vertically", "flip_v")):
            ops.add(self._icon_button(icon, tip, lambda _b, o=op: self.image_op(o)))
        resize = Gtk.Button(label="Resize…", tooltip_text="Resize image")
        resize.connect("clicked", lambda *_: self.resize_dialog())
        ops.add(resize)
        bar.pack_end(ops, False, False, 0)
        return bar

    # ================================================================ tools & props
    def _on_tool_toggled(self, btn, name):
        if btn.get_active():
            self._set_tool(name, from_button=True)

    def _set_tool(self, name, from_button=False):
        if self.tool == "crop" and name != "crop":
            self.crop_draft = None
        self.tool = name
        if not from_button:
            self.tool_buttons[name].set_active(True)
        if name == "crop":
            w, h = self.doc.transformed_size(self.src.get_width(), self.src.get_height())
            self.crop_draft = list(self.doc.crop) if self.doc.crop else [0, 0, w, h]
            self.selected = None
        elif name != "select":
            self.selected = None
        self.tool_label.set_text(dict((t, tip.split(" (")[0].split(":")[0]) for t, tip, _k in TOOLS)[name])
        hints = {
            "select": "Click an object to select it; drag to move; Delete removes it; double-click edits text",
            "arrow": "Drag to draw · Shift snaps to 45°",
            "line": "Drag to draw · Shift snaps to 45°",
            "rect": "Drag to draw · Shift makes a square",
            "ellipse": "Drag to draw · Shift makes a circle",
            "text": "Click where the text should go",
            "callout": "Drag from the point of interest to where the bubble goes",
            "highlighter": "Drag to highlight",
            "pen": "Drag to draw freehand",
            "step": "Click to place the next number",
            "blur": "Drag over sensitive content",
            "pixelate": "Drag over sensitive content",
            "spotlight": "Drag areas to keep bright; the rest is dimmed",
            "crop": "Drag to choose the area · Enter applies · Esc cancels",
        }
        self.hint.set_text(hints.get(name, ""))
        self._load_props(self.styles.get(name, {}))
        self._sync_props_visibility()
        cursor = "default" if name == "select" else "crosshair"
        if name == "text":
            cursor = "text"
        win = self.canvas.get_window()
        if win is not None:
            win.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), cursor))
        self.canvas.queue_draw()

    def _sync_props_visibility(self):
        kind = self.selected["type"] if (self.tool == "select" and self.selected) else self.tool
        wanted = PROPS_FOR.get(kind, set())
        for key, wrap in self.prop_widgets.items():
            wrap.set_visible(key in wanted)

    def _load_props(self, style: dict):
        self._updating_props = True
        try:
            if "color" in style:
                rgba = Gdk.RGBA()
                rgba.parse(style["color"])
                self.color_btn.set_rgba(rgba)
            if "width" in style:
                self.width_spin.set_value(style["width"])
            if "size" in style:
                self.size_spin.set_value(style["size"])
            if "style" in style:
                self.style_combo.set_active_id(style["style"])
            if "font" in style:
                self.font_btn.set_font(f"{style['font']} {int(style.get('font_size', 22))}")
            if "strength" in style:
                self.strength_spin.set_value(style["strength"])
            self.fill_check.set_active(bool(style.get("fill")))
            self.bg_check.set_active(bool(style.get("bg")))
            self.dashed_check.set_active(bool(style.get("dashed")))
            self.shadow_check.set_active(bool(style.get("shadow")))
            self.opacity_scale.set_value(100 * style.get("opacity", 1.0))
        finally:
            self._updating_props = False

    def _read_prop(self, key):
        if key == "color":
            c = self.color_btn.get_rgba()
            return {"color": "#%02x%02x%02x" % (int(c.red * 255), int(c.green * 255), int(c.blue * 255))}
        if key == "width":
            return {"width": self.width_spin.get_value()}
        if key == "size":
            return {"size": self.size_spin.get_value()}
        if key == "style":
            return {"style": self.style_combo.get_active_id() or "number"}
        if key == "font":
            desc = self.font_btn.get_font_desc()
            size = desc.get_size() / 1024 if desc.get_size() else 22
            desc.unset_fields(Pango.FontMask.SIZE)
            return {"font": desc.to_string(), "font_size": max(8, size * 1.33)}
        if key == "strength":
            return {"strength": self.strength_spin.get_value()}
        if key == "fill":
            return {"fill": self.fill_check.get_active()}
        if key == "bg":
            return {"bg": self.bg_check.get_active()}
        if key == "dashed":
            return {"dashed": self.dashed_check.get_active()}
        if key == "shadow":
            return {"shadow": self.shadow_check.get_active()}
        if key == "opacity":
            return {"opacity": self.opacity_scale.get_value() / 100}
        return {}

    def _prop_changed(self, key):
        if self._updating_props:
            return
        values = self._read_prop(key)
        if self.tool == "select" and self.selected is not None:
            self._checkpoint()
            self.selected.update(values)
            if self.selected["type"] in ("text", "callout"):
                render.measure_text(self.selected)
            self._commit()
            kind = self.selected["type"]
        else:
            kind = self.tool
        if kind in self.styles:
            self.styles[kind].update(values)
            self.config.set("editor", "styles", json.dumps(self.styles))

    def _on_aspect(self, combo):
        self.aspect = ASPECTS[combo.get_active()][1]
        if self.crop_draft and self.aspect:
            x, y, w, h = self.crop_draft
            self.crop_draft = self._clamp_rect([x, y, w, w / self.aspect])
        self.canvas.queue_draw()

    # ================================================================ document / history
    def _checkpoint(self):
        self.undo_stack.append(self.doc.to_json())
        if len(self.undo_stack) > 500:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _restore(self, text):
        sel_index = self.doc.items.index(self.selected) if self.selected in self.doc.items else None
        self.doc = Document.from_json(text)
        self.selected = self.doc.items[sel_index] if sel_index is not None and sel_index < len(self.doc.items) else None
        self._rebuild_base()
        self._schedule_save()

    def undo(self, *_):
        if self.undo_stack:
            self.redo_stack.append(self.doc.to_json())
            self._restore(self.undo_stack.pop())

    def redo(self, *_):
        if self.redo_stack:
            self.undo_stack.append(self.doc.to_json())
            self._restore(self.redo_stack.pop())

    def _commit(self):
        """Document changed: re-render and schedule autosave (EDT-07)."""
        self._render_items()
        self._schedule_save()

    def _schedule_save(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(500, self._save_now)
        self._update_buttons()

    def _save_now(self):
        self._save_source = 0
        try:
            self.library.save_doc(self.capture_id, self.doc)
        except Exception as exc:
            self.status.set_text(f"Could not save: {exc}")
        return False

    def _rebuild_base(self):
        self.base = render.transformed_surface(self.src, self.doc)
        self._render_items()
        if self.fit_mode:
            GLib.idle_add(self.zoom_fit)
        else:
            self._update_canvas_size()

    def _render_items(self, exclude=None):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.base.get_width(), self.base.get_height())
        ctx = cairo.Context(surface)
        ctx.set_source_surface(self.base, 0, 0)
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.paint()
        items = [i for i in self.doc.items if i is not exclude]
        render.draw_items(surface, items)
        self.rendered = surface
        self._update_buttons()
        self._update_status()
        self.canvas.queue_draw()

    def _update_buttons(self):
        self.undo_btn.set_sensitive(bool(self.undo_stack))
        self.redo_btn.set_sensitive(bool(self.redo_stack))

    def _update_title(self):
        self.set_title(f"{self.cap.name} — CatCap")

    def _update_status(self, pos=None):
        w, h = self.doc.output_size(self.src.get_width(), self.src.get_height())
        text = f"{w} × {h} px"
        if self.doc.items:
            text += f"  ·  {len(self.doc.items)} annotation{'s' if len(self.doc.items) != 1 else ''}"
        if pos:
            text += f"  ·  {int(pos[0])}, {int(pos[1])}"
        self.status.set_text(text)

    def render_output(self):
        return render.render(self.src, self.doc)

    # ================================================================ view mapping
    def _view_rect(self):
        """(cx, cy, w, h): part of the transformed image currently shown."""
        tw, th = self.base.get_width(), self.base.get_height()
        if self.doc.crop and self.tool != "crop":
            x, y, w, h = self.doc.crop
            return x, y, w, h
        return 0, 0, tw, th

    def _origin(self):
        _cx, _cy, w, h = self._view_rect()
        alloc = self.canvas.get_allocation()
        return max(0, (alloc.width - w * self.zoom) / 2), max(0, (alloc.height - h * self.zoom) / 2)

    def to_doc(self, vx, vy):
        cx, cy, _w, _h = self._view_rect()
        ox, oy = self._origin()
        return (vx - ox) / self.zoom + cx, (vy - oy) / self.zoom + cy

    def set_zoom(self, z, fit=False):
        self.fit_mode = fit
        self.zoom = max(0.05, min(z, 16.0))
        self.zoom_label.set_label(f"{round(self.zoom * 100)}%")
        self._update_canvas_size()

    def zoom_fit(self):
        _cx, _cy, w, h = self._view_rect()
        alloc = self.scroller.get_allocation()
        if alloc.width < 50:
            return False
        z = min((alloc.width - 24) / w, (alloc.height - 24) / h, 1.0)
        self.set_zoom(z, fit=True)
        return False

    def _update_canvas_size(self):
        _cx, _cy, w, h = self._view_rect()
        self.canvas.set_size_request(int(w * self.zoom) + 2, int(h * self.zoom) + 2)
        self.canvas.queue_draw()

    def _on_canvas_allocate(self, *_):
        if self.fit_mode:
            GLib.idle_add(self.zoom_fit)

    # ================================================================ drawing
    def _on_draw(self, _w, cr):
        cx, cy, w, h = self._view_rect()
        ox, oy = self._origin()
        cr.save()
        cr.translate(ox, oy)
        cr.scale(self.zoom, self.zoom)
        cr.translate(-cx, -cy)
        cr.rectangle(cx, cy, w, h)
        cr.clip()
        cr.set_source_surface(self.rendered, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_NEAREST if self.zoom >= 2 else cairo.FILTER_GOOD)
        cr.paint()
        if self.active is not None:
            kind = self.active["type"]
            if kind in ann.EFFECT_KINDS or kind == "spotlight":
                self._dashed_rect(cr, ann.bbox(self.active))
            else:
                render.draw_item(cr, self.active)
        if self.selected is not None and self.tool == "select":
            x, y, bw, bh = ann.bbox(self.selected)
            pad = 4 / self.zoom
            self._dashed_rect(cr, (x - pad, y - pad, bw + 2 * pad, bh + 2 * pad))
            for _n, hx, hy in ann.handles(self.selected):
                self._handle(cr, hx, hy)
        if self.tool == "crop" and self.crop_draft:
            x, y, bw, bh = self.crop_draft
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.rectangle(cx, cy, w, h)
            cr.rectangle(x, y, bw, bh)
            cr.set_source_rgba(0, 0, 0, 0.5)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(1.5 / self.zoom)
            cr.rectangle(x, y, bw, bh)
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.35)
            for i in (1, 2):
                cr.move_to(x + bw * i / 3, y)
                cr.line_to(x + bw * i / 3, y + bh)
                cr.move_to(x, y + bh * i / 3)
                cr.line_to(x + bw, y + bh * i / 3)
            cr.stroke()
            for _n, hx, hy in self._crop_handles():
                self._handle(cr, hx, hy)
        cr.restore()

    def _dashed_rect(self, cr, rect):
        cr.save()
        cr.set_line_width(1.5 / self.zoom)
        cr.set_dash([5 / self.zoom, 4 / self.zoom])
        cr.rectangle(*rect)
        cr.set_source_rgb(1, 1, 1)
        cr.stroke_preserve()
        cr.set_dash([5 / self.zoom, 4 / self.zoom], 5 / self.zoom)
        cr.set_source_rgb(0.2, 0.45, 0.9)
        cr.stroke()
        cr.restore()

    def _handle(self, cr, x, y):
        r = 5 / self.zoom
        cr.rectangle(x - r, y - r, 2 * r, 2 * r)
        cr.set_source_rgb(1, 1, 1)
        cr.fill_preserve()
        cr.set_source_rgb(0.2, 0.45, 0.9)
        cr.set_line_width(1.5 / self.zoom)
        cr.stroke()

    def _crop_handles(self):
        if not self.crop_draft:
            return []
        return ann.handles({"type": "rect", "x": self.crop_draft[0], "y": self.crop_draft[1],
                            "w": self.crop_draft[2], "h": self.crop_draft[3]})

    # ================================================================ mouse
    def _hit_handle(self, handles, x, y):
        tol = 8 / self.zoom
        for name, hx, hy in handles:
            if abs(x - hx) <= tol and abs(y - hy) <= tol:
                return name
        return None

    def _item_at(self, x, y):
        tol = 6 / self.zoom
        for item in reversed(self.doc.items):
            if ann.hit_test(item, x, y, tol):
                return item
        return None

    def _new_item(self, kind, x, y):
        style = copy.deepcopy(self.styles.get(kind, {}))
        if kind == "callout":
            return {"type": kind, "x": x, "y": y, "text": "", **style}
        if kind in ann.LINE_KINDS:
            return {"type": kind, "x1": x, "y1": y, "x2": x, "y2": y, **style}
        if kind in ann.RECT_KINDS:
            return {"type": kind, "x": x, "y": y, "w": 0, "h": 0, **style}
        if kind in ann.PATH_KINDS:
            return {"type": kind, "points": [[x, y]], **style}
        return {"type": kind, "x": x, "y": y, **style}

    def _on_press(self, _w, ev):
        self.canvas.grab_focus()
        x, y = self.to_doc(ev.x, ev.y)
        if ev.button == 2 or (ev.button == 1 and ev.state & Gdk.ModifierType.MOD1_MASK):
            self._pan = (ev.x_root, ev.y_root, self.scroller.get_hadjustment().get_value(),
                         self.scroller.get_vadjustment().get_value())
            return True
        if ev.button == 3:
            self._context_menu(ev, x, y)
            return True
        if ev.button != 1:
            return True
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            item = self._item_at(x, y)
            if item is not None and item["type"] in ann.BOX_KINDS:
                self.edit_text(item)
            elif self.tool == "crop":
                self.apply_crop()
            return True
        tool = self.tool
        if tool == "select":
            if self.selected is not None:
                handle = self._hit_handle(ann.handles(self.selected), x, y)
                if handle:
                    self._checkpoint()
                    self.drag = {"mode": "handle", "name": handle}
                    self._begin_live(self.selected)
                    return True
            item = self._item_at(x, y)
            self.selected = item
            self._load_props(item or {})
            self._sync_props_visibility()
            if item is not None:
                self._checkpoint()
                self.drag = {"mode": "move", "last": (x, y), "moved": False}
                self._begin_live(item)
            self.canvas.queue_draw()
            return True
        if tool == "crop":
            handle = self._hit_handle(self._crop_handles(), x, y)
            if handle:
                self.drag = {"mode": "crop-handle", "name": handle}
            elif self.crop_draft and self._in_rect(self.crop_draft, x, y):
                self.drag = {"mode": "crop-move", "last": (x, y)}
            else:
                self.drag = {"mode": "crop-new", "start": (x, y)}
                self.crop_draft = [x, y, 0, 0]
            return True
        if tool == "text":
            self.add_text(x, y)
            return True
        if tool == "step":
            self._checkpoint()
            item = self._new_item("step", x, y)
            item["n"] = self.doc.next_step_number()
            self.doc.items.append(item)
            self.selected = item
            self._commit()
            return True
        item = self._new_item(tool, x, y)
        if tool == "callout":
            item["tail"] = [x, y]
            item["text"] = "…"
        self.active = item
        self.drag = {"mode": "create", "start": (x, y)}
        return True

    def _in_rect(self, rect, x, y):
        return rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]

    def _begin_live(self, item):
        self.active = item
        self._render_items(exclude=item)

    def _constrain(self, sx, sy, x, y, shift, kind):
        if not shift:
            return x, y
        dx, dy = x - sx, y - sy
        if kind in ann.LINE_KINDS:
            ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            length = math.hypot(dx, dy)
            return sx + math.cos(ang) * length, sy + math.sin(ang) * length
        side = max(abs(dx), abs(dy))
        return sx + math.copysign(side, dx or 1), sy + math.copysign(side, dy or 1)

    def _on_motion(self, _w, ev):
        x, y = self.to_doc(ev.x, ev.y)
        self._update_status((x, y))
        if self._pan is not None:
            rx, ry, hv, vv = self._pan
            self.scroller.get_hadjustment().set_value(hv - (ev.x_root - rx))
            self.scroller.get_vadjustment().set_value(vv - (ev.y_root - ry))
            return True
        if not self.drag:
            if self.tool == "select":
                over = self._item_at(x, y) is not None or (
                    self.selected is not None and self._hit_handle(ann.handles(self.selected), x, y))
                win = self.canvas.get_window()
                win.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), "move" if over else "default"))
            return True
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        mode = self.drag["mode"]
        if mode == "create" and self.active is not None:
            sx, sy = self.drag["start"]
            kind = self.active["type"]
            if kind in ann.LINE_KINDS:
                self.active["x2"], self.active["y2"] = self._constrain(sx, sy, x, y, shift, kind)
            elif kind in ann.RECT_KINDS:
                ex, ey = self._constrain(sx, sy, x, y, shift, kind)
                self.active.update(dict(zip("xywh", ann._norm_rect(sx, sy, ex, ey))))
            elif kind in ann.PATH_KINDS:
                last = self.active["points"][-1]
                if math.hypot(x - last[0], y - last[1]) * self.zoom >= 2:
                    self.active["points"].append([x, y])
            elif kind == "callout":
                self.active["x"], self.active["y"] = x, y
        elif mode == "move":
            lx, ly = self.drag["last"]
            ann.move_item(self.selected, x - lx, y - ly)
            self.drag["last"] = (x, y)
            self.drag["moved"] = True
        elif mode == "handle":
            ann.drag_handle(self.selected, self.drag["name"], x, y)
        elif mode == "crop-new":
            sx, sy = self.drag["start"]
            ex, ey = x, y
            if self.aspect:
                w = abs(ex - sx)
                ey = sy + math.copysign(w / self.aspect, (ey - sy) or 1)
            self.crop_draft = self._clamp_rect(ann._norm_rect(sx, sy, ex, ey))
        elif mode == "crop-move":
            lx, ly = self.drag["last"]
            cx, cy, cw, ch = self.crop_draft
            self.crop_draft = self._clamp_rect([cx + x - lx, cy + y - ly, cw, ch], keep_size=True)
            self.drag["last"] = (x, y)
        elif mode == "crop-handle":
            item = {"type": "rect", "x": self.crop_draft[0], "y": self.crop_draft[1],
                    "w": self.crop_draft[2], "h": self.crop_draft[3]}
            ann.drag_handle(item, self.drag["name"], x, y)
            rect = [item["x"], item["y"], item["w"], item["h"]]
            if self.aspect:
                rect[3] = rect[2] / self.aspect
            self.crop_draft = self._clamp_rect(rect)
        self.canvas.queue_draw()
        return True

    def _clamp_rect(self, rect, keep_size=False):
        tw, th = self.base.get_width(), self.base.get_height()
        x, y, w, h = rect
        if keep_size:
            x = max(0, min(x, tw - w))
            y = max(0, min(y, th - h))
            return [x, y, w, h]
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(tw, x + w), min(th, y + h)
        return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]

    def _on_release(self, _w, ev):
        if self._pan is not None:
            self._pan = None
            return True
        if not self.drag:
            return True
        mode = self.drag["mode"]
        moved = self.drag.get("moved", True)
        self.drag = None
        if mode == "create" and self.active is not None:
            item, self.active = self.active, None
            if self._big_enough(item):
                if item["type"] == "callout":
                    self._finish_callout(item)
                    return True
                self._checkpoint()
                self.doc.items.append(item)
                self.selected = item
                self._commit()
            else:
                self.canvas.queue_draw()
        elif mode in ("move", "handle"):
            item, self.active = self.active, None
            if mode == "move" and not moved and self.undo_stack:
                self.undo_stack.pop()
            if item is not None and item["type"] in ann.BOX_KINDS:
                render.measure_text(item)
            self._commit()
        return True

    def _big_enough(self, item):
        kind = item["type"]
        min_px = 3 / self.zoom
        if kind in ann.LINE_KINDS:
            return math.hypot(item["x2"] - item["x1"], item["y2"] - item["y1"]) >= min_px
        if kind in ann.RECT_KINDS:
            return item["w"] >= min_px and item["h"] >= min_px
        if kind == "callout":
            return True
        return True

    def _on_scroll(self, _w, ev):
        if not ev.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        ok, _dx, dy = ev.get_scroll_deltas()
        direction = -dy if ok and dy else (1 if ev.direction == Gdk.ScrollDirection.UP else -1)
        self.set_zoom(self.zoom * (1.15 if direction > 0 else 1 / 1.15))
        return True

    def _context_menu(self, ev, x, y):
        item = self._item_at(x, y)
        menu = Gtk.Menu()
        if item is not None:
            self.selected = item
            self._set_tool("select")
            self.selected = item
            for label, cb in (("Duplicate", self.duplicate), ("Bring to front", lambda: self.reorder(1)),
                              ("Send to back", lambda: self.reorder(-1)), ("Delete", self.delete_selected)):
                mi = Gtk.MenuItem(label=label)
                mi.connect("activate", lambda _w, c=cb: c())
                menu.append(mi)
            if item["type"] in ann.BOX_KINDS:
                mi = Gtk.MenuItem(label="Edit text…")
                mi.connect("activate", lambda *_: self.edit_text(item))
                menu.append(mi)
        else:
            for label, cb in (("Copy image", self.copy), ("Export…", self.export),
                              ("Pin to screen", lambda: self.app.pin_surface(self.render_output()))):
                mi = Gtk.MenuItem(label=label)
                mi.connect("activate", lambda _w, c=cb: c())
                menu.append(mi)
        menu.show_all()
        menu.attach_to_widget(self.canvas, None)
        menu.popup_at_pointer(ev)
        self.canvas.queue_draw()

    # ================================================================ keyboard
    def _on_key(self, _w, ev):
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Entry, Gtk.SpinButton, Gtk.TextView)):
            return False
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        key = Gdk.keyval_to_lower(ev.keyval)
        if ctrl:
            actions = {
                Gdk.KEY_z: self.redo if shift else self.undo,
                Gdk.KEY_y: self.redo,
                Gdk.KEY_c: self.copy,
                Gdk.KEY_s: self.export,
                Gdk.KEY_d: self.duplicate,
                Gdk.KEY_0: self.zoom_fit,
                Gdk.KEY_1: lambda: self.set_zoom(1.0),
                Gdk.KEY_plus: lambda: self.set_zoom(self.zoom * 1.25),
                Gdk.KEY_equal: lambda: self.set_zoom(self.zoom * 1.25),
                Gdk.KEY_minus: lambda: self.set_zoom(self.zoom / 1.25),
                Gdk.KEY_bracketright: lambda: self.reorder(1),
                Gdk.KEY_bracketleft: lambda: self.reorder(-1),
                Gdk.KEY_w: self.close,
                Gdk.KEY_question: lambda: self.app.show_shortcuts(self),
                Gdk.KEY_slash: lambda: self.app.show_shortcuts(self),
            }
            if key in actions:
                actions[key]()
                return True
            return False
        if key in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            self.delete_selected()
            return True
        if key == Gdk.KEY_Escape:
            if self.tool == "crop":
                self._set_tool("select")
            elif self.active is not None:
                self.active = None
                self.drag = None
                self._render_items()
            else:
                self.selected = None
                self._sync_props_visibility()
                self.canvas.queue_draw()
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.tool == "crop":
            self.apply_crop()
            return True
        if self.selected is not None and key in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            step = 10 if shift else 1
            dx = {Gdk.KEY_Left: -step, Gdk.KEY_Right: step}.get(key, 0)
            dy = {Gdk.KEY_Up: -step, Gdk.KEY_Down: step}.get(key, 0)
            self._checkpoint()
            ann.move_item(self.selected, dx, dy)
            self._commit()
            return True
        for name, _tip, shortcut in TOOLS:
            if ev.string and ev.string.lower() == shortcut:
                self._set_tool(name)
                return True
        return False

    # ================================================================ commands
    def add_text(self, x, y):
        text = ui.prompt_text(self, "Add text", "Text:", "", multiline=True)
        if not text:
            return
        self._checkpoint()
        item = self._new_item("text", x, y)
        item["text"] = text
        render.measure_text(item)
        self.doc.items.append(item)
        self.selected = item
        self._commit()

    def _finish_callout(self, item):
        text = ui.prompt_text(self, "Add callout", "Callout text:", "", multiline=True)
        if not text:
            self._render_items()
            return
        item["text"] = text
        render.measure_text(item)
        item["x"] -= item["w"] / 2
        item["y"] -= item["h"] / 2
        self._checkpoint()
        self.doc.items.append(item)
        self.selected = item
        self._commit()

    def edit_text(self, item):
        text = ui.prompt_text(self, "Edit text", "Text:", item.get("text", ""), multiline=True)
        if text is None:
            return
        self._checkpoint()
        if not text:
            self.doc.items.remove(item)
            self.selected = None
        else:
            item["text"] = text
            render.measure_text(item)
        self._commit()

    def delete_selected(self):
        if self.selected is None or self.selected not in self.doc.items:
            return
        self._checkpoint()
        was_step = self.selected["type"] == "step"
        self.doc.items.remove(self.selected)
        self.selected = None
        if was_step:
            self.doc.renumber_steps()
        self._sync_props_visibility()
        self._commit()

    def duplicate(self):
        if self.selected is None:
            return
        self._checkpoint()
        item = copy.deepcopy(self.selected)
        ann.move_item(item, 16, 16)
        if item["type"] == "step":
            item["n"] = self.doc.next_step_number()
        self.doc.items.append(item)
        self.selected = item
        self._commit()

    def reorder(self, direction):
        if self.selected is None or self.selected not in self.doc.items:
            return
        self._checkpoint()
        self.doc.items.remove(self.selected)
        if direction > 0:
            self.doc.items.append(self.selected)
        else:
            self.doc.items.insert(0, self.selected)
        self.doc.renumber_steps()
        self._commit()

    def image_op(self, op):
        ow, oh = self.src.get_width(), self.src.get_height()
        self._checkpoint()
        sel_index = self.doc.items.index(self.selected) if self.selected in self.doc.items else None
        {"rotate_cw": self.doc.rotate_cw, "rotate_ccw": self.doc.rotate_ccw,
         "flip_h": self.doc.flip_horizontal, "flip_v": self.doc.flip_vertical}[op](ow, oh)
        self.selected = self.doc.items[sel_index] if sel_index is not None else None
        self.crop_draft = None
        if self.tool == "crop":
            self._set_tool("select")
        self.fit_mode = True
        self._rebuild_base()
        self._schedule_save()

    def resize_dialog(self):
        ow, oh = self.src.get_width(), self.src.get_height()
        tw, th = self.doc.transformed_size(ow, oh)
        dlg = Gtk.Dialog(title="Resize image", transient_for=self, modal=True)
        dlg.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("_Resize", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=12)
        pct = Gtk.SpinButton.new_with_range(1, 1600, 1)
        pct.set_value(100)
        wspin = Gtk.SpinButton.new_with_range(1, 40000, 1)
        hspin = Gtk.SpinButton.new_with_range(1, 40000, 1)
        wspin.set_value(tw)
        hspin.set_value(th)
        lock = {"busy": False}

        def from_pct(*_):
            if lock["busy"]:
                return
            lock["busy"] = True
            wspin.set_value(round(tw * pct.get_value() / 100))
            hspin.set_value(round(th * pct.get_value() / 100))
            lock["busy"] = False

        def from_w(*_):
            if lock["busy"]:
                return
            lock["busy"] = True
            pct.set_value(100 * wspin.get_value() / tw)
            hspin.set_value(round(th * wspin.get_value() / tw))
            lock["busy"] = False

        def from_h(*_):
            if lock["busy"]:
                return
            lock["busy"] = True
            pct.set_value(100 * hspin.get_value() / th)
            wspin.set_value(round(tw * hspin.get_value() / th))
            lock["busy"] = False

        pct.connect("value-changed", from_pct)
        wspin.connect("value-changed", from_w)
        hspin.connect("value-changed", from_h)
        for row, (label, widget) in enumerate((("Percent", pct), ("Width (px)", wspin), ("Height (px)", hspin))):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="Aspect ratio is kept. Annotations scale with the image.",
                              xalign=0, sensitive=False), 0, 3, 2, 1)
        dlg.get_content_area().add(grid)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            factor = pct.get_value() / 100
            if abs(factor - 1) > 1e-6:
                self._checkpoint()
                self.doc.set_scale(self.doc.scale * factor)
                self.fit_mode = True
                self._rebuild_base()
                self._schedule_save()
        dlg.destroy()

    def apply_crop(self):
        if not self.crop_draft or self.crop_draft[2] < 2 or self.crop_draft[3] < 2:
            return
        self._checkpoint()
        tw, th = self.base.get_width(), self.base.get_height()
        x, y, w, h = [round(v) for v in self.crop_draft]
        self.doc.crop = None if (x, y, w, h) == (0, 0, tw, th) else [x, y, w, h]
        self.crop_draft = None
        self._set_tool("select")
        self.fit_mode = True
        self._commit()
        GLib.idle_add(self.zoom_fit)

    def reset_crop(self):
        if self.doc.crop:
            self._checkpoint()
            self.doc.crop = None
            self._commit()
        tw, th = self.base.get_width(), self.base.get_height()
        self.crop_draft = [0, 0, tw, th]
        self.canvas.queue_draw()

    def revert(self):
        if self.doc.is_empty():
            return
        if not ui.ask(self, "Revert to original?", "All annotations and image changes are removed. You can undo this.",
                      "_Revert", destructive=True):
            return
        self._checkpoint()
        self.doc = Document()
        self.selected = None
        self.fit_mode = True
        self._rebuild_base()
        self._schedule_save()

    def copy(self):
        ui.copy_surface(self.render_output())
        self.status.set_text("Copied to clipboard")

    def export(self):
        self._flush_save()
        ui.export_dialog(self, self.library, self.config, [self.capture_id])

    def _flush_save(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_now()

    def _on_drag_data_get(self, _w, _ctx, selection, _info, _time):
        self._flush_save()
        path = ui.temp_render(self.library, self.capture_id, self.config)
        selection.set_uris([ui.file_uri(path)])

    def _on_delete(self, *_):
        self._flush_save()
        return False

    def _on_library_event(self, kind, ids):
        if self.capture_id in ids and kind in ("changed",):
            try:
                self.cap = self.library.get(self.capture_id)
                self._update_title()
            except KeyError:
                pass
        if self.capture_id in ids and kind in ("deleted",):
            self.destroy()
