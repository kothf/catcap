"""Settings window (SET-01..05)."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import hotkeys, ui  # noqa: E402

AFTER_ACTIONS = [("toolbar", "Show quick toolbar"), ("editor", "Open in editor"),
                 ("clipboard", "Copy to clipboard"), ("library", "Save to library only")]
FORMATS = [("png", "PNG"), ("jpg", "JPG"), ("webp", "WebP"), ("bmp", "BMP")]


class SettingsWindow(Gtk.Window):
    def __init__(self, app, parent=None):
        super().__init__(title="CatCap Settings", transient_for=parent)
        self.app = app
        self.config = app.config
        self.set_default_size(640, 560)
        self.set_icon_name("io.github.kothf.CatCap")
        header = Gtk.HeaderBar(show_close_button=True, title="Settings")
        self.set_titlebar(header)
        notebook = Gtk.Notebook()
        self.add(notebook)
        for title, builder in (("General", self._general), ("Capture", self._capture), ("Hotkeys", self._hotkeys),
                               ("Library", self._library), ("Output", self._output)):
            page = builder()
            page.set_border_width(16)
            notebook.append_page(page, Gtk.Label(label=title))
        self.show_all()

    # ------------------------------------------------------------ helpers
    def _grid(self):
        return Gtk.Grid(column_spacing=12, row_spacing=10)

    def _switch(self, grid, row, label, section, key, on_change=None, hint=None):
        lbl = Gtk.Label(label=label, xalign=0, hexpand=True)
        sw = Gtk.Switch(active=bool(self.config.get(section, key)), halign=Gtk.Align.END)

        def changed(w, _p):
            self.config.set(section, key, w.get_active())
            if on_change:
                on_change()

        sw.connect("notify::active", changed)
        grid.attach(lbl, 0, row, 1, 1)
        grid.attach(sw, 1, row, 1, 1)
        if hint:
            h = Gtk.Label(label=hint, xalign=0, wrap=True, max_width_chars=70)
            h.get_style_context().add_class("catcap-dim")
            grid.attach(h, 0, row + 1, 2, 1)
            return row + 2
        return row + 1

    def _combo(self, grid, row, label, section, key, options):
        combo = Gtk.ComboBoxText(halign=Gtk.Align.END)
        for k, text in options:
            combo.append(k, text)
        combo.set_active_id(str(self.config.get(section, key)))
        combo.connect("changed", lambda c: self.config.set(section, key, c.get_active_id()))
        grid.attach(Gtk.Label(label=label, xalign=0, hexpand=True), 0, row, 1, 1)
        grid.attach(combo, 1, row, 1, 1)
        return row + 1

    def _spin(self, grid, row, label, section, key, lo, hi):
        spin = Gtk.SpinButton.new_with_range(lo, hi, 1)
        spin.set_value(self.config.get(section, key))
        spin.set_halign(Gtk.Align.END)
        spin.connect("value-changed", lambda s: self.config.set(section, key, int(s.get_value())))
        grid.attach(Gtk.Label(label=label, xalign=0, hexpand=True), 0, row, 1, 1)
        grid.attach(spin, 1, row, 1, 1)
        return row + 1

    # ------------------------------------------------------------ pages
    def _general(self):
        g = self._grid()
        r = self._switch(g, 0, "Start CatCap at login (in the tray)", "general", "start_at_login",
                         self.app.update_autostart)
        r = self._switch(g, r, "Show a notification after each capture", "general", "notifications")
        r = self._switch(g, r, "Play a camera sound", "general", "sound")
        about = Gtk.Label(xalign=0, wrap=True, max_width_chars=70, margin_top=20)
        about.set_markup(f"Settings file: <tt>{ui.GLib.markup_escape_text(str(self.config.path))}</tt>")
        about.get_style_context().add_class("catcap-dim")
        g.attach(about, 0, r, 2, 1)
        return g

    def _capture(self):
        g = self._grid()
        r = self._combo(g, 0, "After a capture", "capture", "after_action", AFTER_ACTIONS)
        r = self._spin(g, r, "Default delay (seconds)", "capture", "delay", 0, 60)
        r = self._switch(g, r, "Include the mouse pointer", "capture", "include_cursor",
                         hint="X11 only. On Wayland the portal decides.")
        r = self._switch(g, r, "Include window title bar and borders", "capture", "window_decorations")
        r = self._switch(g, r, "Show magnifier while selecting", "capture", "magnifier")
        r = self._spin(g, r, "Magnifier zoom", "capture", "magnifier_zoom", 2, 20)
        r = self._switch(g, r, "Highlight and snap to windows while selecting", "capture", "snap_to_windows")
        backend = self.app.backend.name
        note = Gtk.Label(xalign=0, wrap=True, max_width_chars=70, margin_top=16)
        note.get_style_context().add_class("catcap-dim")
        if backend == "wayland":
            note.set_text("Wayland session: captures go through the desktop's screenshot portal. Window and "
                          "active-window captures use the portal's picker, region selection works on the "
                          "primary monitor, and the source application is not recorded.")
        else:
            note.set_text("X11 session: all capture modes are available.")
        g.attach(note, 0, r, 2, 1)
        return g

    def _hotkeys(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        mgr = self.app.hotkeys
        top = self._grid()
        r = self._switch(top, 0, "Enable global hotkeys", "hotkeys", "enabled", self._apply_hotkeys)
        if hotkeys.is_cinnamon():
            r = self._switch(top, r, "Use Print Screen for CatCap", "hotkeys", "take_over_print", self._apply_hotkeys,
                             hint="Frees Print Screen from Cinnamon's built-in screenshot tool. "
                                  "Turning this off restores Cinnamon's keys.")
        box.add(top)
        desc = Gtk.Label(label=mgr.backend_description(), xalign=0, wrap=True, max_width_chars=70)
        desc.get_style_context().add_class("catcap-dim")
        box.add(desc)

        self.hk_store = Gtk.ListStore(str, str, str, str)  # action, label, accel, command
        for action, label, flag in hotkeys.ACTIONS:
            self.hk_store.append([action, label, self.config.get("hotkeys", action), mgr.command(flag)])
        tree = Gtk.TreeView(model=self.hk_store)
        tree.append_column(Gtk.TreeViewColumn("Action", Gtk.CellRendererText(), text=1))
        accel = Gtk.CellRendererAccel(editable=True, accel_mode=Gtk.CellRendererAccelMode.OTHER)
        accel.connect("accel-edited", self._accel_edited)
        accel.connect("accel-cleared", self._accel_cleared)
        col = Gtk.TreeViewColumn("Shortcut", accel)
        col.set_cell_data_func(accel, self._accel_data)
        tree.append_column(col)
        cmd = Gtk.CellRendererText()
        cmd.set_property("foreground", "gray")
        tree.append_column(Gtk.TreeViewColumn("Command", cmd, text=3))
        frame = Gtk.Frame()
        frame.add(tree)
        box.add(frame)
        hint = Gtk.Label(label="Click a shortcut to change it; Backspace clears it.", xalign=0)
        hint.get_style_context().add_class("catcap-dim")
        box.add(hint)
        self.hk_warnings = Gtk.Label(xalign=0, wrap=True, max_width_chars=70)
        box.add(self.hk_warnings)
        return box

    def _accel_data(self, _col, cell, model, it, _data):
        accel = model[it][2]
        key, mods = Gtk.accelerator_parse(accel) if accel else (0, 0)
        cell.set_property("accel-key", key)
        cell.set_property("accel-mods", mods)

    def _accel_edited(self, _cell, path, key, mods, _hw):
        accel = Gtk.accelerator_name(key, mods)
        self.hk_store[path][2] = accel
        self.config.set("hotkeys", self.hk_store[path][0], accel)
        self._apply_hotkeys()

    def _accel_cleared(self, _cell, path):
        self.hk_store[path][2] = ""
        self.config.set("hotkeys", self.hk_store[path][0], "")
        self._apply_hotkeys()

    def _apply_hotkeys(self):
        warnings = self.app.hotkeys.apply()
        if warnings:
            self.hk_warnings.set_markup("<b>Check these:</b>\n" + "\n".join(
                ui.GLib.markup_escape_text(w) for w in warnings))
        elif self.config.get("hotkeys", "enabled"):
            self.hk_warnings.set_text("Hotkeys are active.")
        else:
            self.hk_warnings.set_text("")

    def _library(self):
        g = self._grid()
        g.attach(Gtk.Label(label="Image folder", xalign=0, hexpand=True), 0, 0, 1, 1)
        chooser = Gtk.FileChooserButton(title="Choose the image folder", action=Gtk.FileChooserAction.SELECT_FOLDER)
        chooser.set_filename(str(self.config.images_dir()))
        chooser.connect("file-set", self._move_library)
        g.attach(chooser, 1, 0, 1, 1)
        r = self._spin(g, 1, "Delete items in Trash after (days, 0 = never)", "library", "trash_days", 0, 3650)
        r = self._spin(g, r, "Thumbnail size (px, applies on reopen)", "library", "thumb_size", 96, 320)
        note = Gtk.Label(xalign=0, wrap=True, max_width_chars=70, margin_top=16)
        note.get_style_context().add_class("catcap-dim")
        note.set_text("Originals are stored as PNG with a .json file holding the annotations next to each one. "
                      "Edited images are rendered on demand and are not stored.")
        g.attach(note, 0, r, 2, 1)
        return g

    def _move_library(self, chooser):
        new_dir = Path(chooser.get_filename())
        if new_dir == self.app.library.images_dir:
            return
        if not ui.ask(self, "Move library?", f"Move all captures to {new_dir}?", "_Move"):
            chooser.set_filename(str(self.app.library.images_dir))
            return
        try:
            self.app.library.move_images(new_dir)
            self.config.set("library", "images_dir", str(new_dir))
        except Exception as exc:
            ui.error_dialog(self, "Could not move the library", str(exc))

    def _output(self):
        g = self._grid()
        g.attach(Gtk.Label(label="File name template", xalign=0, hexpand=True), 0, 0, 1, 1)
        entry = Gtk.Entry(text=self.config.get("output", "filename_template"), width_chars=30)
        entry.connect("changed", lambda e: self.config.set("output", "filename_template", e.get_text()))
        g.attach(entry, 1, 0, 1, 1)
        hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=70)
        hint.get_style_context().add_class("catcap-dim")
        hint.set_text("Tokens: {app} {title} {name} {mode} {width} {height} {id}, and date patterns such as "
                      "{yyyy-MM-dd} or {HHmmss}.")
        g.attach(hint, 0, 1, 2, 1)
        r = self._combo(g, 2, "Default export format", "output", "default_format", FORMATS)
        r = self._spin(g, r, "JPG / WebP quality", "output", "jpg_quality", 10, 100)
        return g
