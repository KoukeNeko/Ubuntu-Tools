"""store.py 的測試。使用記憶體後端與暫存資料夾，不會動到真實的系統設定。

執行：python3 -m unittest discover tests
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["GSETTINGS_BACKEND"] = "memory"
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="tuneup-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store  # noqa: E402

IF = "org.gnome.desktop.interface"


def change(key, value):
    text = None if value is None else store.to_variant(IF, key, value).print_(False)
    return store.gsettings_change(IF, key, text)


class StoreTest(unittest.TestCase):
    def setUp(self):
        for key in ("clock-show-seconds", "text-scaling-factor", "cursor-size"):
            store.settings(IF).reset(key)
        if store.HISTORY_FILE.exists():
            store.HISTORY_FILE.unlink()
        for path in store.PROFILE_DIR.glob("*.json"):
            path.unlink()

    def get(self, key):
        return store.settings(IF).get_value(key).unpack()

    def test_apply_writes_and_records(self):
        entry = store.apply("tweak", [change("clock-show-seconds", True), change("text-scaling-factor", 1.25)])
        self.assertTrue(self.get("clock-show-seconds"))
        self.assertEqual(self.get("text-scaling-factor"), 1.25)
        self.assertEqual(len(entry["changes"]), 2)
        self.assertEqual(entry["changes"][0]["old"], None)
        self.assertEqual(len(store.load_history()), 1)

    def test_no_op_is_not_recorded(self):
        self.assertIsNone(store.apply("tweak", [change("clock-show-seconds", None)]))
        self.assertEqual(store.load_history(), [])

    def test_undo_restores_default_and_can_be_undone(self):
        first = store.apply("tweak", [change("cursor-size", 48)])
        undone = store.undo(first["id"])
        self.assertIsNone(store.user_text(IF, "cursor-size"))
        store.undo(undone["id"])
        self.assertEqual(self.get("cursor-size"), 48)

    def test_restore_to_reverts_everything_after(self):
        first = store.apply("tweak", [change("cursor-size", 32)])
        store.apply("tweak", [change("cursor-size", 48), change("clock-show-seconds", True)])
        store.apply("tweak", [change("cursor-size", 64)])
        entry = store.restore_to(first["id"])
        self.assertEqual(self.get("cursor-size"), 32)
        self.assertFalse(self.get("clock-show-seconds"))
        self.assertEqual(len(entry["changes"]), 2)

    def test_extension_keys_lists_what_installed_extensions_bring(self):
        schemas = store.EXTENSION_DIRS[0] / "dock@test" / "schemas"
        schemas.mkdir(parents=True)
        (schemas / "dock.gschema.xml").write_text(
            '<schemalist><schema id="org.test.dock" path="/org/test/dock/">'
            '<key name="transparent" type="b"><default>false</default></key></schema></schemalist>')
        subprocess.run(["glib-compile-schemas", str(schemas)], check=True)
        store.forget_absent()
        self.assertIn(("org.test.dock", "transparent"), store.extension_keys())

        keys = [("org.test.dock", "transparent")]
        store.apply("tweak", [store.gsettings_change("org.test.dock", "transparent", "true")])
        profile = store.capture("dock", keys, {})
        store.settings("org.test.dock").reset("transparent")
        store.apply("profile", store.profile_changes(profile, keys, {}, {}))
        self.assertTrue(store.settings("org.test.dock").get_boolean("transparent"))

    def test_failed_apply_still_records_what_it_wrote(self):
        changes = [change("cursor-size", 48), store.extension_change("a@b", "A", False, True)]
        with mock.patch.object(store, "shell_call", side_effect=store.GLib.Error("shell is busy")):
            with self.assertRaises(store.GLib.Error):
                store.apply("profile", changes)
        self.assertEqual(self.get("cursor-size"), 48)
        history = store.load_history()
        self.assertEqual([c["key"] for c in history[0]["changes"]], ["cursor-size"])
        store.undo(history[0]["id"])
        self.assertIsNone(store.user_text(IF, "cursor-size"))

    def test_history_keeps_only_the_latest(self):
        with mock.patch.object(store, "MAX_HISTORY", 2):
            for size in (32, 48, 64):
                store.apply("tweak", [change("cursor-size", size)])
        self.assertEqual([e["changes"][0]["new"] for e in store.load_history()], ["48", "64"])

    def test_imported_profile_keeps_its_extension_settings(self):
        profile = store.capture("搬家", [(IF, "cursor-size")], {})
        profile[store.EXTENSION_SETTINGS] = True
        exported = dict(profile, junk="ignored")
        self.assertTrue(store.valid_profile(exported))
        imported = store.imported_profile(exported)
        self.assertTrue(imported[store.EXTENSION_SETTINGS])
        self.assertNotIn("junk", imported)
        self.assertFalse(store.valid_profile(dict(profile, extension_settings="yes")))

    def test_saving_does_not_replace_a_profile_of_the_same_name(self):
        store.save_profile(store.capture("a/b", [], {"mine@x": True}))
        with self.assertRaises(ValueError):
            store.save_profile(store.capture("a_b", [], {}))
        self.assertEqual(store.load_profiles()[0]["extensions"], {"mine@x": True})
        store.save_profile(store.capture("a/b", [], {}), replace=True)
        self.assertEqual(store.load_profiles()[0]["extensions"], {})

    def test_profile_roundtrip(self):
        keys = [(IF, "cursor-size"), (IF, "clock-show-seconds")]
        store.apply("tweak", [change("cursor-size", 48)])
        profile = store.capture("工作", keys, {})
        store.apply("tweak", [change("cursor-size", None), change("clock-show-seconds", True)])
        changes = store.profile_changes(profile, keys, {}, {})
        self.assertEqual({c["key"] for c in changes}, {"cursor-size", "clock-show-seconds"})
        store.apply("profile", changes, "工作")
        self.assertEqual(self.get("cursor-size"), 48)
        self.assertIsNone(store.user_text(IF, "clock-show-seconds"))
        self.assertEqual(store.profile_changes(profile, keys, {}, {}), [])

    def test_profile_ignores_unknown_keys_and_bad_values(self):
        keys = [(IF, "cursor-size")]
        profile = {"name": "x", "settings": {f"{IF} cursor-size": "'not a number'", "org.evil.schema key": "1"}, "extensions": {}}
        self.assertTrue(store.valid_profile(profile))
        self.assertEqual(store.profile_changes(profile, keys, {}, {}), [])

    def test_invalid_profile_rejected(self):
        self.assertFalse(store.valid_profile({"name": "", "settings": {}}))
        self.assertFalse(store.valid_profile({"name": "x", "settings": {"a": 1}}))
        self.assertFalse(store.valid_profile([]))

    def test_save_and_load_profiles(self):
        store.save_profile(store.capture("遊戲 / test", [], {"a@b": True}))
        self.assertEqual([p["name"] for p in store.load_profiles()], ["遊戲 / test"])

    def test_rename_profile(self):
        store.save_profile(store.capture("a", [], {}))
        store.save_profile(store.capture("b", [], {}))
        with self.assertRaises(ValueError):
            store.rename_profile("a", "b")
        # 暫存資料夾不支援垃圾桶，測試時改成直接刪除
        with mock.patch.object(store, "delete_profile", lambda name: store._profile_path(name).unlink()):
            store.rename_profile("a", "c")
        self.assertEqual(sorted(p["name"] for p in store.load_profiles()), ["b", "c"])

    def test_schema_shipped_inside_an_extension(self):
        ext = Path(tempfile.mkdtemp(prefix="tuneup-ext-")) / "demo@test" / "schemas"
        ext.mkdir(parents=True)
        (ext / "org.tuneup.test.demo.gschema.xml").write_text(
            '<schemalist><schema id="org.tuneup.test.demo" path="/org/tuneup/test/demo/">'
            '<key name="name" type="s"><default>""</default></key></schema></schemalist>')
        subprocess.run(["glib-compile-schemas", str(ext)], check=True)
        self.assertIsNone(store.settings("org.tuneup.test.demo"))
        with mock.patch.object(store, "EXTENSION_DIRS", (ext.parent.parent,)):
            self.assertIsNone(store.settings("org.tuneup.test.demo"))  # 還記得剛才找不到
            store.forget_absent()
            self.assertTrue(store.has_key("org.tuneup.test.demo", "name"))
            store.apply("tweak", [store.gsettings_change("org.tuneup.test.demo", "name", "'Fluent'")])
            self.assertEqual(store.settings("org.tuneup.test.demo").get_string("name"), "Fluent")


if __name__ == "__main__":
    unittest.main()
