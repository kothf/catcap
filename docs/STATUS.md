# Implementation status (v0.2.1)

v0.2.0 moved the interface from GTK 3 to Qt 6 (PyQt6) with CatCap's own design; v0.2.1 made CatCap tray-first.
Since then: safer library rebuild and move, a solid Redact tool, a more responsive library and export,
mixed-DPI and multi-monitor Wayland fixes, lint in CI and offscreen GUI tests.

Mapped against [FRS.md](FRS.md). ✅ done · 🟡 partial · ⬜ not yet.

## Capture
| ID | Status | Notes |
|---|---|---|
| CAP-01 Full screen | ✅ | All monitors in one image |
| CAP-02 Single monitor | ✅ | Monitor under the pointer |
| CAP-03 Region | ✅ | Frozen-screen overlay |
| CAP-04 Window | ✅ | Hover highlight + click (X11); portal picker (Wayland). Decorations on/off setting |
| CAP-05 Active window | ✅ | Silent on X11; portal picker on Wayland |
| CAP-06 Repeat last region | ✅ | |
| CAP-07 Fixed-size region | ⬜ | |
| CAP-08 Menu capture | 🟡 | Use a delayed capture (tray › Region after delay) |
| CAP-09/11 Scrolling capture | ⬜ | |
| CAP-10 Freehand region | ⬜ | |
| OVL-01..05 Overlay basics | ✅ | Crosshair, size label, magnifier, arrow-key nudge, handles, Enter/double-click/Esc |
| OVL-06 Snap | 🟡 | Snaps to windows, not to UI elements |
| OVL-07 Colour under cursor | ✅ | Shown in magnifier, `C` copies HEX |
| OVL-08 Quick toolbar | ✅ | In the overlay (Capture/Copy/Edit/Pin) and after capture (Copy/Edit/Pin/Library/Discard) |
| OPT-01..03 | ✅ | Delay with countdown, cursor (X11), after-capture action |
| OPT-04 | ✅ | Sound + notification toggles |
| OPT-05/06 Presets | ⬜ | |
| TRG-01/02 Hotkeys, Print Screen | ✅ | Cinnamon custom shortcuts (X11 + Wayland), Print takeover with restore (`catcap --release-hotkeys`), conflict warnings. Other desktops: bind the shown commands manually |
| TRG-03 Tray | ✅ | Runs in the tray (StatusNotifierItem / XEmbed): left-click captures, menu with all capture types, recent captures, copy last, start at login. Hotkeys reach the running instance in about 40 ms |
| TRG-04 CLI | ✅ | `catcap --help`; forwarded to the running instance without starting Qt |
| TRG-05 D-Bus API | ⬜ | |

## Editor
| ID | Status | Notes |
|---|---|---|
| EDT-01..05, 07, 09 | ✅ | Zoom/pan, non-destructive JSON, undo/redo, select/move/resize/reorder/duplicate/delete, properties bar, autosave, revert |
| EDT-06 Quick styles | 🟡 | Each tool remembers its last style; no named style presets yet |
| EDT-08 Filmstrip | ⬜ | |
| ANN-01..09 | ✅ | Arrow (straight), line, rectangle, ellipse, text, callout, highlighter, pen, numbered steps (1,2,3 / A,B,C, auto-renumber), blur, pixelate |
| ANN-10 Spotlight | ✅ | |
| ANN-14 Redact | 🟡 | Manual solid redact box (K); no automatic detection yet |
| ANN-11..13, 15 | ⬜ | Magnify, stamps, fill, cursor overlay |
| IMG-01..03 | ✅ | Crop (with aspect presets), resize, rotate, flip |
| IMG-04..09 | ⬜ | Canvas expand, effects, cut-out, eyedropper in editor, combine, templates |
| OCR-01..03 | ⬜ | |

## Library
| ID | Status | Notes |
|---|---|---|
| LIB-01..05 | ✅ | Auto-save, XDG locations, PNG + JSON sidecar, metadata incl. app/window title (X11), rebuild from disk |
| LIB-06 Move library | ✅ | Settings › Library |
| BRW-01..06 | ✅ | Grid + list, date groups (list view), sort, sidebar, preview pane, lazy thumbnails |
| ORG-01..07 | ✅ | Rename, tags with autocomplete, collections (drag captures onto them), favourites, bulk actions, trash with auto-purge, notes |
| ORG-08/09 | ⬜ | |
| SRC-01/02 | ✅ | Instant search; filters for date, app, type, annotated; tag/collection via sidebar |
| SRC-03/04 | ⬜ | |
| IMP-01/02 | ✅ | File dialog, drag-and-drop, Ctrl+V |
| IMP-03 | ⬜ | |

## Output
| ID | Status | Notes |
|---|---|---|
| OUT-01..06 | ✅ | Clipboard, PNG/JPG/WebP/BMP, PDF (multi-page), drag-and-drop, filename templates, open with / show in folder |
| OUT-07 Email | ✅ | via `xdg-email` |
| OUT-08 Pin | ✅ | |
| OUT-09..11 | ⬜ | |

## Settings, platform, packaging
| Item | Status | Notes |
|---|---|---|
| SET-01..05, APP-01, APP-02, APP-04 | ✅ | |
| APP-03 Localisation | ⬜ | Strings are not wrapped for translation yet |
| Wayland (NFR-C2) | ✅ | Portal capture, own overlay on every monitor, clear messages; tested against a mock portal, not yet on a real Cinnamon Wayland session |
| HiDPI / multi-monitor | 🟡 | Scale-aware code; mixed scaling on X11 is resampled to one common scale. Not yet tested on real hardware |
| NFR-S3 Exclude windows | ⬜ | |
| `.deb` package (NFR-D1) | ✅ | `packaging/build-deb.sh`, built in CI |
| Flatpak (NFR-D2) | ⬜ | |

## Tested so far
- Unit tests: config, library, annotations, rendering/export, filename templates, hotkey parsing, Cinnamon hotkey takeover/restore against the real schemas, the Wayland portal flow against a mock `xdg-desktop-portal`, library rebuild/move safety, schema migrations, and
  offscreen GUI smoke tests (editor tools and autosave, library window, export, drag data, overlay, CLI forwarding).
- GUI runs under Xvfb + Openbox: full/active/region capture, overlay, quick toolbar, editor tools, crop, library grid/list/preview, settings.
- Not yet run on a real Linux Mint desktop. Please report anything odd.
