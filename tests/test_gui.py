"""Smoke tests for the windows and the capture workflow, run offscreen (no real display needed).

They drive the real CatCap object with a temporary home: editor tools, the library window,
the selection overlay, exports, drag data and command forwarding between instances.
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QFileDialog

APP = QApplication.instance() or QApplication([])
ENV_KEYS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "XDG_PICTURES_DIR",
            "CATCAP_NO_TRAY")


def spin(ms=100):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        APP.processEvents()
        time.sleep(0.005)


def image(w=320, h=200, color="#336699"):
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(color))
    return img


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="catcap-gui-"))
        self._env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS[:4]:
            os.environ[k] = str(self.tmp / k.lower())
        os.environ["XDG_PICTURES_DIR"] = str(self.tmp / "pics")
        os.environ["CATCAP_NO_TRAY"] = "1"
        from catcap.app import CatCap

        self.app = CatCap(APP)
        self.lib = self.app.library

    def tearDown(self):
        for w in QApplication.topLevelWidgets():
            w.close()
        spin(50)
        self.lib.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class IconTests(unittest.TestCase):
    def test_app_icon_renders_without_the_svg_icon_plugin(self):
        from catcap.app import app_icon

        icon = app_icon()
        self.assertFalse(icon.isNull())
        self.assertIn(22, [s.width() for s in icon.availableSizes()])
        pm = icon.pixmap(22, 22)
        self.assertGreater(pm.toImage().pixelColor(11, 11).alpha(), 0)


class EditorTests(AppTestCase):
    def test_every_tool_and_autosave(self):
        from catcap.editor import TOOLS

        cid = self.lib.add_capture(image())
        ed = self.app.open_editor(cid)
        for name, _label, _key in TOOLS:
            ed.set_tool(name)
        ed.set_tool("redact")
        ed._checkpoint()
        ed.doc.items.append(ed._new_item("redact", 10, 10) | {"w": 40, "h": 30})
        ed._commit()
        self.assertEqual(ed.render_output().pixelColor(20, 20).name(), "#000000")
        ed.close()          # flushes the pending autosave
        spin()
        self.assertTrue(self.lib.get(cid).has_annotations)
        self.assertEqual(self.lib.load_doc(cid).items[0]["type"], "redact")

    def test_undo_redo(self):
        cid = self.lib.add_capture(image())
        ed = self.app.open_editor(cid)
        ed._checkpoint()
        ed.doc.items.append(ed._new_item("rect", 5, 5) | {"w": 20, "h": 20})
        ed._commit()
        ed.undo()
        self.assertEqual(ed.doc.items, [])
        ed.redo()
        self.assertEqual(len(ed.doc.items), 1)


class LibraryWindowTests(AppTestCase):
    def test_rebuild_asks_before_forgetting_missing_captures(self):
        from catcap import ui

        cid = self.lib.add_capture(image())
        self.app.show_library()
        spin()
        self.lib.original_path(cid).unlink()
        with mock.patch.object(ui, "ask", return_value=False) as ask:
            self.app.library_window.rebuild()
        self.assertTrue(ask.called)
        self.assertTrue(self.lib.exists(cid))
        with mock.patch.object(ui, "ask", return_value=True):
            self.app.library_window.rebuild()
        self.assertFalse(self.lib.exists(cid))

    def test_preview_appears_after_selection(self):
        cid = self.lib.add_capture(image(2400, 1600))
        self.app.show_library()
        spin(150)
        self.app.library_window.select_capture(cid)
        spin(300)
        self.assertFalse(self.app.library_window.preview.pixmap().isNull())

    def test_batch_and_pdf_export(self):
        from catcap import ui

        ids = [self.lib.add_capture(image(color=c), app="Same") for c in ("#ff0000", "#00ff00", "#0000ff")]
        out = self.tmp / "out"
        out.mkdir()
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=str(out)):
            ui.export_dialog(None, self.lib, self.app.config, ids)
        self.assertEqual(len(list(out.glob("*.png"))), 3)
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(out / "all.pdf"), "")):
            ui.export_pdf_dialog(None, self.lib, self.app.config, ids)
        self.assertTrue((out / "all.pdf").exists())

    def test_drag_data_is_rendered_only_on_request(self):
        from catcap import ui

        ids = [self.lib.add_capture(image()) for _ in range(3)]
        mime = ui.drag_mime(self.lib, ids, self.app.config)
        self.assertIn("text/uri-list", mime.formats())
        self.assertTrue(mime.hasFormat(ui.IDS_MIME))
        self.assertIsNone(mime._urls)
        uris = [u for u in bytes(mime.retrieveData("text/uri-list", None)).decode().split("\r\n") if u]
        self.assertEqual(len(uris), 3)


class CaptureFlowTests(AppTestCase):
    def test_overlay_selection_stores_capture(self):
        from catcap.capture import Snapshot
        from catcap.overlay import Overlay

        snap = Snapshot(image=image(800, 600), scale=1.0, backend="wayland", monitors=[("A", 0, 0, 800, 600)])
        self.app._capturing = True
        self.app._open_overlay(snap, "region", "library")
        overlays = [k for k in self.app._keep if isinstance(k, Overlay)]
        self.assertGreaterEqual(len(overlays), 1)
        overlays[0]._finish((10, 20, 100, 50))
        spin(200)
        self.assertEqual(self.lib.count(), 1)
        from catcap.library import Query

        cap = self.lib.query(Query())[0]
        self.assertEqual((cap.width, cap.height), (100, 50))
        self.assertEqual(self.app.config.get("state", "last_region"), "10,20,100,50")
        self.assertFalse(self.app._capturing)

    def test_overlay_cancel_ends_capture(self):
        from catcap.capture import Snapshot
        from catcap.overlay import Overlay

        snap = Snapshot(image=image(), scale=1.0, backend="wayland", monitors=[("A", 0, 0, 320, 200)])
        self.app._capturing = True
        self.app._open_overlay(snap, "region", None)
        overlay = next(k for k in self.app._keep if isinstance(k, Overlay))
        overlay._cancel()
        spin(100)
        self.assertFalse(self.app._capturing)
        self.assertEqual(self.lib.count(), 0)


class ForwardTests(unittest.TestCase):
    def test_cli_forwards_arguments_to_running_instance(self):
        from catcap import cli

        tmp = Path(tempfile.mkdtemp(prefix="catcap-sock-"))
        path = str(tmp / "instance.sock")
        received = {}
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)

        def serve():
            conn, _ = server.accept()
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                received.update(json.loads(data))
                conn.sendall(b"ok\n")

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            with mock.patch.object(cli, "socket_path", return_value=path):
                self.assertTrue(cli.forward(cli.parse_args(["--region", "--delay", "3", "--clipboard"])))
            thread.join(2)
            self.assertTrue(received["args"]["region"])
            self.assertEqual(received["args"]["delay"], 3)
            self.assertTrue(received["args"]["clipboard"])
        finally:
            server.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_forward_without_instance_returns_false(self):
        from catcap import cli

        with mock.patch.object(cli, "socket_path", return_value="/nonexistent/catcap.sock"):
            self.assertFalse(cli.forward(cli.parse_args(["--full"])))


if __name__ == "__main__":
    unittest.main()
