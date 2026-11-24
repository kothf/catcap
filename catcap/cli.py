"""Command line entry point.

Kept free of Qt imports: when CatCap is already running in the tray, a hotkey such as
`catcap --region` only parses its arguments and hands them to the running instance over a
Unix socket, so the capture starts at once instead of after a Qt start-up.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys

from . import APP_NAME, __version__, paths


def parse_args(argv):
    p = argparse.ArgumentParser(prog="catcap", description="Capture, annotate and organise screenshots.")
    g = p.add_argument_group("capture")
    g.add_argument("--region", action="store_true", help="capture a region or window")
    g.add_argument("--window", action="store_true", help="capture a window")
    g.add_argument("--full", action="store_true", help="capture the whole screen (all monitors)")
    g.add_argument("--monitor", action="store_true", help="capture the monitor under the pointer")
    g.add_argument("--active", action="store_true", help="capture the active window")
    g.add_argument("--last-region", dest="last_region", action="store_true", help="capture the last region again")
    g.add_argument("-d", "--delay", type=int, help="wait N seconds before capturing")
    g.add_argument("--cursor", dest="cursor", action="store_true", default=None, help="include the mouse pointer")
    g.add_argument("--no-cursor", dest="cursor", action="store_false", help="leave the mouse pointer out")
    a = p.add_argument_group("after capture")
    a.add_argument("--clipboard", action="store_true", help="copy to the clipboard")
    a.add_argument("--edit", action="store_true", help="open in the editor")
    a.add_argument("--save-only", dest="save_only", action="store_true", help="only save to the library")
    o = p.add_argument_group("other")
    o.add_argument("--library", action="store_true", help="open the library")
    o.add_argument("--settings", action="store_true", help="open settings")
    o.add_argument("--background", action="store_true", help="start in the tray without opening a window")
    o.add_argument("--release-hotkeys", dest="release_hotkeys", action="store_true",
                   help="remove CatCap's shortcuts and give Print Screen back to Cinnamon, then exit")
    o.add_argument("--quit", action="store_true", help="quit the running CatCap")
    o.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    p.add_argument("files", nargs="*", help="image files to open in the editor")
    return p.parse_args(argv)


def socket_path() -> str:
    return str(paths.runtime_dir() / "instance.sock")


def forward(args: argparse.Namespace) -> bool:
    """Send the arguments to a running CatCap. True when one took them."""
    path = socket_path()
    if not os.path.exists(path):
        return False
    payload = json.dumps({"args": vars(args), "cwd": os.getcwd()}).encode() + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(path)
            s.sendall(payload)
            s.shutdown(socket.SHUT_WR)
            return s.recv(16).startswith(b"ok")
    except OSError:
        return False


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if forward(args):
        return 0
    if args.quit:
        return 0
    try:
        from .app import run
    except ImportError as exc:
        sys.stderr.write(f"CatCap cannot start: {exc}\nInstall python3-pyqt6, python3-pyqt6.qtsvg and "
                         "python3-xlib (or run install-deps.sh from the source folder).\n")
        return 1
    return run(args)
