# CatCap

Screenshot capture, annotation and library app for Linux Mint (images only, no video).
Works on X11 and Wayland.

- [Functional Requirements Specification](docs/FRS.md)
- [What is implemented so far](docs/STATUS.md)

## Try it from this folder

```bash
./install-deps.sh      # once: installs PyQt6 and the other packages CatCap needs
./run-catcap           # opens the library and puts CatCap in the tray
```

Useful commands:

```bash
./run-catcap --region          # select a region or click a window
./run-catcap --window          # pick a window
./run-catcap --full --edit     # whole screen, open in the editor
./run-catcap --region --delay 5 --clipboard
./run-catcap --help            # all options
```

CatCap lives in the panel tray, like other screenshot tools: left-click the icon to capture a
region, middle-click to open the library, right-click for every capture type, recent captures,
“Copy last capture” and “Start at login”. Closing the library keeps it running in the tray.
It starts at login by default (untick it in the welcome dialog, the tray menu or Settings).

After you select a region, a tool column next to it lets you draw straight on the frozen screen,
pen, line, arrow, rectangle, ellipse, marker, text, numbered steps, blur, pixelate and
redact, with a colour button, undo/redo (Ctrl+Z) and the mouse wheel for thickness. What you draw is
saved as normal annotations, so you can still change it later in the editor.

Global hotkeys (Print Screen and friends) are switched on in the welcome dialog or in
**Settings › Hotkeys**. On Cinnamon they are registered as Cinnamon custom shortcuts, so they
also work in the Wayland session.

## Which Linux Mint?

The `.deb` package is for **Linux Mint 22 or newer** (Ubuntu 24.04 base), which ships PyQt6.
Linux Mint 21 has no PyQt6 package: run CatCap from this folder instead; `./install-deps.sh`
then installs PyQt6 with pip for your user.

## Install as a .deb package

```bash
./packaging/build-deb.sh                          # creates dist/catcap_<version>_all.deb
sudo apt install ./dist/catcap_0.2.4_all.deb      # then start “CatCap” from the Mint menu
```

GitHub Actions also builds the package on every push (download it from the run's
“catcap-deb” artifact) and attaches it to releases tagged `v*`.

Uninstall with `sudo apt remove catcap`. Your captures stay in `~/Pictures/CatCap`.

## Where things are stored

| What | Where |
|---|---|
| Original captures (PNG, never modified) | `~/Pictures/CatCap/YYYY/MM/` |
| Annotations (JSON next to each PNG) | same folder |
| Library database | `~/.local/share/catcap/library.db` |
| Thumbnails | `~/.cache/catcap/thumbs/` |
| Settings | `~/.config/catcap/config.toml` |

Edited images are rendered on demand when you copy, export or drag them; they are not stored.
The PNGs in the image folder are always the untouched originals, so blurred or redacted areas are
still visible in them: share captures by copying, dragging or exporting them from CatCap.

**Rebuild library from disk** (library menu) re-adds PNGs that are missing from the database. Captures
whose file can't be found are kept unless you confirm removing them, so a library on an unplugged
drive doesn't lose its tags and notes.

## Development

```bash
python3 -m unittest discover -s tests -v                 # core and offscreen GUI tests
dbus-run-session -- python3 -m unittest discover -s tests  # also runs the Wayland portal tests
pipx run ruff check catcap tests                         # lint (CI runs this too)
```

The library database carries a schema version. When you change `SCHEMA` in `library.py`, bump
`SCHEMA_VERSION` and add the upgrade statements to `MIGRATIONS` so existing libraries are upgraded.

Built with Python 3 and Qt 6 (PyQt6). Code layout: `catcap/capture/` (X11 and portal backends),
`overlay.py` (region selection), `editor.py` (+ `editor_tools.py`, `editor_widgets.py`),
`library_window.py` (+ `library_views.py`), `library.py` (SQLite),
`annotations.py` + `render.py` (non-destructive editing), `theme.py` (palette, stylesheet, icons),
`hotkeys.py`, `tray.py`, `app.py` (CLI and workflow).

To give Print Screen back to Cinnamon (for example before uninstalling), run `catcap --release-hotkeys`.

## Credits

Created by [Andrey Dumchin](https://github.com/kothf), 4X5VA. Built on Qt 6 and PyQt6, python-xlib, xdg-desktop-portal
and SQLite, with development help from [Claude Code](https://claude.com/claude-code) by Anthropic.
The same credits are shown in the app under **About CatCap**.

## Licence

MIT, see [LICENSE](LICENSE).
