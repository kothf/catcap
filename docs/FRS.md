# CatCap — Functional Requirements Specification (FRS)

| Item | Value |
|---|---|
| Product | CatCap — screenshot capture, annotation and library app |
| Target platform | Linux Mint 22.x and newer (Cinnamon edition first; MATE and Xfce best-effort). Mint 21.x works from source with PyQt6 from pip. |
| Document version | 0.3 (draft) |
| Date | 2026-11-24 |
| Status | For review — open questions resolved (see §12) |

---

## 1. Introduction

### 1.1 Purpose
This document specifies the functional and non-functional requirements for **CatCap**, a desktop application for Linux Mint that captures still screenshots, lets the user annotate and edit them, and keeps every capture in a searchable, organised **library**. CatCap is similar in scope to Snagit's image workflow. **Video and screen recording are out of scope.**

### 1.2 Scope
CatCap covers:
- Capturing images of the screen (full screen, a monitor, a window, a region, a menu, a scrolling area).
- Editing captures with annotations (arrows, text, shapes, blur, step numbers, and so on).
- Storing every capture automatically in a local library with metadata, tags, collections and search.
- Sharing and exporting captures (clipboard, file, drag-and-drop, email or other apps).

CatCap does **not** cover:
- Video, GIF or screen recording, or audio capture.
- Cloud sync or a hosted sharing service in v1 (see §10, Future).
- Windows or macOS builds.

### 1.3 Definitions

| Term | Meaning |
|---|---|
| Capture | A single still image produced by a capture action. |
| Library | The local database plus the image store that holds all captures. |
| Editor | The window where the user annotates and edits a capture. |
| Annotation | A non-destructive object drawn over a capture (arrow, text, shape and so on). |
| Original | The unmodified pixels as captured; always kept. |
| Rendered | The flattened image (original plus annotations) used for export. |
| Collection | A user-defined folder-like grouping of captures (a capture can belong to several). |
| Tag | A free-text label attached to a capture. |
| Preset | A saved capture configuration (mode, delay, destination and so on). |

### 1.4 Priority keys
- **M**: Must have (v1.0)
- **S**: Should have (v1.x)
- **C**: Could have (later)

---

## 2. Overall description

### 2.1 Product perspective
CatCap is a standalone native desktop app. It runs as a **tray/background process** (for global hotkeys and fast capture) plus on-demand windows (the Capture overlay, the Editor and the Library).

### 2.2 User classes
- **Everyday user**: quick screenshots to share in chat or email.
- **Documentation writer / support engineer**: annotated, step-numbered screenshots and consistent styling.
- **Developer / QA**: bug reports, pixel-accurate captures, colour picking, measurements.

### 2.3 Operating environment
- Linux Mint 21.x and 22.x, x86_64.
- Display server: **both X11 (Mint's default) and Wayland (Cinnamon Wayland session) are supported from v1.0**. On Wayland, capture goes through `xdg-desktop-portal`; see §8.3 for the per-feature behaviour.
- Multi-monitor setups with mixed DPI and scaling (100%, 125%, 150%, 200%).

### 2.4 Design and implementation constraints
- Must integrate with the Cinnamon panel tray (StatusNotifierItem / XEmbed).
- Must follow freedesktop.org standards: XDG base directories, `.desktop` file, icon theme, MIME types.
- Must work offline. No telemetry by default.
- Technology stack (decided, revised in 0.3): **Python 3 + Qt 6 (PyQt6)** with CatCap's own visual design (light and dark), **SQLite** for the library and **QPainter** for imaging. X11 window detection uses `python-xlib`; Wayland capture uses the `xdg-desktop-portal` D-Bus API through Qt D-Bus. Global hotkeys are written to Cinnamon's settings with the `gsettings` tool.
- Licence: **MIT**.

### 2.5 Assumptions and dependencies
- The `xdg-desktop-portal` Screenshot interface is available on Wayland.
- On X11, XComposite/XFixes are available for window and cursor capture.
- Optional OCR depends on Tesseract being installed.

---

## 3. Functional requirements: Capture

### 3.1 Capture modes

| ID | Requirement | Priority |
|---|---|---|
| CAP-01 | **Full screen**: capture all monitors as one image. | M |
| CAP-02 | **Single monitor**: capture the monitor under the cursor, or one the user picks. | M |
| CAP-03 | **Region**: the user drags a rectangle on a frozen-screen overlay. | M |
| CAP-04 | **Window**: hovering highlights the window under the cursor; a click captures it. Option to include or exclude the window decoration and shadow. | M |
| CAP-05 | **Active window**: capture the focused window without any interaction. | M |
| CAP-06 | **Repeat last region**: re-capture the exact rectangle used last. | M |
| CAP-07 | **Fixed-size region**: capture a region of preset dimensions (for example 1280×720), positioned by the user. | S |
| CAP-08 | **Menu capture**: capture an open context or drop-down menu, using a delay. | S |
| CAP-09 | **Scrolling capture**: auto-scroll a window or region and stitch the frames into one tall image, with manual stop. | S |
| CAP-10 | **Freehand / polygon region**: non-rectangular selection, with a transparent background outside the shape. | C |
| CAP-11 | **Panoramic (manual) scroll**: the user scrolls by hand while CatCap stitches. | C |

### 3.2 Region selection overlay

| ID | Requirement | Priority |
|---|---|---|
| OVL-01 | The overlay freezes the screen at the moment capture is invoked, so tooltips and hover states are preserved. | M |
| OVL-02 | Shows a crosshair, live W×H dimensions and X,Y coordinates. | M |
| OVL-03 | A **magnifier loupe** (configurable zoom) for pixel-precise edges. | M |
| OVL-04 | Arrow keys nudge the selection by 1 px; Shift+arrow by 10 px; Ctrl+arrow resizes. | M |
| OVL-05 | The selection can be resized and moved with handles before confirming. Enter or double-click confirms; Esc cancels. | M |
| OVL-06 | Snap to window and UI-element edges when near them (toggleable). | S |
| OVL-07 | Shows the colour value (HEX/RGB) under the cursor; a key copies it. | S |
| OVL-08 | A quick-action toolbar after selection: Copy, Save, Edit, Pin, Cancel. | S |

### 3.3 Capture options

| ID | Requirement | Priority |
|---|---|---|
| OPT-01 | **Delay timer**: 0–60 s, with a visible countdown. | M |
| OPT-02 | Include or exclude the **mouse cursor**. | M |
| OPT-03 | **After-capture action**, configurable: open in Editor / copy to clipboard / save silently to library / show quick toolbar. | M |
| OPT-04 | Play a capture sound (toggleable) and show a desktop notification with a thumbnail (toggleable). | S |
| OPT-05 | **Capture presets**: named combinations of mode + delay + cursor + after-action + effects, each bindable to a hotkey. | S |
| OPT-06 | Auto-apply effects on capture (border, shadow, watermark) per preset. | C |

### 3.4 Triggers and hotkeys

| ID | Requirement | Priority |
|---|---|---|
| TRG-01 | **Global hotkeys** for each capture mode, user-configurable, with conflict detection against Cinnamon's own bindings. | M |
| TRG-02 | Optionally take over the **Print Screen** key (with a guided step that frees it from Cinnamon's default screenshot tool). | M |
| TRG-03 | **Tray icon menu** with all capture modes, Open Library, Settings and Quit. | M |
| TRG-04 | **Command-line interface**, for example `catcap --region`, `catcap --window --delay 3 --clipboard`, `catcap --library`. | M |
| TRG-05 | Optional **D-Bus API** for automation and Cinnamon applets. | C |

---

## 4. Functional requirements: Editor

### 4.1 General

| ID | Requirement | Priority |
|---|---|---|
| EDT-01 | Opens a capture in a canvas with zoom (fit, 100%, 25–800%) and pan (space+drag, scroll wheel). | M |
| EDT-02 | **Non-destructive editing**: annotations are stored as objects. The original pixels are never overwritten, and annotations stay editable after saving and reopening. | M |
| EDT-03 | Unlimited **undo/redo** within a session (Ctrl+Z / Ctrl+Shift+Z). | M |
| EDT-04 | Select, move, resize, rotate, re-order (z-order), duplicate and delete annotations. | M |
| EDT-05 | A properties panel for the selected object: colour, opacity, stroke width, fill, font, shadow. | M |
| EDT-06 | **Quick styles**: built-in and user-saved style presets per tool. | S |
| EDT-07 | Autosave of edits to the library (no "unsaved changes" loss on close or crash). | M |
| EDT-08 | A recent-captures strip (filmstrip) at the bottom to switch between captures without opening the Library. | S |
| EDT-09 | Revert to the original at any time. | M |

### 4.2 Annotation tools

| ID | Tool | Details | Priority |
|---|---|---|---|
| ANN-01 | Arrow | Straight and curved; head styles; thickness. | M |
| ANN-02 | Line | Solid or dashed. | M |
| ANN-03 | Rectangle / Ellipse | Outline, fill, rounded corners. | M |
| ANN-04 | Text | Font, size, colour, background box, alignment; multiline. | M |
| ANN-05 | Callout | Speech-bubble shapes with editable text and a tail. | M |
| ANN-06 | Highlighter | Semi-transparent marker, freehand or rectangle. | M |
| ANN-07 | Pen | Freehand drawing with smoothing. | M |
| ANN-08 | **Step / counter** | Auto-incrementing numbered badges (1, 2, 3… / A, B, C…); renumber on delete. | M |
| ANN-09 | **Blur / Pixelate** | Region obfuscation for sensitive data. Must be irreversible in exported output. | M |
| ANN-10 | **Spotlight** | Dim everything except the selected area(s). | S |
| ANN-11 | **Magnify** | A circular or rectangular zoomed inset of part of the image. | S |
| ANN-12 | Stamp / Sticker | Built-in icon set (check, cross, warning, info) plus user images. | S |
| ANN-13 | Eraser / Fill | Fill a region with a sampled colour, to hide content. | S |
| ANN-14 | **Smart redact** | Detect text with OCR and blur emails, IPs, numbers or user-chosen patterns. | C |
| ANN-15 | Cursor overlay | Add or move a mouse-pointer graphic after capture. | C |

### 4.3 Image operations

| ID | Requirement | Priority |
|---|---|---|
| IMG-01 | Crop, with aspect-ratio presets and a free mode. | M |
| IMG-02 | Resize (px or %), keeping or ignoring the aspect ratio. | M |
| IMG-03 | Rotate 90°/180° and flip. | M |
| IMG-04 | Expand canvas (add padding) with a background colour. | S |
| IMG-05 | Effects: border, drop shadow, torn edge, perspective, rounded corners. | S |
| IMG-06 | **Cut-out**: remove a horizontal or vertical strip and join the remaining pieces (optionally with a jagged edge). | S |
| IMG-07 | Colour picker (eyedropper) that copies HEX/RGB. | S |
| IMG-08 | Combine multiple captures into one image (side-by-side or grid). | C |
| IMG-09 | Templates: device frames, step-by-step layout. | C |

### 4.4 Text and OCR

| ID | Requirement | Priority |
|---|---|---|
| OCR-01 | **Grab text**: extract text from a capture or region (Tesseract) and copy it to the clipboard. | S |
| OCR-02 | OCR text is indexed in the library for search (§5.4). | S |
| OCR-03 | OCR language selectable from the installed Tesseract language packs. | S |

---

## 5. Functional requirements: Library

### 5.1 Storage

| ID | Requirement | Priority |
|---|---|---|
| LIB-01 | **Every capture is automatically saved to the library** (unless the user discards it), including the original, the annotation data and a thumbnail. | M |
| LIB-02 | Library location defaults to `~/Pictures/CatCap/` (images) and `$XDG_DATA_HOME/catcap/` (database). Both are configurable. | M |
| LIB-03 | Storage format: the original as PNG, annotations as JSON (versioned schema), and a thumbnail cache in `$XDG_CACHE_HOME/catcap/`. **Rendered (flattened) images are not stored in the library.** They are generated on demand for copy, export, drag-and-drop, pin and print; temporary files for drag-and-drop go to `$XDG_RUNTIME_DIR/catcap/` and are deleted when no longer needed. Thumbnails show the image with its annotations. | M |
| LIB-04 | Metadata per capture: ID, created date/time, capture mode, dimensions, file size, source **application name and window title** (where available), monitor, tags, collections, favourite flag, notes, OCR text. | M |
| LIB-05 | A library rebuild/repair command that rescans the image folder and restores database records. | S |
| LIB-06 | Move the library to a new location, with migration of the files. | S |

### 5.2 Browsing

| ID | Requirement | Priority |
|---|---|---|
| BRW-01 | **Grid view** of thumbnails with adjustable size; **list view** with sortable columns. | M |
| BRW-02 | Grouping by date (Today, Yesterday, This week, then by month). | M |
| BRW-03 | Sort by date, name, size, dimensions or application. | M |
| BRW-04 | A sidebar with: All captures, Favourites, Recent, Collections, Tags, Trash. | M |
| BRW-05 | A **preview pane** with a larger image and metadata; double-click opens the Editor. | M |
| BRW-06 | Lazy loading and virtualised scrolling, so the view stays smooth with 10,000+ captures. | M |
| BRW-07 | A timeline/calendar filter. | C |

### 5.3 Organising

| ID | Requirement | Priority |
|---|---|---|
| ORG-01 | Rename a capture (display name). | M |
| ORG-02 | Add and remove **tags**, with autocomplete from existing tags. | M |
| ORG-03 | Create, rename and delete **collections**; add captures by drag-and-drop or menu. A capture may be in several collections. | M |
| ORG-04 | Mark as **favourite**. | M |
| ORG-05 | Multi-select (Ctrl/Shift click, rubber band) with bulk actions: tag, add to collection, export, delete. | M |
| ORG-06 | **Trash** with restore and empty. Items are auto-purged after N days (default 30, configurable). | M |
| ORG-07 | Free-text notes per capture. | S |
| ORG-08 | Auto-tag by source application (for example `firefox`, `terminal`). | S |
| ORG-09 | Duplicate detection (perceptual hash) with a merge/delete suggestion. | C |

### 5.4 Search and filter

| ID | Requirement | Priority |
|---|---|---|
| SRC-01 | Instant search box over name, tags, notes, application and window title. | M |
| SRC-02 | Filters: date range, tag, collection, application, capture mode, has-annotations, favourite. | M |
| SRC-03 | Search inside **OCR text** (when OCR is enabled). | S |
| SRC-04 | Saved searches (smart collections). | C |

### 5.5 Import

| ID | Requirement | Priority |
|---|---|---|
| IMP-01 | Import existing images (PNG, JPG, WebP, BMP, GIF first frame) by file dialog or drag-and-drop onto the Library. | M |
| IMP-02 | Import from the clipboard (Ctrl+V in the Library or Editor creates a new capture). | M |
| IMP-03 | Optionally watch a folder and auto-import new images into the library. | C |

---

## 6. Functional requirements: Output and sharing

| ID | Requirement | Priority |
|---|---|---|
| OUT-01 | **Copy to clipboard** as an image (rendered), from the Editor, the Library or the quick toolbar. | M |
| OUT-02 | **Save / Export as** PNG, JPG (quality setting), WebP or BMP. | M |
| OUT-03 | Export to PDF (single, or several captures as pages). | S |
| OUT-04 | **Drag-and-drop** from the Editor or Library into other apps (file manager, browser, chat clients). | M |
| OUT-05 | Configurable filename template, for example `{app}_{yyyy-MM-dd}_{HHmmss}`. | M |
| OUT-06 | "Open with…" an external app; "Show in file manager" (Nemo). | M |
| OUT-07 | Send by email (via `xdg-email` attachment). | S |
| OUT-08 | **Pin to screen**: a floating, always-on-top borderless window showing the capture (for reference while working). | S |
| OUT-09 | Print. | C |
| OUT-10 | Export annotations-only as a project file (`.catcap`: a zip of original + JSON) for sharing editable captures. | C |
| OUT-11 | Plug-in or custom-command destinations (for example run a script or upload with a user-supplied command). | C |

---

## 7. Functional requirements: Settings and app behaviour

| ID | Requirement | Priority |
|---|---|---|
| SET-01 | Settings window with sections: General, Capture, Hotkeys, Editor, Library, Output, Advanced. | M |
| SET-02 | Start at login (writes an `~/.config/autostart` entry). | M |
| SET-03 | Theme follows the system (light/dark, Mint-Y accent colours). | M |
| SET-04 | Default annotation styles (colour, width, font). | M |
| SET-05 | Settings stored in `$XDG_CONFIG_HOME/catcap/config.toml` (human-readable). | M |
| SET-06 | Export and import of settings. | C |
| APP-01 | Single instance: invoking `catcap` again forwards the command to the running instance. | M |
| APP-02 | Keyboard shortcuts for all frequent actions, plus a shortcuts help dialog (Ctrl+?). | M |
| APP-03 | Localisation-ready (gettext), English at v1. | M |
| APP-04 | First-run wizard: library location, Print Screen takeover, after-capture action. | S |

---

## 8. Non-functional requirements

### 8.1 Performance

| ID | Requirement |
|---|---|
| NFR-P1 | Hotkey press to overlay visible: **≤ 250 ms** (app running in tray). |
| NFR-P2 | Capture confirm to Editor open: **≤ 500 ms** for a 4K capture. |
| NFR-P3 | Library open with 10,000 captures: first thumbnails in **≤ 1 s**; scrolling at 60 fps. |
| NFR-P4 | Search results update in **≤ 200 ms** for 10,000 items (excluding OCR indexing). |
| NFR-P5 | Idle tray process memory **≤ 80 MB**. |
| NFR-P6 | On-demand render of a 4K capture with annotations (for copy, export or drag) in **≤ 300 ms**. |

### 8.2 Reliability and data safety
- NFR-R1: Database writes are transactional (SQLite WAL). A crash must not corrupt the library.
- NFR-R2: Originals are never modified after capture.
- NFR-R3: Automatic database backup on version upgrade.
- NFR-R4: Blur, pixelate and redaction are applied destructively to **exported** images, so hidden data cannot be recovered from the exported file. Exported files strip any annotation layers.

### 8.3 Compatibility
- NFR-C1: Full feature set on X11 (Cinnamon, MATE, Xfce).
- NFR-C2: **Wayland is supported in v1.0** (Cinnamon Wayland session, plus any desktop with `xdg-desktop-portal`). Display-server detection happens at runtime; one build serves both.

| Feature | X11 | Wayland (v1.0) |
|---|---|---|
| Full screen / monitor | Native (XGetImage/XComposite) | Portal Screenshot, non-interactive |
| Region + frozen overlay, magnifier | Native | Portal full-screen grab, then CatCap's own full-screen overlay for selection (same UX) |
| Window capture | Hover-highlight + click | Portal interactive window picker |
| Active window | Native | Portal interactive picker (no silent capture) |
| Scrolling capture | Auto-scroll via XTest | Manual-scroll stitching (CAP-11) only |
| Global hotkeys | Native key grab | `GlobalShortcuts` portal where available; otherwise CatCap registers its CLI commands as Cinnamon custom shortcuts |
| Source app / window title metadata | Yes | Not available (left empty) |
| Exclude windows from capture (NFR-S3) | Yes | Not available |

- NFR-C2a: Where a Wayland limitation applies, the UI must say so (for example a note in the mode menu) instead of failing silently.
- NFR-C2b: Portal permission prompts are handled gracefully; if the user denies permission, CatCap shows how to grant it.
- NFR-C3: HiDPI: captures are saved at native pixel resolution; the UI scales correctly.
- NFR-C4: Multi-monitor with different resolutions and scale factors, including negative coordinates.

### 8.4 Usability and accessibility
- NFR-U1: Every function is reachable by keyboard.
- NFR-U2: Works with the Orca screen reader for the Library and Settings (ATK labels).
- NFR-U3: Respects system font size and high-contrast themes.
- NFR-U4: A first capture is possible within 30 seconds of first launch, with no configuration.

### 8.5 Security and privacy
- NFR-S1: No network access in v1. No telemetry.
- NFR-S2: Library files are created with user-only permissions (0600/0700).
- NFR-S3: Optional "exclude from capture" for password-manager windows (by WM_CLASS) on X11.

### 8.6 Packaging and distribution
- NFR-D1: `.deb` package installable on Mint 21/22 with declared dependencies.
- NFR-D2: Flatpak (Flathub) build.
- NFR-D3: Appears in the Mint menu with an icon. Registers as a handler for opening images ("Open with CatCap").
- NFR-D4: Licence: **MIT**.

---

## 9. Main use cases

**UC-1: Quick region capture to chat**
1. The user presses Print Screen.
2. The overlay appears; the user drags a region and presses Enter.
3. The quick toolbar appears; the user clicks Copy.
4. The capture goes to the clipboard and is saved in the library automatically.

**UC-2: Annotated documentation screenshot**
1. The user presses the Window-capture hotkey and clicks the target window.
2. The Editor opens. The user adds three step badges, an arrow and a text callout, and blurs an email address.
3. The user exports it as PNG via drag-and-drop into the documentation tool.
4. Later, the user finds the capture in the Library by searching for the tag "docs" and edits the callout text (the annotations are still editable).

**UC-3: Find an old screenshot**
1. The user opens the Library and types "invoice" in search.
2. Results match the window title and OCR text. The user filters to "last month".
3. The user opens the capture in the preview and copies it to the clipboard.

**UC-4: Long web page**
1. The user picks Scrolling capture and clicks inside the browser window.
2. CatCap scrolls and stitches the page, and the user presses Esc to stop early.
3. The tall image opens in the Editor.

---

## 10. Out of scope / future

- Video, GIF and audio recording (explicitly excluded).
- Cloud sync or hosted share links (possible v2: user-configured WebDAV, S3 or Nextcloud).
- AI-assisted features (auto-describe, smart redact beyond OCR patterns).
- Mobile companion apps.

---

## 11. Acceptance criteria (v1.0 release gate)

1. All **M** requirements are implemented and pass manual test scripts on Linux Mint 22 Cinnamon in **both X11 and Wayland sessions** (with the Wayland differences in §8.3), at 1× and 2× scale, with single and dual monitors.
2. Every capture mode produces a library entry with correct metadata.
3. Annotations survive save, app restart and reopen, and can still be edited.
4. Exported images with blur or pixelate contain no recoverable original pixels in the blurred area.
5. The performance targets in §8.1 are met on reference hardware (4-core CPU, 8 GB RAM, SSD).
6. The `.deb` installs, launches from the menu, registers hotkeys and uninstalls cleanly.

---

## 12. Decisions (resolved open questions)

| # | Question | Decision |
|---|---|---|
| 1 | Technology stack | **Python 3 + Qt 6 (PyQt6)**, chosen in 0.3 for a modern custom look (first build used GTK 3). |
| 2 | Store rendered PNGs next to originals? | **No.** Render on demand only (LIB-03, NFR-P6). |
| 3 | Wayland in v1? | **Yes.** v1.0 runs on both X11 and Wayland (§2.3, §8.3). |
| 4 | Licence | **MIT**. |
