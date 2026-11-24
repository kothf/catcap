# CatCap

Screenshot capture, annotation and library app for Linux Mint (Snagit-style, images only, no video).
Works on X11 and Wayland.

- [Functional Requirements Specification](docs/FRS.md)
- [What is implemented so far](docs/STATUS.md)

## Try it from this folder

```bash
./install-deps.sh      # once: installs the GTK/Python packages CatCap needs
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

Global hotkeys (Print Screen and friends) are switched on in the welcome dialog or in
**Settings › Hotkeys**. On Cinnamon they are registered as Cinnamon custom shortcuts, so they
also work in the Wayland session.

## Install as a .deb package

```bash
./packaging/build-deb.sh                          # creates dist/catcap_<version>_all.deb
sudo apt install ./dist/catcap_0.1.0_all.deb      # then start “CatCap” from the Mint menu
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

## Development

```bash
python3 -m unittest discover -s tests -v                 # core tests
dbus-run-session -- python3 -m unittest discover -s tests  # also runs the Wayland portal tests
```

Code layout: `catcap/capture/` (X11 and portal backends), `overlay.py` (region selection),
`editor.py`, `library_window.py`, `library.py` (SQLite), `annotations.py` + `render.py`
(non-destructive editing), `hotkeys.py`, `tray.py`, `app.py` (CLI and workflow).

## Licence

MIT, see [LICENSE](LICENSE).
