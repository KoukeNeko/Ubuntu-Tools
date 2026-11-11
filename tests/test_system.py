"""system.py 解析函式的測試；不執行任何會改動系統的指令。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import system  # noqa: E402


class SystemTest(unittest.TestCase):
    def test_journal_usage(self):
        self.assertEqual(system.parse_journal_usage("Archived and active journals take up 24M in the file system."), 24 * 1024**2)
        self.assertEqual(system.parse_journal_usage("Archived and active journals take up 1.5G in the file system."), int(1.5 * 1024**3))
        self.assertEqual(system.parse_journal_usage("unexpected output"), 0)

    def test_autoremove(self):
        out = "Reading package lists...\nRemv linux-image-6.8.0-40-generic [6.8.0-40.40]\nRemv libfoo1 [1.2]\n"
        self.assertEqual(system.parse_autoremove(out), ["linux-image-6.8.0-40-generic", "libfoo1"])
        self.assertEqual(system.parse_autoremove("0 upgraded, 0 newly installed"), [])

    def test_upower(self):
        info = system.parse_upower("  native-path:          BAT0\n    energy-full:         45.2 Wh\n    energy-full-design:  57 Wh\n    charge-cycles:       312\n")
        self.assertEqual(info["energy-full"], "45.2 Wh")
        self.assertEqual(info["charge-cycles"], "312")

    def test_human(self):
        self.assertEqual(system.human(0), "0 B")
        self.assertEqual(system.human(1536), "2 KB")
        self.assertEqual(system.human(60685004), "57.9 MB")

    def test_missing_command(self):
        self.assertEqual(system.run(["tuneup-no-such-command"]), (-1, ""))


if __name__ == "__main__":
    unittest.main()
