"""Cinnamon hotkey registration against the real Cinnamon schemas, using a throw-away keyfile backend.

Needs the `gsettings` tool and the cinnamon-desktop-data schemas; skipped otherwise.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _cinnamon_schemas() -> bool:
    if not shutil.which("gsettings"):
        return False
    out = subprocess.run(["gsettings", "list-schemas"], capture_output=True, text=True).stdout.split()
    return "org.cinnamon.desktop.keybindings" in out


@unittest.skipUnless(_cinnamon_schemas(), "needs gsettings and cinnamon-desktop-data")
class CinnamonHotkeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="catcap-hk-"))
        self.env = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "GSETTINGS_BACKEND", "XDG_CURRENT_DESKTOP")}
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp)
        os.environ["GSETTINGS_BACKEND"] = "keyfile"
        os.environ["XDG_CURRENT_DESKTOP"] = "X-Cinnamon"
        from catcap import hotkeys

        hotkeys._schemas_cache = None
        self.hk = hotkeys

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_take_over_and_restore_print(self):
        from catcap.config import Config

        cfg = Config(self.tmp / "catcap.toml")
        cfg.set("hotkeys", "enabled", True)
        mgr = self.hk.HotkeyManager(cfg)
        media = self.hk.GSettings(self.hk.MEDIA_SCHEMA)
        before = media.strv("screenshot")
        self.assertIn("Print", before)
        self.assertEqual(mgr.apply(), [])
        self.assertEqual(media.strv("screenshot"), [])
        kb = self.hk.GSettings(self.hk.KB_SCHEMA)
        self.assertIn("catcap-region", kb.strv("custom-list"))
        entry = self.hk.GSettings(self.hk.CUSTOM_SCHEMA, self.hk.CUSTOM_PATH.format("catcap-region"))
        self.assertEqual(entry.strv("binding"), ["Print"])
        self.assertTrue(entry.get("command").endswith("--region"))
        self.assertIn("Cinnamon “terminal”", mgr.conflicts("<Primary><Alt>t", "region"))
        cfg.set("hotkeys", "enabled", False)
        mgr.apply()
        self.assertEqual(media.strv("screenshot"), before)
        self.assertNotIn("catcap-region", kb.strv("custom-list"))


if __name__ == "__main__":
    unittest.main()
