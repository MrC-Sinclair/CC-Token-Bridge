import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_app
from desktop_presets import ADAPTERS, can_sync, desktop_sources


class DesktopSourcesTest(unittest.TestCase):
    def test_upgrade_keeps_user_settings_and_removals(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory) / "config.json"
            # A user already added Marvis and removed the former CodeBuddy preset.
            custom = dict(id="user-script", name="我的 Marvis", app_type="Marvis", kind="custom",
                          script="custom.py", root=directory)
            old = dict(db_path="my-database.db", auto_sync=True, sources=[custom])
            bridge_app.save_config(old, location)
            with patch.object(bridge_app, "config_path", return_value=location):
                config = bridge_app.load_config()
                self.assertEqual(len(config["sources"]), len(desktop_sources()))
                self.assertEqual(config["sources"][0], custom)
                self.assertEqual(config["db_path"], old["db_path"])
                self.assertIs(config["auto_sync"], True)
                self.assertEqual(config, json.loads(location.read_text(encoding="utf-8")))
                config["sources"].pop()
                bridge_app.save_config(config)
                self.assertEqual(bridge_app.load_config(), config)
            bridge_app.save_config(old, location)
            self.assertEqual(bridge_app.load_config(location), old)

    def test_config_drops_unavailable_presets(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory) / "config.json"
            old = bridge_app.default_config()
            old["desktop_presets_version"] = 1
            old["sources"].append(dict(id="zcode", preset="zcode", kind="desktop", name="ZCode",
                                       app_type="ZCode", root=str(Path(directory) / ".zcode"),
                                       script=""))
            location.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
            with patch.object(bridge_app, "config_path", return_value=location):
                config = bridge_app.load_config()
            self.assertFalse(any(s.get("preset") == "zcode" for s in config["sources"]))
            self.assertEqual(len(config["sources"]), 1 + len(desktop_sources()))
            self.assertEqual(config["desktop_presets_version"], bridge_app.PRESETS_VERSION)
            self.assertEqual(json.loads(location.read_text(encoding="utf-8")), config)
            self.assertFalse(any(s.get("preset") == "zcode"
                                 for s in bridge_app.load_config(location)["sources"]))

    def test_pending_sources_activate_with_an_explicit_scanner(self):
        for source in desktop_sources():
            self.assertEqual(can_sync(source), source["preset"] in ADAPTERS)
            custom = copy.copy(source)
            custom["script"] = "usage.py"
            self.assertTrue(can_sync(custom))
