"""Wayland path: talk to a mock org.freedesktop.portal.Screenshot on a private session bus.

Needs a D-Bus session (run under `dbus-run-session`); skipped otherwise.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gi  # noqa: E402

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

XML = """
<node>
  <interface name="org.freedesktop.portal.Screenshot">
    <method name="Screenshot">
      <arg type="s" name="parent_window" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="o" name="handle" direction="out"/>
    </method>
  </interface>
</node>
"""


class MockPortal:
    def __init__(self, bus, response=0):
        self.bus = bus
        self.response = response
        self.calls = []
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.reg = bus.register_object("/org/freedesktop/portal/desktop", node.interfaces[0], self._call, None, None)

    def _call(self, conn, sender, path, iface, method, params, invocation):
        _parent, options = params.unpack()
        self.calls.append(options)
        token = options["handle_token"]
        handle = f"/org/freedesktop/portal/desktop/request/{sender[1:].replace('.', '_')}/{token}"
        invocation.return_value(GLib.Variant("(o)", (handle,)))
        fd, png = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 640, 480)
        pb.fill(0x112233FF)
        pb.savev(png, "png", [], [])
        self.png = png
        results = {"uri": GLib.Variant("s", Gio.File.new_for_path(png).get_uri())}
        GLib.timeout_add(50, lambda: (conn.emit_signal(sender, handle, "org.freedesktop.portal.Request", "Response",
                                                        GLib.Variant("(ua{sv})", (self.response, results))), False)[1])


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "needs a D-Bus session")
class PortalTests(unittest.TestCase):
    def run_request(self, response, interactive=False):
        from catcap.capture.portal import PortalBackend

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owner = Gio.bus_own_name_on_connection(bus, "org.freedesktop.portal.Desktop",
                                               Gio.BusNameOwnerFlags.REPLACE, None, None)
        mock = MockPortal(bus, response)
        loop = GLib.MainLoop()
        out = {}

        def done(snap, err):
            out["snap"], out["err"] = snap, err
            loop.quit()

        backend = PortalBackend()
        (backend.pick_window if interactive else backend.snapshot)(done)
        GLib.timeout_add(5000, loop.quit)
        loop.run()
        bus.unregister_object(mock.reg)
        Gio.bus_unown_name(owner)
        return out, mock

    def test_screenshot_success_removes_portal_file(self):
        out, mock = self.run_request(0)
        self.assertIsNone(out.get("err"))
        snap = out["snap"]
        self.assertEqual((snap.pixbuf.get_width(), snap.pixbuf.get_height()), (640, 480))
        self.assertEqual(snap.backend, "wayland")
        self.assertFalse(Path(mock.png).exists(), "portal output file should be cleaned up")
        self.assertFalse(mock.calls[0]["interactive"])

    def test_interactive_and_cancel(self):
        out, mock = self.run_request(1, interactive=True)
        self.assertIsNone(out["snap"])
        self.assertEqual(out["err"], "cancelled")
        self.assertTrue(mock.calls[0]["interactive"])


if __name__ == "__main__":
    unittest.main()
