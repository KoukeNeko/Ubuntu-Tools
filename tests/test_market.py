"""market.py 的測試：解析、主題辨識，以及不受信任壓縮檔的防護。不連網。"""
import io
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market  # noqa: E402


def make_tar(path, members):
    """members: {成員路徑: 內容；None 代表資料夾；("link", 目標) 代表符號連結}"""
    with tarfile.open(path, "w:xz") as t:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                t.addfile(info)
            elif isinstance(content, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, content[1]
                t.addfile(info)
            else:
                info.size = len(content)
                t.addfile(info, io.BytesIO(content))


class MarketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tuneup-market-"))
        self.install = self.tmp / "icons"
        patched = {k: dict(v, install=self.install, dirs=(str(self.install),)) for k, v in market.KINDS.items()}
        patcher = mock.patch.object(market, "KINDS", patched)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_tar_path_traversal_rejected(self):
        archive = self.tmp / "evil.tar.xz"
        make_tar(archive, {"../escaped.txt": b"x"})
        with self.assertRaises(ValueError):
            market.safe_extract(archive, self.tmp / "out")
        self.assertFalse((self.tmp / "escaped.txt").exists())

    def test_tar_symlink_escape_rejected(self):
        archive = self.tmp / "evil2.tar.xz"
        make_tar(archive, {"theme/link": ("link", "/etc/passwd")})
        with self.assertRaises(ValueError):
            market.safe_extract(archive, self.tmp / "out2")

    def test_zip_path_traversal_rejected(self):
        archive = self.tmp / "evil.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("../../escaped.txt", "x")
        with self.assertRaises(ValueError):
            market.safe_extract(archive, self.tmp / "out3")
        self.assertFalse((self.tmp.parent / "escaped.txt").exists())

    def test_not_an_archive(self):
        junk = self.tmp / "junk"
        junk.write_text("hello")
        with self.assertRaises(ValueError):
            market.safe_extract(junk, self.tmp / "out4")

    def test_install_finds_nested_and_multiple_themes(self):
        archive = self.tmp / "icons.tar.xz"
        make_tar(archive, {"pack/Candy/index.theme": b"[Icon Theme]", "pack/Candy/scalable/a.svg": b"<svg/>",
                           "pack/Candy-dark/index.theme": b"[Icon Theme]", "pack/Candy-dark/apps/a.svg": b"<svg/>",
                           "pack/README": b"hi"})
        self.assertEqual(sorted(market.install_archive("icons", archive)), ["Candy", "Candy-dark"])
        self.assertTrue((self.install / "Candy" / "index.theme").exists())
        self.assertEqual([p.name for p in self.install.iterdir() if p.name.startswith(".tuneup-")], [])
        self.assertEqual([t["name"] for t in market.installed_themes("icons")], ["Candy", "Candy-dark"])

    def test_install_rejects_wrong_kind(self):
        archive = self.tmp / "notcursor.tar.xz"
        make_tar(archive, {"Foo/index.theme": b"[Icon Theme]", "Foo/scalable/a.svg": b"<svg/>"})
        with self.assertRaises(ValueError):
            market.install_archive("cursors", archive)

    def test_cursor_only_theme_is_not_an_icon_theme(self):
        cursor = self.install / "Bibata"
        (cursor / "cursors").mkdir(parents=True)
        (cursor / "index.theme").write_text("[Icon Theme]")
        self.assertTrue(market.is_theme(cursor, "cursors"))
        self.assertFalse(market.is_theme(cursor, "icons"))

    def test_remove_refuses_outside_home(self):
        with self.assertRaises(ValueError):
            market.remove_theme("/usr/share/icons/Adwaita")

    def test_download_refuses_plain_http(self):
        with self.assertRaises(ValueError):
            market.download("http://example.com/a.tar.xz", self.tmp / "a")
        with self.assertRaises(ValueError):
            market.http_get("file:///etc/passwd", 10)

    def test_parse_ego(self):
        items, pages = market.parse_ego({"numpages": 7, "extensions": [
            {"uuid": "a@b", "name": "A", "creator": "c", "description": " d ", "downloads": "12", "link": "/extension/1/a/", "screenshot": None}]})
        self.assertEqual(pages, 7)
        self.assertEqual((items[0]["downloads"], items[0]["page"], items[0]["screenshot"]), (12, "https://extensions.gnome.org/extension/1/a/", ""))

    def test_parse_ocs_collects_all_files(self):
        items, total = market.parse_ocs({"totalitems": 3, "data": [{
            "id": 5, "name": "Candy", "personid": "eli", "description": "<h2>Hi &hearts;<br />there</h2>", "downloads": "10", "score": "96",
            "downloadlink1": "https://f/1", "downloadname1": "a.tar.xz", "downloadsize1": "1702", "downloadmd5sum1": "abc",
            "downloadlink2": "https://f/2", "downloadname2": "b.zip"}]})
        self.assertEqual(total, 3)
        self.assertEqual(items[0]["description"], "Hi ♥\nthere")
        self.assertEqual([(f["name"], f["size"]) for f in items[0]["files"]], [("a.tar.xz", 1702 * 1024), ("b.zip", 0)])


if __name__ == "__main__":
    unittest.main()
