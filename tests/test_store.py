"""store.py 的測試。使用記憶體後端與暫存資料夾，不會動到真實的系統設定。

執行：python3 -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
