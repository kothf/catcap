"""Core tests (no display needed): config, library, annotations, rendering, filenames, hotkeys.

Run with:  python3 -m unittest discover -s tests -v
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])


class TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="catcap-test-"))
        self._env = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                                                    "XDG_RUNTIME_DIR")}
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "cfg")
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "cache")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pixbuf(self, w=400, h=300, color="#3366CC"):
        image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(color))
        return image


class ConfigTests(TempHome):
    def test_roundtrip_and_defaults(self):
        from catcap import config

        cfg = config.Config()
        self.assertEqual(cfg.get("capture", "after_action"), "toolbar")
        cfg.set("editor", "color", 'quote " and \\ backslash')
        cfg.set("capture", "delay", 5)
        cfg.set("general", "sound", True)
        again = config.Config()
        self.assertEqual(again.get("editor", "color"), 'quote " and \\ backslash')
        self.assertEqual(again.get("capture", "delay"), 5)
        self.assertIs(again.get("general", "sound"), True)
        self.assertEqual(oct(cfg.path.stat().st_mode & 0o777), "0o600")

    def test_wrong_types_fall_back(self):
        from catcap import config

        path = Path(os.environ["XDG_CONFIG_HOME"]) / "catcap" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('[capture]\ndelay = "soon"\nmagnifier = false\n# comment\n')
        cfg = config.Config()
        self.assertEqual(cfg.get("capture", "delay"), 0)
        self.assertIs(cfg.get("capture", "magnifier"), False)


class LibraryTests(TempHome):
    def make(self):
        from catcap.library import Library

        return Library(self.tmp / "pics")

    def test_add_query_search_tags(self):
        from catcap.library import Query

        lib = self.make()
        a = lib.add_capture(self.pixbuf(), mode="region", app="firefox", title="Invoice 42")
        b = lib.add_capture(self.pixbuf(200, 100), mode="window", app="nemo", title="Home")
        lib.set_tags(a, ["Docs", " bug ", "docs"])
        self.assertEqual(lib.get(a).tags, ["bug", "docs"])
        self.assertEqual([c.id for c in lib.query(Query(search="invoice"))], [a])
        self.assertEqual([c.id for c in lib.query(Query(search="docs"))], [a])
        self.assertEqual([c.id for c in lib.query(Query(view="tag", tag="bug"))], [a])
        self.assertEqual([c.id for c in lib.query(Query(app="nemo"))], [b])
        self.assertEqual([c.id for c in lib.query(Query(sort="date_asc"))], [a, b])
        self.assertEqual(lib.apps(), ["firefox", "nemo"])
        self.assertTrue(lib.original_path(a).exists())
        self.assertEqual(oct(lib.original_path(a).stat().st_mode & 0o777), "0o600")

    def test_collections_favourites_trash(self):
        from catcap.library import Query

        lib = self.make()
        a = lib.add_capture(self.pixbuf())
        k = lib.create_collection("Work")
        lib.add_to_collection([a], k)
        self.assertEqual(lib.collections(), [(k, "Work", 1)])
        self.assertEqual(len(lib.query(Query(view="collection", collection_id=k))), 1)
        lib.set_favourite([a], True)
        self.assertEqual(len(lib.query(Query(view="favourites"))), 1)
        lib.trash([a])
        self.assertEqual(len(lib.query(Query())), 0)
        self.assertEqual(len(lib.query(Query(view="trash"))), 1)
        lib.restore([a])
        self.assertEqual(len(lib.query(Query())), 1)
        lib.trash([a])
        lib.db.execute("UPDATE captures SET trashed=?", (time.time() - 40 * 86400,))
        self.assertEqual(lib.purge_trash(30), 1)
        self.assertFalse(lib.exists(a))

    def test_annotations_saved_beside_original_and_rebuild(self):
        from catcap.annotations import Document

        lib = self.make()
        a = lib.add_capture(self.pixbuf())
        doc = Document(items=[{"type": "rect", "x": 1, "y": 2, "w": 30, "h": 40}], crop=[0, 0, 100, 50])
        lib.save_doc(a, doc)
        self.assertTrue(lib.doc_path(a).exists())
        cap = lib.get(a)
        self.assertTrue(cap.has_annotations)
        self.assertEqual((cap.width, cap.height), (100, 50))
        self.assertEqual(lib.load_doc(a).items, doc.items)
        lib.save_doc(a, Document())
        self.assertFalse(lib.doc_path(a).exists())
        # rebuild re-adds files that are missing from the database
        lib.db.execute("DELETE FROM captures")
        lib.db.commit()
        self.assertEqual(lib.rebuild(), (1, []))

    def test_search_treats_percent_and_underscore_literally(self):
        from catcap.library import Query

        lib = self.make()
        a = lib.add_capture(self.pixbuf(), name="50% off")
        lib.add_capture(self.pixbuf(), name="500 items")
        b = lib.add_capture(self.pixbuf(), name="snake_case")
        lib.add_capture(self.pixbuf(), name="snakeXcase")
        self.assertEqual([c.id for c in lib.query(Query(search="50%"))], [a])
        self.assertEqual([c.id for c in lib.query(Query(search="e_c"))], [b])

    def test_schema_migrations_run_and_newer_db_is_refused(self):
        from catcap import library as libmod

        lib = self.make()
        self.assertEqual(lib.schema_version(), libmod.SCHEMA_VERSION)
        lib.close()
        old = (libmod.SCHEMA_VERSION, dict(libmod.MIGRATIONS))
        try:
            libmod.SCHEMA_VERSION = old[0] + 1
            libmod.MIGRATIONS[old[0]] = ["ALTER TABLE captures ADD COLUMN extra TEXT NOT NULL DEFAULT ''"]
            lib = self.make()
            self.assertEqual(lib.schema_version(), old[0] + 1)
            cols = [r[1] for r in lib.db.execute("PRAGMA table_info(captures)")]
            self.assertIn("extra", cols)
            lib.close()
            libmod.SCHEMA_VERSION, libmod.MIGRATIONS = old[0], dict(old[1])
            with self.assertRaises(libmod.NewerLibraryError):
                self.make()
        finally:
            libmod.SCHEMA_VERSION, libmod.MIGRATIONS = old[0], dict(old[1])

    def test_rebuild_keeps_captures_whose_files_are_missing(self):
        lib = self.make()
        a = lib.add_capture(self.pixbuf())
        lib.set_tags(a, ["keep me"])
        lib.original_path(a).unlink()
        added, missing = lib.rebuild()
        self.assertEqual((added, missing), (0, [a]))
        self.assertTrue(lib.exists(a))
        self.assertEqual(lib.get_tags(a), ["keep me"])
        lib.forget(missing)
        self.assertFalse(lib.exists(a))

    def test_rebuild_refuses_when_folder_is_gone(self):
        lib = self.make()
        a = lib.add_capture(self.pixbuf())
        shutil.rmtree(lib.images_dir)
        with self.assertRaises(FileNotFoundError):
            lib.rebuild()
        self.assertTrue(lib.exists(a))

    def test_move_images_merges_into_existing_folders(self):
        lib = self.make()
        a = lib.add_capture(self.pixbuf())
        rel = lib.get(a).path
        target = self.tmp / "elsewhere"
        year = rel.split("/")[0]
        (target / year).mkdir(parents=True)
        (target / year / "other.txt").write_text("x")
        lib.move_images(target)
        self.assertEqual(lib.images_dir, target.resolve())
        self.assertTrue((target / rel).exists())
        self.assertFalse((target / year / year).exists())
        self.assertTrue((target / year / "other.txt").exists())

    def test_move_images_refuses_clashes_and_nested_targets(self):
        lib = self.make()
        a = lib.add_capture(self.pixbuf())
        rel = lib.get(a).path
        old = lib.images_dir
        with self.assertRaises(ValueError):
            lib.move_images(old / "sub")
        target = self.tmp / "clash"
        (target / rel).parent.mkdir(parents=True)
        (target / rel).write_text("not mine")
        with self.assertRaises(FileExistsError):
            lib.move_images(target)
        self.assertEqual(lib.images_dir, old)
        self.assertTrue((old / rel).exists())


class AnnotationTests(unittest.TestCase):
    def test_rotation_and_flip_keep_items_on_image(self):
        from catcap.annotations import Document

        doc = Document(items=[{"type": "arrow", "x1": 10, "y1": 20, "x2": 30, "y2": 40}])
        doc.rotate_cw(400, 300)
        self.assertEqual(doc.transformed_size(400, 300), (300, 400))
        self.assertEqual((doc.items[0]["x1"], doc.items[0]["y1"]), (280, 10))
        for _ in range(3):
            doc.rotate_cw(400, 300)
        self.assertEqual(doc.rotate, 0)
        self.assertEqual((doc.items[0]["x1"], doc.items[0]["y1"]), (10, 20))
        doc.flip_horizontal(400, 300)
        doc.flip_horizontal(400, 300)
        self.assertEqual((doc.rotate, doc.flip), (0, False))
        self.assertEqual((doc.items[0]["x1"], doc.items[0]["y1"]), (10, 20))

    def test_scale_and_steps(self):
        from catcap.annotations import Document, step_label

        doc = Document(items=[{"type": "step", "x": 10, "y": 10, "n": 1, "size": 30},
                              {"type": "step", "x": 50, "y": 10, "n": 2, "size": 30}])
        doc.set_scale(2.0)
        self.assertEqual(doc.items[1]["x"], 100)
        self.assertEqual(doc.items[1]["size"], 60)
        del doc.items[0]
        doc.renumber_steps()
        self.assertEqual(doc.items[0]["n"], 1)
        self.assertEqual(step_label(28, "letter"), "AB")

    def test_json_roundtrip_ignores_unknown_items(self):
        from catcap.annotations import Document

        doc = Document.from_json('{"version":1,"items":[{"type":"arrow","x1":0,"y1":0,"x2":1,"y2":1},'
                                 '{"type":"bogus"}]}')
        self.assertEqual(len(doc.items), 1)
        self.assertEqual(Document.from_json(doc.to_json()).items, doc.items)


class RenderTests(TempHome):
    def test_blur_hides_pixels_and_exports(self):
        from catcap import render
        from catcap.annotations import Document

        src = self.pixbuf(200, 100, "#FFFFFF")
        p = QPainter(src)
        for x in range(0, 100, 4):
            p.fillRect(x, 0, 2, 100, QColor("black"))
        p.end()
        doc = Document(items=[{"type": "pixelate", "x": 0, "y": 0, "w": 100, "h": 100, "strength": 20},
                              {"type": "text", "x": 120, "y": 10, "text": "Hi"}])
        out = render.render(src, doc)
        stripes = {QColor(out.pixel(x, 50)).lightness() for x in range(0, 100)}
        self.assertLess(max(stripes) - min(stripes), 60, "pixelated stripes should be flattened")
        for fmt in ("png", "jpg", "webp", "bmp", "pdf"):
            path = render.save_image(out, self.tmp / f"x.{fmt}")
            self.assertGreater(path.stat().st_size, 100)

    def test_crop_output_size(self):
        from catcap import render
        from catcap.annotations import Document

        src = self.pixbuf(400, 300)
        doc = Document(crop=[10, 20, 100, 50])
        doc.rotate_cw(400, 300)
        out = render.render(src, doc)
        self.assertEqual((out.width(), out.height()), (50, 100))

    def test_temp_renders_with_the_same_name_do_not_collide(self):
        from catcap import ui
        from catcap.config import Config
        from catcap.library import Library

        os.environ["XDG_RUNTIME_DIR"] = str(self.tmp / "run")
        lib = Library(self.tmp / "pics")
        config = Config()
        config.set("output", "filename_template", "{app}")
        a = lib.add_capture(self.pixbuf(color="#ff0000"), app="Same")
        b = lib.add_capture(self.pixbuf(color="#0000ff"), app="Same")
        pa, pb = ui.temp_render(lib, a, config), ui.temp_render(lib, b, config)
        self.assertNotEqual(pa, pb)
        self.assertEqual((pa.name, pb.name), ("Same.png", "Same.png"))
        self.assertEqual(QImage(str(pa)).pixelColor(5, 5).name(), "#ff0000")
        self.assertEqual(QImage(str(pb)).pixelColor(5, 5).name(), "#0000ff")
        ui.cleanup_temp(max_age=-1)
        self.assertFalse(pa.exists())

    def test_redact_covers_area_with_opaque_colour(self):
        from catcap import render
        from catcap.annotations import Document

        src = self.pixbuf(100, 80, "#3366CC")
        doc = Document(items=[{"type": "redact", "x": 10, "y": 10, "w": 30, "h": 20,
                               "color": "#000000", "opacity": 0.2}])
        out = render.render(src, doc)
        self.assertEqual(out.pixelColor(20, 20).name(), "#000000")
        self.assertEqual(out.pixelColor(20, 20).alpha(), 255)
        self.assertEqual(out.pixelColor(60, 60).name(), "#3366cc")


class FilenameAndHotkeyTests(unittest.TestCase):
    def test_filename_template(self):
        from catcap.library import Capture
        from catcap.ui import format_filename

        cap = Capture(id="abcdef1234", name="n", created=time.mktime((2026, 3, 4, 5, 6, 7, 0, 0, -1)),
                      modified=0, mode="region", width=10, height=20, size=0, app="fire/fox", title="T",
                      monitor="", favourite=False, notes="", ocr="", has_annotations=False, trashed=None, path="")
        self.assertEqual(format_filename("{app}_{yyyy-MM-dd}_{HHmmss}", cap), "fire_fox_2026-03-04_050607")
        self.assertEqual(format_filename("{width}x{height}-{id}", cap), "10x20-abcdef12")

    def test_accelerator_normalisation(self):
        from catcap.hotkeys import _norm, gtk_to_qt, qt_to_gtk

        self.assertEqual(_norm("<Primary><Alt>t"), _norm("<Control><Alt>T"))
        self.assertNotEqual(_norm("<Alt>Print"), _norm("<Control><Alt>Print"))
        self.assertIsNone(_norm(""))
        self.assertEqual(qt_to_gtk("Ctrl+Alt+Print"), "<Primary><Alt>Print")
        self.assertEqual(gtk_to_qt("<Primary><Shift>Print"), "Ctrl+Shift+Print")
        self.assertEqual(_norm(qt_to_gtk(gtk_to_qt("<Shift>Print"))), _norm("<Shift>Print"))


if __name__ == "__main__":
    unittest.main()
