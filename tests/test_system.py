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

    def test_apt_search_ranks_exact_then_prefix(self):
        text = "libvlc-bin - tools\nvlc-data - data files\nvlc - multimedia player\n"
        self.assertEqual([p["name"] for p in system.parse_apt_search(text, "vlc")], ["vlc", "vlc-data", "libvlc-bin"])

    def test_snap_find(self):
        text = ("Name   Version  Publisher   Notes  Summary\n"
                "vlc    3.0.20   videolan**  -      The ultimate media player\n")
        self.assertEqual(system.parse_snap_find(text), [{"name": "vlc", "version": "3.0.20", "publisher": "videolan", "summary": "The ultimate media player"}])
        self.assertEqual(system.parse_snap_find("error: unable to contact snap store\n"), [])

    def test_search_ignores_empty_and_option_like_queries(self):
        self.assertEqual(system.search_apps("  "), {"apt": [], "snap": []})
        self.assertEqual(system.search_apps("--"), {"apt": [], "snap": []})

    def test_apt_history(self):
        text = ("\nStart-Date: 2026-11-12  00:08:31\nCommandline: apt-get install -y timeshift\n"
                "Install: libxapp1:amd64 (3.2.2-2, automatic), timeshift:amd64 (24.06)\nEnd-Date: 2026-11-12  00:08:34\n"
                "\nStart-Date: 2026-11-12  00:09:00\nCommandline: apt-get upgrade\nUpgrade: gdm3:amd64 (51.0, 51.1)\nRemove: foo:amd64 (1)\n")
        entries = system.parse_apt_history(text)
        self.assertEqual([(e["time"], e["install"], e["upgrade"], e["remove"]) for e in entries],
                         [("2026-11-12 00:09:00", 0, 1, 1), ("2026-11-12 00:08:31", 2, 0, 0)])

    def test_xkb_options(self):
        text = ("! layout\n  us  English (US)\n\n! option\n  grp  Switching to another layout\n  grp:switch  Right Alt (while pressed)\n"
                "  empty  A group without options\n  caps  Caps Lock behavior\n  caps:escape  Make Caps Lock an additional Esc\n")
        self.assertEqual(system.parse_xkb_options(text), [
            ("Switching to another layout", [("grp:switch", "Right Alt (while pressed)")]),
            ("Caps Lock behavior", [("caps:escape", "Make Caps Lock an additional Esc")])])
        self.assertEqual(system.parse_xkb_options("! layout\n  us  English\n"), [])

    def test_deb822_sources(self):
        text = "Types: deb\nURIs: http://a/ubuntu/\nSuites: x x-updates\n\nTypes: deb\nURIs: http://b/\nSuites: y\nEnabled: no\n"
        self.assertEqual(system.parse_deb822(text), [{"uri": "http://a/ubuntu/", "suites": "x x-updates", "enabled": True},
                                                      {"uri": "http://b/", "suites": "y", "enabled": False}])

    def test_sources_list(self):
        text = "# comment\ndeb [arch=amd64 signed-by=/k.gpg] https://repo/ stable main\n# deb http://old/ focal main\n"
        self.assertEqual(system.parse_sources_list(text), [{"uri": "https://repo/", "suites": "stable", "enabled": True},
                                                            {"uri": "http://old/", "suites": "focal", "enabled": False}])

    def test_missing_command(self):
        self.assertEqual(system.run(["tuneup-no-such-command"]), (-1, ""))


if __name__ == "__main__":
    unittest.main()
