"""Wayland path: CatCap's Qt D-Bus portal client against a mock org.freedesktop.portal.Screenshot.

The mock runs in a child process (PyGObject) on the same session bus.
Needs a D-Bus session (run under `dbus-run-session`) and python3-gi; skipped otherwise.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

MOCK = r'''
import sys, tempfile, os
from gi.repository import Gio, GLib
response = int(sys.argv[1]); calls_file = sys.argv[2]; png = sys.argv[3]
XML = """<node><interface name="org.freedesktop.portal.Screenshot"><method name="Screenshot">
<arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
</interface></node>"""
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
def call(conn, sender, path, iface, method, params, inv):
    _p, opts = params.unpack()
    open(calls_file, "a").write(repr(opts.get("interactive")) + "\n")
    handle = "/org/freedesktop/portal/desktop/request/%s/%s" % (sender[1:].replace(".", "_"), opts["handle_token"])
    inv.return_value(GLib.Variant("(o)", (handle,)))
    res = {"uri": GLib.Variant("s", Gio.File.new_for_path(png).get_uri())}
    GLib.timeout_add(80, lambda: (conn.emit_signal(sender, handle, "org.freedesktop.portal.Request", "Response",
                                  GLib.Variant("(ua{sv})", (response, res))), False)[1])
node = Gio.DBusNodeInfo.new_for_xml(XML)
bus.register_object("/org/freedesktop/portal/desktop", node.interfaces[0], call, None, None)
Gio.bus_own_name_on_connection(bus, "org.freedesktop.portal.Desktop", Gio.BusNameOwnerFlags.REPLACE, None, None)
print("ready", flush=True)
GLib.MainLoop().run()
'''


def _have_gi() -> bool:
    import importlib.util

    return importlib.util.find_spec("gi") is not None


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS") and _have_gi(), "needs a D-Bus session and python3-gi")
class PortalTests(unittest.TestCase):
    def run_request(self, response, interactive=False):
        from PyQt6.QtGui import QColor, QImage

        from catcap.capture.portal import PortalBackend

        tmp = Path(tempfile.mkdtemp())
        png = tmp / "shot.png"
        img = QImage(640, 480, QImage.Format.Format_RGB32)
        img.fill(QColor("#112233"))
        img.save(str(png))
        calls = tmp / "calls.txt"
        proc = subprocess.Popen([sys.executable, "-c", MOCK, str(response), str(calls), str(png)],
                                stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            time.sleep(0.2)
            out = {}
            loop = QEventLoop()

            def done(snap, err):
                out["snap"], out["err"] = snap, err
                loop.quit()

            backend = PortalBackend()
            (backend.pick_window if interactive else backend.snapshot)(done)
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            return out, png, calls.read_text().split()
        finally:
            proc.terminate()
            proc.wait()

    def test_screenshot_success_removes_portal_file(self):
        out, png, calls = self.run_request(0)
        self.assertIsNone(out.get("err"))
        snap = out["snap"]
        self.assertEqual((snap.image.width(), snap.image.height()), (640, 480))
        self.assertEqual(snap.backend, "wayland")
        self.assertFalse(png.exists(), "portal output file should be cleaned up")
        self.assertEqual(calls, ["False"])

    def test_interactive_and_cancel(self):
        out, _png, calls = self.run_request(1, interactive=True)
        self.assertIsNone(out["snap"])
        self.assertEqual(out["err"], "cancelled")
        self.assertEqual(calls, ["True"])


if __name__ == "__main__":
    unittest.main()
