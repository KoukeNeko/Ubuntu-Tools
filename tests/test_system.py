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

    def test_snap_list(self):
        text = ("Name      Version    Rev    Tracking         Publisher    Notes\n"
                "core24    20260824   2124   latest/stable    canonical**  base\n"
                "firefox   157.0.1-1  9036   latest/stable/…  mozilla**    -\n")
        snaps = system.parse_snap_list(text)
        self.assertEqual([(s["name"], s["system"], s["publisher"]) for s in snaps], [("core24", True, "canonical"), ("firefox", False, "mozilla")])

    def test_dpkg(self):
        pkgs = system.parse_dpkg("bash\t2020\t5.3-3ubuntu1\tGNU Bourne Again SHell\nbroken line\n")
        self.assertEqual(pkgs, [{"name": "bash", "size": 2020 * 1024, "version": "5.3-3ubuntu1", "summary": "GNU Bourne Again SHell"}])

    def test_upgradable(self):
        text = "Listing...\ngdm3/stonking-updates 51.0-0ubuntu1.1 amd64 [upgradable from: 51.0-0ubuntu1]\n"
        self.assertEqual(system.parse_upgradable(text), [{"name": "gdm3", "new": "51.0-0ubuntu1.1", "old": "51.0-0ubuntu1"}])
        self.assertEqual(system.parse_upgradable("Listing...\n"), [])

    def test_protected_packages(self):
        self.assertTrue(any(n.startswith(system.PROTECTED_PREFIXES) for n in ["foo", "gnome-shell-common"]))
        self.assertFalse(any(n.startswith(system.PROTECTED_PREFIXES) for n in ["vlc", "libvlc5"]))

    def test_journal_merges_repeats(self):
        lines = "\n".join([
            '{"__REALTIME_TIMESTAMP": "1000000", "SYSLOG_IDENTIFIER": "systemd", "MESSAGE": "Failed to start x"}',
            '{"__REALTIME_TIMESTAMP": "3000000", "SYSLOG_IDENTIFIER": "systemd", "MESSAGE": "Failed to start x"}',
            '{"__REALTIME_TIMESTAMP": "2000000", "_COMM": "foo", "MESSAGE": "boom"}',
            '{"__REALTIME_TIMESTAMP": "9000000", "MESSAGE": [1, 2, 3]}', "not json"])
        items = system.parse_journal(lines)
        self.assertEqual([(i["source"], i["count"], i["time"]) for i in items], [("systemd", 2, 3), ("foo", 1, 2)])

    def test_missing_command(self):
        self.assertEqual(system.run(["tuneup-no-such-command"]), (-1, ""))


if __name__ == "__main__":
    unittest.main()
