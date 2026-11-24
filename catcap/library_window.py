"""Library window (BRW-*, ORG-*, SRC-*, IMP-*)."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from . import render, ui  # noqa: E402
from .library import Query  # noqa: E402

COL_PIXBUF, COL_MARKUP, COL_ID = range(3)
L_ID, L_NAME, L_DATE, L_DIMS, L_SIZE, L_APP, L_MODE, L_GROUP = range(8)

SORT_OPTIONS = [("date_desc", "Newest first"), ("date_asc", "Oldest first"), ("name", "Name"),
                ("size", "File size"), ("dims", "Dimensions"), ("app", "Application")]
DATE_FILTERS = [("any", "Any time"), ("today", "Today"), ("7d", "Last 7 days"),
                ("30d", "Last 30 days"), ("year", "This year")]
MODE_LABELS = {"region": "Region", "window": "Window", "full": "Full screen", "monitor": "Monitor",
               "active": "Active window", "last_region": "Repeat region", "import": "Imported",
               "clipboard": "Pasted", "recovered": "Recovered", "portal": "Portal"}


def _esc(text: str) -> str:
    return GLib.markup_escape_text(text or "")


def date_group(ts: float) -> str:
    now = time.localtime()
    t = time.localtime(ts)
    today_start = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    if ts >= today_start:
        return "Today"
    if ts >= today_start - 86400:
        return "Yesterday"
    if ts >= today_start - 6 * 86400:
        return "This week"
    return time.strftime("%B %Y", t)


class LibraryWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="CatCap Library")
        self.app = app
        self.library = app.library
        self.config = app.config
        self.query = Query(sort=self.config.get("library", "sort"))
        self.captures = []
        self.by_id = {}
        self.thumb_cache: dict[str, GdkPixbuf.Pixbuf] = {}
        self.thumb_size = int(self.config.get("library", "thumb_size"))
        self._thumb_job = 0
        self._refresh_source = 0
        self._preview_id = None
        self._loading_preview = False
        self.placeholder = self._make_placeholder()
        self.set_icon_name("io.github.kothf.CatCap")
        self.set_default_size(1180, 760)
        self._build()
        self.library.connect(self._on_library_event)
        self.connect("destroy", lambda *_: self.library.disconnect(self._on_library_event))
        self.connect("key-press-event", self._on_key)
        self.refresh_sidebar()
        self.refresh()

    # ================================================================ build
    def _build(self):
        header = Gtk.HeaderBar(show_close_button=True, title="CatCap Library")
        self.set_titlebar(header)

        capture_btn = Gtk.MenuButton(label="New capture", tooltip_text="Take a new screenshot (Ctrl+N: region)")
        capture_btn.get_style_context().add_class("suggested-action")
        capture_btn.set_popup(self._capture_menu())
        header.pack_start(capture_btn)
        import_btn = Gtk.Button(image=Gtk.Image.new_from_icon_name("document-open-symbolic", Gtk.IconSize.BUTTON),
                                tooltip_text="Import images (or drop files here, or paste with Ctrl+V)")
        import_btn.connect("clicked", lambda *_: self.import_dialog())
        header.pack_start(import_btn)

        self.search = Gtk.SearchEntry(placeholder_text="Search name, tags, app, window title, notes",
                                      width_chars=40)
        self.search.connect("search-changed", self._on_search)
        header.set_custom_title(self.search)

        menu_btn = Gtk.MenuButton(image=Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        menu_btn.set_popup(self._main_menu())
        header.pack_end(menu_btn)

        view_box = Gtk.Box()
        view_box.get_style_context().add_class("linked")
        self.grid_toggle = Gtk.RadioButton(tooltip_text="Grid view")
        self.grid_toggle.set_mode(False)
        self.grid_toggle.set_image(Gtk.Image.new_from_icon_name("view-grid-symbolic", Gtk.IconSize.BUTTON))
        self.list_toggle = Gtk.RadioButton.new_from_widget(self.grid_toggle)
        self.list_toggle.set_tooltip_text("List view")
        self.list_toggle.set_mode(False)
        self.list_toggle.set_image(Gtk.Image.new_from_icon_name("view-list-symbolic", Gtk.IconSize.BUTTON))
        view_box.add(self.grid_toggle)
        view_box.add(self.list_toggle)
        header.pack_end(view_box)

        self.sort_combo = Gtk.ComboBoxText(tooltip_text="Sort")
        for key, label in SORT_OPTIONS:
            self.sort_combo.append(key, label)
        self.sort_combo.set_active_id(self.query.sort if self.query.sort in dict(SORT_OPTIONS) else "date_desc")
        self.sort_combo.connect("changed", self._on_sort)
        header.pack_end(self.sort_combo)

        self.filter_btn = Gtk.MenuButton(image=Gtk.Image.new_from_icon_name("view-more-horizontal-symbolic",
                                                                             Gtk.IconSize.BUTTON),
                                         tooltip_text="Filters")
        self.filter_btn.set_image(Gtk.Image.new_from_icon_name("edit-find-replace-symbolic", Gtk.IconSize.BUTTON))
        self.filter_btn.set_popover(self._filter_popover())
        header.pack_end(self.filter_btn)

        outer = Gtk.Paned()
        self.add(outer)
        outer.pack1(self._build_sidebar(), False, False)
        inner = Gtk.Paned()
        outer.pack2(inner, True, False)
        outer.set_position(210)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.info_bar = Gtk.Label(xalign=0, margin=6, margin_start=12)
        self.info_bar.get_style_context().add_class("catcap-dim")
        center.add(self.info_bar)
        self.stack = Gtk.Stack()
        self.stack.add_named(self._build_grid(), "grid")
        self.stack.add_named(self._build_list(), "list")
        self.stack.add_named(self._build_empty(), "empty")
        center.pack_start(self.stack, True, True, 0)
        inner.pack1(center, True, False)
        inner.pack2(self._build_preview(), False, False)
        inner.set_position(self.get_default_size()[0] - 210 - 340)

        if self.config.get("library", "view") == "list":
            self.list_toggle.set_active(True)
        self.grid_toggle.connect("toggled", self._on_view_toggle)

        self.drag_dest_set(Gtk.DestDefaults.ALL, [ui.URI_TARGET], Gdk.DragAction.COPY)
        self.connect("drag-data-received", self._on_external_drop)
        self.show_all()
        self._update_preview()

    def _capture_menu(self):
        menu = Gtk.Menu()
        for mode, label in (("region", "Region or window…"), ("window", "Window…"), ("full", "Full screen"),
                            ("monitor", "Current monitor"), ("active", "Active window"),
                            ("last_region", "Repeat last region")):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _w, m=mode: self.app.start_capture(m, hide=self))
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        delayed = Gtk.MenuItem(label="Region after 5 seconds")
        delayed.connect("activate", lambda *_: self.app.start_capture("region", delay=5, hide=self))
        menu.append(delayed)
        paste = Gtk.MenuItem(label="New from clipboard")
        paste.connect("activate", lambda *_: self.paste())
        menu.append(paste)
        menu.show_all()
        return menu

    def _main_menu(self):
        menu = Gtk.Menu()
        for label, cb in (("Settings", lambda: self.app.show_settings(self)),
                          ("Keyboard shortcuts", lambda: self.app.show_shortcuts(self)),
                          (None, None),
                          ("Empty trash…", self.empty_trash),
                          ("Rebuild library from disk", self.rebuild),
                          ("Open image folder", lambda: ui.show_in_folder(self.library.images_dir / ".")),
                          (None, None),
                          ("About CatCap", lambda: self.app.show_about(self)),
                          ("Quit CatCap", lambda: self.app.quit())):
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _w, c=cb: c())
            menu.append(item)
        menu.show_all()
        return menu

    def _filter_popover(self):
        pop = Gtk.Popover()
        grid = Gtk.Grid(column_spacing=10, row_spacing=8, margin=12)
        self.date_combo = Gtk.ComboBoxText()
        for key, label in DATE_FILTERS:
            self.date_combo.append(key, label)
        self.date_combo.set_active_id("any")
        self.app_combo = Gtk.ComboBoxText()
        self.mode_combo = Gtk.ComboBoxText()
        self.annotated_check = Gtk.CheckButton(label="Only captures with annotations")
        reset = Gtk.Button(label="Reset filters")
        grid.attach(Gtk.Label(label="Date", xalign=0), 0, 0, 1, 1)
        grid.attach(self.date_combo, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Application", xalign=0), 0, 1, 1, 1)
        grid.attach(self.app_combo, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Capture type", xalign=0), 0, 2, 1, 1)
        grid.attach(self.mode_combo, 1, 2, 1, 1)
        grid.attach(self.annotated_check, 0, 3, 2, 1)
        grid.attach(reset, 0, 4, 2, 1)
        grid.show_all()
        pop.add(grid)
        pop.connect("show", lambda *_: self._fill_filter_combos())
        for w in (self.date_combo, self.app_combo, self.mode_combo):
            w.connect("changed", self._on_filters)
        self.annotated_check.connect("toggled", self._on_filters)
        reset.connect("clicked", self._reset_filters)
        return pop

    def _fill_filter_combos(self):
        self._filling = True
        cur_app = self.query.app or ""
        self.app_combo.remove_all()
        self.app_combo.append("", "Any application")
        for app in self.library.apps():
            self.app_combo.append(app, app)
        self.app_combo.set_active_id(cur_app)
        cur_mode = self.query.mode or ""
        self.mode_combo.remove_all()
        self.mode_combo.append("", "Any type")
        for mode in self.library.modes():
            self.mode_combo.append(mode, MODE_LABELS.get(mode, mode))
        self.mode_combo.set_active_id(cur_mode)
        self._filling = False

    def _build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar = Gtk.ListBox()
        self.sidebar.get_style_context().add_class("catcap-sidebar")
        self.sidebar.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.sidebar.connect("row-selected", self._on_sidebar_selected)
        self.sidebar.connect("button-press-event", self._on_sidebar_button)
        self.sidebar.drag_dest_set(Gtk.DestDefaults.ALL, [ui.IDS_TARGET], Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        self.sidebar.connect("drag-data-received", self._on_sidebar_drop)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.add(self.sidebar)
        box.pack_start(sw, True, True, 0)
        return box

    def _sidebar_row(self, key, label, count=None, icon=None):
        row = Gtk.ListBoxRow()
        row.key = key
        hbox = Gtk.Box(spacing=8)
        if icon:
            hbox.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
        lbl = Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        hbox.pack_start(lbl, True, True, 0)
        if count is not None:
            c = Gtk.Label(label=str(count))
            c.get_style_context().add_class("catcap-dim")
            hbox.pack_end(c, False, False, 0)
        row.add(hbox)
        return row

    def _sidebar_header(self, text, button_cb=None):
        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.key = None
        hbox = Gtk.Box()
        lbl = Gtk.Label(label=text.upper(), xalign=0)
        lbl.get_style_context().add_class("catcap-sidebar-header")
        hbox.pack_start(lbl, True, True, 0)
        if button_cb:
            btn = Gtk.Button(image=Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.MENU),
                             relief=Gtk.ReliefStyle.NONE, tooltip_text="New collection")
            btn.connect("clicked", lambda *_: button_cb())
            hbox.pack_end(btn, False, False, 0)
        row.add(hbox)
        return row

    def refresh_sidebar(self):
        current = getattr(self.sidebar.get_selected_row(), "key", ("all",))
        for child in self.sidebar.get_children():
            self.sidebar.remove(child)
        total = self.library.count()
        self.sidebar.add(self._sidebar_row(("all",), "All captures", total, "folder-pictures-symbolic"))
        self.sidebar.add(self._sidebar_row(("favourites",), "Favourites", None, "starred-symbolic"))
        self.sidebar.add(self._sidebar_row(("recent",), "Recent (7 days)", None, "document-open-recent-symbolic"))
        self.sidebar.add(self._sidebar_row(("trash",), "Trash", None, "user-trash-symbolic"))
        self.sidebar.add(self._sidebar_header("Collections", self.new_collection))
        for cid, name, count in self.library.collections():
            self.sidebar.add(self._sidebar_row(("collection", cid), name, count, "folder-symbolic"))
        tags = self.library.all_tags()
        if tags:
            self.sidebar.add(self._sidebar_header("Tags"))
            for tag, count in tags:
                self.sidebar.add(self._sidebar_row(("tag", tag), tag, count, "tag-symbolic"))
        self.sidebar.show_all()
        self._selecting_sidebar = True
        for row in self.sidebar.get_children():
            if getattr(row, "key", None) == current:
                self.sidebar.select_row(row)
                break
        else:
            self.sidebar.select_row(self.sidebar.get_row_at_index(0))
        self._selecting_sidebar = False

    def _build_grid(self):
        self.grid_store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str)
        self.icon_view = Gtk.IconView(model=self.grid_store)
        self.icon_view.set_pixbuf_column(COL_PIXBUF)
        self.icon_view.set_markup_column(COL_MARKUP)
        self.icon_view.set_item_width(self.thumb_size + 16)
        self.icon_view.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.icon_view.set_margin(8)
        self.icon_view.set_column_spacing(6)
        self.icon_view.set_row_spacing(6)
        self.icon_view.connect("selection-changed", lambda *_: self._update_preview())
        self.icon_view.set_item_padding(4)
        self.icon_view.connect("size-allocate", self._on_grid_allocate)
        self.icon_view.connect("item-activated", lambda _v, path: self.open_editor(self.grid_store[path][COL_ID]))
        self.icon_view.connect("button-press-event", self._on_grid_button)
        self.icon_view.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, [ui.URI_TARGET, ui.IDS_TARGET],
                                                Gdk.DragAction.COPY)
        self.icon_view.connect("drag-data-get", self._on_drag_data_get)
        sw = Gtk.ScrolledWindow()
        sw.add(self.icon_view)
        sw.get_vadjustment().connect("value-changed", lambda *_: self._queue_thumbs())
        self.grid_scroller = sw
        return sw

    def _on_grid_allocate(self, view, alloc):
        cell = self.thumb_size + 16 + 2 * view.get_item_padding() + view.get_column_spacing()
        cols = max(1, (alloc.width - 2 * view.get_margin()) // cell)
        if view.get_columns() != cols:
            GLib.idle_add(view.set_columns, cols)

    def _build_list(self):
        self.list_store = Gtk.TreeStore(str, str, str, str, str, str, str, bool)
        self.tree = Gtk.TreeView(model=self.list_store)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.tree.get_selection().set_select_function(lambda _s, model, path, _c: not model[path][L_GROUP])
        self.tree.get_selection().connect("changed", lambda *_: self._update_preview())
        self.tree.connect("row-activated", self._on_tree_activated)
        self.tree.connect("button-press-event", self._on_tree_button)
        self.tree.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, [ui.URI_TARGET, ui.IDS_TARGET],
                                           Gdk.DragAction.COPY)
        self.tree.connect("drag-data-get", self._on_drag_data_get)
        for title, col, expand in (("Name", L_NAME, True), ("Date", L_DATE, False), ("Dimensions", L_DIMS, False),
                                   ("Size", L_SIZE, False), ("Application", L_APP, False), ("Type", L_MODE, False)):
            cell = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END if expand else Pango.EllipsizeMode.NONE)
            column = Gtk.TreeViewColumn(title, cell, text=col)
            column.add_attribute(cell, "weight-set", L_GROUP)
            cell.set_property("weight", Pango.Weight.BOLD)
            column.set_expand(expand)
            column.set_resizable(True)
            self.tree.append_column(column)
        sw = Gtk.ScrolledWindow()
        sw.add(self.tree)
        return sw

    def _build_empty(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER)
        image = Gtk.Image.new_from_icon_name("io.github.kothf.CatCap", Gtk.IconSize.DIALOG)
        image.set_pixel_size(96)
        self.empty_title = Gtk.Label()
        self.empty_title.get_style_context().add_class("catcap-preview-title")
        self.empty_text = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=50)
        self.empty_text.get_style_context().add_class("catcap-dim")
        self.empty_button = Gtk.Button(label="Take a screenshot", halign=Gtk.Align.CENTER)
        self.empty_button.get_style_context().add_class("suggested-action")
        self.empty_button.connect("clicked", lambda *_: self.app.start_capture("region", hide=self))
        for w in (image, self.empty_title, self.empty_text, self.empty_button):
            box.add(w)
        return box

    def _build_preview(self):
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_size_request(330, -1)
        self.preview_stack = Gtk.Stack()
        sw.add(self.preview_stack)

        # --- single capture
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
        self.preview_image = Gtk.Image()
        frame = Gtk.EventBox(tooltip_text="Double-click to edit")
        frame.add(self.preview_image)
        frame.connect("button-press-event", self._on_preview_click)
        box.add(frame)
        name_box = Gtk.Box(spacing=6)
        self.name_entry = Gtk.Entry(tooltip_text="Name (F2)")
        self.name_entry.connect("activate", lambda *_: self._save_name())
        self.name_entry.connect("focus-out-event", lambda *_: (self._save_name(), False)[1])
        self.fav_btn = Gtk.ToggleButton(tooltip_text="Favourite")
        self.fav_btn.connect("toggled", self._on_fav_toggled)
        name_box.pack_start(self.name_entry, True, True, 0)
        name_box.add(self.fav_btn)
        box.add(name_box)

        actions = Gtk.Box(spacing=6)
        edit = Gtk.Button(label="Edit")
        edit.get_style_context().add_class("suggested-action")
        edit.connect("clicked", lambda *_: self._for_selected(lambda ids: self.open_editor(ids[0])))
        copy_btn = Gtk.Button(label="Copy")
        copy_btn.connect("clicked", lambda *_: self.copy_selected())
        export = Gtk.Button(label="Export…")
        export.connect("clicked", lambda *_: self.export_selected())
        more = Gtk.MenuButton(image=Gtk.Image.new_from_icon_name("view-more-symbolic", Gtk.IconSize.BUTTON))
        more.set_popup(self._single_menu())
        for w in (edit, copy_btn, export):
            actions.pack_start(w, True, True, 0)
        actions.add(more)
        box.add(actions)

        self.details = Gtk.Grid(column_spacing=10, row_spacing=4)
        box.add(self.details)

        box.add(Gtk.Label(label="Tags (comma separated)", xalign=0))
        self.tags_entry = Gtk.Entry(placeholder_text="e.g. docs, bug, invoice")
        completion = Gtk.EntryCompletion()
        self.tag_model = Gtk.ListStore(str)
        completion.set_model(self.tag_model)
        completion.set_text_column(0)
        completion.set_match_func(self._tag_match, None)
        completion.connect("match-selected", self._tag_selected)
        self.tags_entry.set_completion(completion)
        self.tags_entry.connect("activate", lambda *_: self._save_tags())
        self.tags_entry.connect("focus-out-event", lambda *_: (self._save_tags(), False)[1])
        box.add(self.tags_entry)

        self.collections_btn = Gtk.MenuButton(label="Collections")
        self.collections_btn.set_popover(Gtk.Popover())
        self.collections_btn.get_popover().connect("show", lambda p: self._fill_collections_popover(p))
        box.add(self.collections_btn)

        box.add(Gtk.Label(label="Notes", xalign=0))
        self.notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.notes.set_size_request(-1, 80)
        self.notes.connect("focus-out-event", lambda *_: (self._save_notes(), False)[1])
        notes_frame = Gtk.Frame()
        notes_frame.add(self.notes)
        box.add(notes_frame)

        self.trash_actions = Gtk.Box(spacing=6)
        restore = Gtk.Button(label="Restore")
        restore.connect("clicked", lambda *_: self._for_selected(self.library.restore))
        delete = Gtk.Button(label="Delete permanently")
        delete.get_style_context().add_class("destructive-action")
        delete.connect("clicked", lambda *_: self.delete_selected(permanent=True))
        self.trash_actions.pack_start(restore, True, True, 0)
        self.trash_actions.pack_start(delete, True, True, 0)
        box.add(self.trash_actions)
        self.preview_stack.add_named(box, "single")
        self.single_actions = actions

        # --- multiple captures
        multi = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.multi_label = Gtk.Label(xalign=0)
        self.multi_label.get_style_context().add_class("catcap-preview-title")
        multi.add(self.multi_label)
        self.multi_normal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for label, cb in (("Export to folder…", self.export_selected),
                          ("Export as one PDF…", lambda: self._for_selected(
                              lambda ids: ui.export_pdf_dialog(self, self.library, self.config, ids))),
                          ("Add tag…", self.tag_selected),
                          ("Mark as favourite", lambda: self._for_selected(lambda ids: self.library.set_favourite(ids, True))),
                          ("Remove favourite", lambda: self._for_selected(lambda ids: self.library.set_favourite(ids, False))),
                          ("Move to trash", self.delete_selected)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, c=cb: c())
            self.multi_normal.add(b)
        add_coll = Gtk.MenuButton(label="Add to collection")
        add_coll.set_popover(Gtk.Popover())
        add_coll.get_popover().connect("show", lambda p: self._fill_collections_popover(p, multi=True))
        self.multi_normal.add(add_coll)
        multi.add(self.multi_normal)
        self.multi_trash = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for label, cb in (("Restore", lambda: self._for_selected(self.library.restore)),
                          ("Delete permanently", lambda: self.delete_selected(permanent=True))):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, c=cb: c())
            self.multi_trash.add(b)
        multi.add(self.multi_trash)
        self.preview_stack.add_named(multi, "multi")

        # --- nothing selected
        none = Gtk.Label(label="Select a capture to see details.\n\nTip: drag captures onto a collection in the sidebar.",
                         justify=Gtk.Justification.CENTER, wrap=True, margin=20)
        none.get_style_context().add_class("catcap-dim")
        self.preview_stack.add_named(none, "none")
        return sw

    def _single_menu(self):
        menu = Gtk.Menu()
        for label, cb in (("Pin to screen", lambda ids: self.app.pin_surface(render.render_capture(self.library, ids[0]))),
                          ("Export as PDF…", lambda ids: ui.export_pdf_dialog(self, self.library, self.config, ids)),
                          ("Open with…", lambda ids: ui.open_with(self, ui.temp_render(self.library, ids[0], self.config))),
                          ("Send by email…", lambda ids: ui.send_email(ui.temp_render(self.library, ids[0], self.config))),
                          ("Show original in file manager", lambda ids: ui.show_in_folder(self.library.original_path(ids[0]))),
                          ("Move to trash", lambda ids: self.delete_selected())):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _w, c=cb: self._for_selected(c))
            menu.append(item)
        menu.show_all()
        return menu

    # ================================================================ data
    def _make_placeholder(self):
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, self.thumb_size, int(self.thumb_size * 0.62))
        pb.fill(0x8080803c)
        return pb

    def _on_library_event(self, kind, ids):
        for cid in ids:
            if kind in ("edited", "deleted"):
                self.thumb_cache.pop(cid, None)
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
        self._refresh_source = GLib.timeout_add(120, self._deferred_refresh, kind)

    def _deferred_refresh(self, kind):
        self._refresh_source = 0
        self.refresh_sidebar()
        self.refresh()
        return False

    def refresh(self):
        selected = set(self.selected_ids())
        self.captures = self.library.query(self.query)
        self.by_id = {c.id: c for c in self.captures}
        self.grid_store.clear()
        self.list_store.clear()
        for cap in self.captures:
            markup = f"<small>{_esc(cap.name)}</small>"
            if cap.favourite:
                markup = "★ " + markup
            self.grid_store.append([self.thumb_cache.get(cap.id, self.placeholder), markup, cap.id])
        group_iter, group_name = None, None
        grouped = self.query.sort in ("date_desc", "date_asc")
        for cap in self.captures:
            parent = None
            if grouped:
                g = date_group(cap.created)
                if g != group_name:
                    group_name = g
                    group_iter = self.list_store.append(None, ["", g, "", "", "", "", "", True])
                parent = group_iter
            self.list_store.append(parent, [cap.id, ("★ " if cap.favourite else "") + cap.name,
                                            ui.human_date(cap.created), f"{cap.width} × {cap.height}",
                                            ui.human_size(cap.size), cap.app,
                                            MODE_LABELS.get(cap.mode, cap.mode), False])
        self.tree.expand_all()
        self._restore_selection(selected)
        self._update_info()
        self._show_view()
        self._queue_thumbs()
        self._update_preview()

    def _restore_selection(self, ids):
        if not ids:
            return
        for row in self.grid_store:
            if row[COL_ID] in ids:
                self.icon_view.select_path(row.path)
        sel = self.tree.get_selection()

        def visit(model, path, it):
            if model[it][L_ID] in ids:
                sel.select_iter(it)
            return False

        self.list_store.foreach(visit)

    def _update_info(self):
        n = len(self.captures)
        view = {"all": "All captures", "favourites": "Favourites", "recent": "Recent", "trash": "Trash",
                "collection": "Collection", "tag": f"Tag: {self.query.tag}"}.get(self.query.view, "")
        filters = self._active_filters()
        text = f"{view} · {n} capture{'s' if n != 1 else ''}"
        if filters:
            text += " · filtered by " + ", ".join(filters)
        if self.query.view == "trash" and n:
            text += f" · items older than {self.config.get('library', 'trash_days')} days are removed automatically"
        self.info_bar.set_text(text)

    def _active_filters(self):
        out = []
        if self.query.search:
            out.append(f"“{self.query.search}”")
        if self.query.date_from:
            out.append(dict(DATE_FILTERS).get(self.date_combo.get_active_id(), "date"))
        if self.query.app:
            out.append(self.query.app)
        if self.query.mode:
            out.append(MODE_LABELS.get(self.query.mode, self.query.mode))
        if self.query.annotated_only:
            out.append("annotated")
        return out

    def _show_view(self):
        if not self.captures:
            if self.query.view == "trash":
                title, text, button = "Trash is empty", "Deleted captures stay here until the trash is emptied.", False
            elif self._active_filters():
                title, text, button = "No matching captures", "Try another search or reset the filters.", False
            elif self.library.count() == 0:
                title = "No captures yet"
                text = ("Press Print Screen (after enabling hotkeys in Settings), use “New capture”, "
                        "or drop image files here.")
                button = True
            else:
                title, text, button = "Nothing here", "Drag captures onto this collection to add them.", False
            self.empty_title.set_text(title)
            self.empty_text.set_text(text)
            self.empty_button.set_visible(button)
            self.stack.set_visible_child_name("empty")
        else:
            self.stack.set_visible_child_name("list" if self.list_toggle.get_active() else "grid")

    # ---------------------------------------------------------------- thumbnails
    def _queue_thumbs(self):
        if self._thumb_job:
            return
        self._thumb_job = GLib.idle_add(self._load_thumbs, priority=GLib.PRIORITY_LOW)

    def _visible_first(self):
        rows = list(range(len(self.grid_store)))
        rng = self.icon_view.get_visible_range()
        paths = [p for p in (rng or ()) if isinstance(p, Gtk.TreePath)]
        if len(paths) == 2:
            start, end = paths[0].get_indices()[0], paths[1].get_indices()[0]
            rows = list(range(start, end + 1)) + rows[:start] + rows[end + 1:]
        return rows

    def _load_thumbs(self):
        deadline = time.monotonic() + 0.02
        for index in self._visible_first():
            if index >= len(self.grid_store):
                continue
            row = self.grid_store[index]
            cid = row[COL_ID]
            if cid in self.thumb_cache:
                if row[COL_PIXBUF] is not self.thumb_cache[cid]:
                    row[COL_PIXBUF] = self.thumb_cache[cid]
                continue
            try:
                pb = render.render_thumbnail(self.library, cid, self.thumb_size)
            except Exception as exc:
                print(f"catcap: thumbnail failed for {cid}: {exc}")
                pb = self.placeholder
            self.thumb_cache[cid] = pb
            row[COL_PIXBUF] = pb
            if time.monotonic() > deadline:
                return True
        self._thumb_job = 0
        return False

    # ================================================================ selection helpers
    def selected_ids(self) -> list[str]:
        if self.list_toggle.get_active():
            model, paths = self.tree.get_selection().get_selected_rows()
            return [model[p][L_ID] for p in paths if model[p][L_ID]]
        paths = sorted(self.icon_view.get_selected_items(), key=lambda p: p.get_indices()[0])
        return [self.grid_store[p][COL_ID] for p in paths]

    def _for_selected(self, fn):
        ids = self.selected_ids()
        if ids:
            fn(ids)

    def select_capture(self, capture_id):
        for row in self.grid_store:
            if row[COL_ID] == capture_id:
                self.icon_view.unselect_all()
                self.icon_view.select_path(row.path)
                self.icon_view.scroll_to_path(row.path, True, 0.5, 0.5)
                return

    # ================================================================ preview pane
    def _update_preview(self):
        ids = self.selected_ids()
        in_trash = self.query.view == "trash"
        if not ids:
            self.preview_stack.set_visible_child_name("none")
            self._preview_id = None
            return
        if len(ids) > 1:
            self.multi_label.set_text(f"{len(ids)} captures selected")
            self.multi_normal.set_visible(not in_trash)
            self.multi_trash.set_visible(in_trash)
            self.preview_stack.set_visible_child_name("multi")
            self._preview_id = None
            return
        cid = ids[0]
        cap = self.by_id.get(cid)
        if cap is None:
            return
        self.preview_stack.set_visible_child_name("single")
        self._loading_preview = True
        if self._preview_id != cid:
            try:
                surface = render.render_capture(self.library, cid)
                pb = render.scale_to_fit(render.surface_to_pixbuf(surface), 306, 230)
            except Exception:
                pb = self.placeholder
            self.preview_image.set_from_pixbuf(pb)
        self._preview_id = cid
        self.name_entry.set_text(cap.name)
        self.fav_btn.set_active(cap.favourite)
        self.fav_btn.set_image(Gtk.Image.new_from_icon_name(
            "starred-symbolic" if cap.favourite else "non-starred-symbolic", Gtk.IconSize.BUTTON))
        for child in self.details.get_children():
            self.details.remove(child)
        rows = [("Created", ui.human_date(cap.created)), ("Size", f"{cap.width} × {cap.height} px · {ui.human_size(cap.size)}"),
                ("Type", MODE_LABELS.get(cap.mode, cap.mode) + (" · annotated" if cap.has_annotations else ""))]
        if cap.app:
            rows.append(("Application", cap.app))
        if cap.title:
            rows.append(("Window", cap.title))
        if cap.trashed:
            rows.append(("Deleted", ui.human_date(cap.trashed)))
        for i, (k, v) in enumerate(rows):
            kl = Gtk.Label(label=k, xalign=0, yalign=0)
            kl.get_style_context().add_class("catcap-dim")
            vl = Gtk.Label(label=v, xalign=0, wrap=True, selectable=True, max_width_chars=32)
            vl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.details.attach(kl, 0, i, 1, 1)
            self.details.attach(vl, 1, i, 1, 1)
        self.details.show_all()
        self.tags_entry.set_text(", ".join(cap.tags))
        self.tag_model.clear()
        for tag, _n in self.library.all_tags():
            self.tag_model.append([tag])
        self.notes.get_buffer().set_text(cap.notes)
        names = [n for k, n, _c in self.library.collections() if k in self.library.capture_collections(cid)]
        self.collections_btn.set_label("Collections: " + (", ".join(names) if names else "none"))
        self.single_actions.set_visible(not in_trash)
        self.trash_actions.set_visible(in_trash)
        for w in (self.name_entry, self.tags_entry, self.notes, self.collections_btn, self.fav_btn):
            w.set_sensitive(not in_trash)
        self._loading_preview = False

    def _on_preview_click(self, _w, ev):
        if ev.type == Gdk.EventType._2BUTTON_PRESS and self._preview_id:
            self.open_editor(self._preview_id)

    def _save_name(self):
        if self._preview_id and not self._loading_preview:
            name = self.name_entry.get_text().strip()
            cap = self.by_id.get(self._preview_id)
            if name and cap and name != cap.name:
                self.library.update(self._preview_id, name=name)

    def _save_tags(self):
        if self._preview_id and not self._loading_preview:
            tags = [t for t in self.tags_entry.get_text().split(",") if t.strip()]
            cap = self.by_id.get(self._preview_id)
            if cap and sorted(self.library.normalize_tag(t) for t in tags) != sorted(cap.tags):
                self.library.set_tags(self._preview_id, tags)

    def _save_notes(self):
        if self._preview_id and not self._loading_preview:
            buf = self.notes.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            cap = self.by_id.get(self._preview_id)
            if cap and text != cap.notes:
                self.library.update(self._preview_id, notes=text)

    def _on_fav_toggled(self, btn):
        if self._preview_id and not self._loading_preview:
            self.library.set_favourite([self._preview_id], btn.get_active())

    def _tag_match(self, completion, key, it, _data):
        text = self.tags_entry.get_text()
        current = text.split(",")[-1].strip().lower()
        if not current:
            return False
        return completion.get_model()[it][0].startswith(current)

    def _tag_selected(self, _completion, model, it):
        parts = [p.strip() for p in self.tags_entry.get_text().split(",")[:-1] if p.strip()]
        parts.append(model[it][0])
        self.tags_entry.set_text(", ".join(parts) + ", ")
        self.tags_entry.set_position(-1)
        return True

    def _fill_collections_popover(self, pop, multi=False):
        if pop.get_child():
            pop.remove(pop.get_child())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=8)
        ids = self.selected_ids()
        member = self.library.capture_collections(ids[0]) if (ids and not multi) else set()
        for cid, name, _count in self.library.collections():
            if multi:
                b = Gtk.ModelButton(text=name)
                b.connect("clicked", lambda _b, k=cid: self.library.add_to_collection(self.selected_ids(), k))
            else:
                b = Gtk.CheckButton(label=name, active=cid in member)
                b.connect("toggled", self._on_collection_check, cid)
            box.add(b)
        new = Gtk.ModelButton(text="New collection…")
        new.connect("clicked", lambda *_: self.new_collection(add_selected=True))
        box.add(new)
        box.show_all()
        pop.add(box)

    def _on_collection_check(self, btn, collection_id):
        ids = self.selected_ids()
        if btn.get_active():
            self.library.add_to_collection(ids, collection_id)
        else:
            self.library.remove_from_collection(ids, collection_id)

    # ================================================================ actions
    def open_editor(self, capture_id):
        if self.query.view == "trash":
            return
        self.app.open_editor(capture_id)

    def copy_selected(self):
        ids = self.selected_ids()
        if ids:
            ui.copy_capture(self.library, ids[0])
            self.info_bar.set_text("Copied to clipboard")

    def export_selected(self):
        ui.export_dialog(self, self.library, self.config, self.selected_ids())

    def tag_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        tag = ui.prompt_text(self, "Add tag", f"Tag to add to {len(ids)} captures:")
        if tag:
            self.library.add_tag(ids, tag)

    def delete_selected(self, permanent=False):
        ids = self.selected_ids()
        if not ids:
            return
        if permanent or self.query.view == "trash":
            if ui.ask(self, f"Delete {len(ids)} capture{'s' if len(ids) > 1 else ''} permanently?",
                      "The original image and its annotations are removed from disk. This cannot be undone.",
                      "_Delete", destructive=True):
                self.library.delete_permanently(ids)
        else:
            self.library.trash(ids)
            self.info_bar.set_text(f"Moved {len(ids)} to Trash. Find it in Trash to restore.")

    def empty_trash(self):
        if ui.ask(self, "Empty trash?", "All captures in the trash are deleted permanently.", "_Empty trash",
                  destructive=True):
            self.library.empty_trash()

    def rebuild(self):
        added = self.library.rebuild()
        self.info_bar.set_text(f"Library rebuilt: {added} capture(s) recovered from disk.")

    def new_collection(self, add_selected=False):
        name = ui.prompt_text(self, "New collection", "Collection name:")
        if not name or not name.strip():
            return
        try:
            cid = self.library.create_collection(name)
        except Exception as exc:
            ui.error_dialog(self, "Could not create collection", str(exc))
            return
        if add_selected:
            ids = self.selected_ids()
            if ids:
                self.library.add_to_collection(ids, cid)

    def import_dialog(self):
        dialog = Gtk.FileChooserNative.new("Import images", self, Gtk.FileChooserAction.OPEN, "_Import", "_Cancel")
        dialog.set_select_multiple(True)
        flt = Gtk.FileFilter()
        flt.set_name("Images")
        flt.add_pixbuf_formats()
        dialog.add_filter(flt)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            self.import_paths(dialog.get_filenames())
        dialog.destroy()

    def import_paths(self, paths):
        done, failed = 0, []
        for p in paths:
            try:
                self.library.import_file(p)
                done += 1
            except Exception as exc:
                failed.append(f"{p}: {exc}")
        self.info_bar.set_text(f"Imported {done} image(s)." + (f" {len(failed)} failed." if failed else ""))
        if failed:
            ui.error_dialog(self, "Some files could not be imported", "\n".join(failed[:10]))

    def paste(self):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        pixbuf = clipboard.wait_for_image()
        if pixbuf is not None:
            cid = self.library.add_capture(pixbuf, mode="clipboard", name="Pasted " + time.strftime("%Y-%m-%d %H:%M"))
            GLib.timeout_add(200, lambda: (self.select_capture(cid), False)[1])
            return
        uris = clipboard.wait_for_uris()
        if uris:
            files = [GLib.filename_from_uri(u)[0] for u in uris if u.startswith("file://")]
            self.import_paths([f for f in files if ui.is_image_path(f)])
            return
        self.info_bar.set_text("The clipboard has no image.")

    # ================================================================ events
    def _on_search(self, entry):
        self.query.search = entry.get_text().strip()
        self.refresh()

    def _on_sort(self, combo):
        self.query.sort = combo.get_active_id()
        self.config.set("library", "sort", self.query.sort)
        self.refresh()

    def _on_view_toggle(self, *_):
        selected = set(self.selected_ids())
        self.config.set("library", "view", "list" if self.list_toggle.get_active() else "grid")
        self._show_view()
        self.icon_view.unselect_all()
        self.tree.get_selection().unselect_all()
        self._restore_selection(selected)

    def _on_filters(self, *_):
        if getattr(self, "_filling", False):
            return
        key = self.date_combo.get_active_id()
        now = time.time()
        lt = time.localtime()
        today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        self.query.date_from = {"today": today, "7d": now - 7 * 86400, "30d": now - 30 * 86400,
                                "year": time.mktime((lt.tm_year, 1, 1, 0, 0, 0, 0, 0, -1))}.get(key)
        self.query.app = self.app_combo.get_active_id() or None
        self.query.mode = self.mode_combo.get_active_id() or None
        self.query.annotated_only = self.annotated_check.get_active()
        self.refresh()

    def _reset_filters(self, *_):
        self._filling = True
        self.date_combo.set_active_id("any")
        self.app_combo.set_active_id("")
        self.mode_combo.set_active_id("")
        self.annotated_check.set_active(False)
        self._filling = False
        self._on_filters()

    def _on_sidebar_selected(self, _lb, row):
        if row is None or getattr(self, "_selecting_sidebar", False) or not getattr(row, "key", None):
            return
        key = row.key
        self.query.view = key[0]
        self.query.collection_id = key[1] if key[0] == "collection" else None
        self.query.tag = key[1] if key[0] == "tag" else None
        self.refresh()

    def _on_sidebar_button(self, lb, ev):
        if ev.button != 3:
            return False
        row = lb.get_row_at_y(int(ev.y))
        key = getattr(row, "key", None)
        menu = Gtk.Menu()
        if key and key[0] == "collection":
            for label, cb in (("Rename…", lambda: self._rename_collection(key[1])),
                              ("Delete collection", lambda: self._delete_collection(key[1]))):
                mi = Gtk.MenuItem(label=label)
                mi.connect("activate", lambda _w, c=cb: c())
                menu.append(mi)
        elif key and key[0] == "trash":
            mi = Gtk.MenuItem(label="Empty trash…")
            mi.connect("activate", lambda *_: self.empty_trash())
            menu.append(mi)
        else:
            return False
        menu.show_all()
        menu.attach_to_widget(lb, None)
        menu.popup_at_pointer(ev)
        return True

    def _rename_collection(self, collection_id):
        current = dict((k, n) for k, n, _c in self.library.collections()).get(collection_id, "")
        name = ui.prompt_text(self, "Rename collection", "New name:", current)
        if name and name.strip():
            self.library.rename_collection(collection_id, name)

    def _delete_collection(self, collection_id):
        if ui.ask(self, "Delete collection?", "The captures stay in the library.", "_Delete", destructive=True):
            if self.query.view == "collection" and self.query.collection_id == collection_id:
                self.sidebar.select_row(self.sidebar.get_row_at_index(0))
            self.library.delete_collection(collection_id)

    def _on_sidebar_drop(self, lb, _ctx, _x, y, selection, _info, _time):
        row = lb.get_row_at_y(y)
        key = getattr(row, "key", None)
        ids = [i for i in (selection.get_text() or "").split("\n") if i]
        if not key or not ids:
            return
        if key[0] == "collection":
            self.library.add_to_collection(ids, key[1])
        elif key[0] == "favourites":
            self.library.set_favourite(ids, True)
        elif key[0] == "trash":
            self.library.trash(ids)
        elif key[0] == "tag":
            self.library.add_tag(ids, key[1])

    def _on_drag_data_get(self, _w, _ctx, selection, info, _time):
        ids = self.selected_ids()
        if info == 1:
            selection.set_text("\n".join(ids), -1)
        else:
            selection.set_uris([ui.file_uri(ui.temp_render(self.library, i, self.config)) for i in ids])

    def _on_external_drop(self, _w, ctx, _x, _y, selection, _info, _time):
        if Gtk.drag_get_source_widget(ctx) is not None:
            return
        files = []
        for uri in selection.get_uris() or []:
            if uri.startswith("file://"):
                path = GLib.filename_from_uri(uri)[0]
                if ui.is_image_path(path):
                    files.append(path)
        if files:
            self.import_paths(files)

    def _on_grid_button(self, view, ev):
        if ev.button != 3:
            return False
        path = view.get_path_at_pos(int(ev.x), int(ev.y))
        if path is not None and not view.path_is_selected(path):
            view.unselect_all()
            view.select_path(path)
        self._popup_item_menu(ev)
        return True

    def _on_tree_button(self, tree, ev):
        if ev.button != 3:
            return False
        hit = tree.get_path_at_pos(int(ev.x), int(ev.y))
        if hit:
            sel = tree.get_selection()
            if not sel.path_is_selected(hit[0]):
                sel.unselect_all()
                sel.select_path(hit[0])
        self._popup_item_menu(ev)
        return True

    def _on_tree_activated(self, tree, path, _col):
        cid = self.list_store[path][L_ID]
        if cid:
            self.open_editor(cid)

    def _popup_item_menu(self, ev):
        ids = self.selected_ids()
        if not ids:
            return
        menu = Gtk.Menu()
        if self.query.view == "trash":
            entries = [("Restore", lambda: self.library.restore(ids)),
                       ("Delete permanently", lambda: self.delete_selected(permanent=True))]
        else:
            entries = []
            if len(ids) == 1:
                entries += [("Edit", lambda: self.open_editor(ids[0])), ("Copy", self.copy_selected),
                            ("Pin to screen", lambda: self.app.pin_surface(render.render_capture(self.library, ids[0])))]
            entries += [("Export…", self.export_selected), ("Add tag…", self.tag_selected),
                        ("Favourite", lambda: self.library.set_favourite(ids, True)),
                        ("Move to trash", self.delete_selected)]
            if self.query.view == "collection":
                entries.append(("Remove from this collection",
                                lambda: self.library.remove_from_collection(ids, self.query.collection_id)))
        for label, cb in entries:
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _w, c=cb: c())
            menu.append(mi)
        menu.show_all()
        menu.attach_to_widget(self, None)
        menu.popup_at_pointer(ev)

    def _on_key(self, _w, ev):
        focus = self.get_focus()
        typing = isinstance(focus, (Gtk.Entry, Gtk.TextView))
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        key = Gdk.keyval_to_lower(ev.keyval)
        if ctrl and key == Gdk.KEY_f:
            self.search.grab_focus()
            return True
        if ctrl and key == Gdk.KEY_n:
            self.app.start_capture("region", hide=self)
            return True
        if typing:
            return False
        if ctrl and key == Gdk.KEY_v:
            self.paste()
            return True
        if ctrl and key == Gdk.KEY_c:
            self.copy_selected()
            return True
        if ctrl and key == Gdk.KEY_e:
            self.export_selected()
            return True
        if key == Gdk.KEY_Delete:
            self.delete_selected(permanent=shift)
            return True
        if key == Gdk.KEY_F2:
            self.name_entry.grab_focus()
            return True
        if ctrl and key in (Gdk.KEY_question, Gdk.KEY_slash):
            self.app.show_shortcuts(self)
            return True
        return False
