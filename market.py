"""市集與主題管理：extensions.gnome.org、gnome-look.org（OpenDesktop），以及本機主題的安裝與移除。不依賴 GTK。

從市集下載的壓縮檔是不受信任的第三方內容：只允許 https、限制大小、核對 MD5，
解壓縮時拒絕任何會寫到目的資料夾之外的路徑或連結。
"""
import hashlib
import html
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from gi.repository import Gio

EGO = "https://extensions.gnome.org"
OCS = "https://api.opendesktop.org/ocs/v1/content/data"
MAX_JSON_BYTES = 2_000_000
MAX_IMAGE_BYTES = 4_000_000
MAX_ARCHIVE_BYTES = 400_000_000
PAGE_SIZE = 15
MAX_PREVIEWS = 8  # 詳細頁最多載入幾張預覽圖

HOME_DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
ICON_DIRS = ("/usr/share/icons", str(HOME_DATA / "icons"), "~/.icons")
THEME_DIRS = ("/usr/share/themes", str(HOME_DATA / "themes"), "~/.themes")
IF = "org.gnome.desktop.interface"

# 主題種類：gnome-look 的分類編號、搜尋的資料夾、安裝位置、辨識用的檔案或資料夾、對應的設定
KINDS = {
    "icons": {"category": 132, "dirs": ICON_DIRS, "install": HOME_DATA / "icons", "markers": ("index.theme",), "setting": (IF, "icon-theme")},
    # X11 程式（libXcursor）只找 ~/.icons 與 /usr/share/icons，裝在 ~/.local/share/icons 會退回內建的小游標
    "cursors": {"category": 107, "dirs": ICON_DIRS, "install": Path.home() / ".icons", "markers": ("cursors",), "setting": (IF, "cursor-theme")},
    "gtk": {"category": 135, "dirs": THEME_DIRS, "install": HOME_DATA / "themes", "markers": ("gtk-3.0", "gtk-4.0"), "setting": (IF, "gtk-theme")},
    "shell": {"category": 134, "dirs": THEME_DIRS, "install": HOME_DATA / "themes", "markers": ("gnome-shell",),
              "setting": ("org.gnome.shell.extensions.user-theme", "name")},
}
EGO_SORTS = ["popularity", "recent", "downloads", "name"]
OCS_SORTS = ["down", "new", "high", "alpha"]  # 下載次數、最新、評分、名稱


# ---------- 網路 ----------
def http_get(url, limit):
    if not url.startswith("https://"):
        raise ValueError("only https:// URLs are allowed")
    req = urllib.request.Request(url, headers={"User-Agent": "Tuneup"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read(limit + 1)
    if len(body) > limit:
        raise ValueError("response too large")
    return body


def fetch_json(url, limit=MAX_JSON_BYTES):
    return json.loads(http_get(url, limit))


def fetch_image(url):
    return http_get(url, MAX_IMAGE_BYTES)


def plain(text):
    """市集的說明常含 HTML；轉成純文字。"""
    text = re.sub(r"<br\s*/?>|</p>|</h\d>|</li>", "\n", text or "", flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


# ---------- 擴充功能市集 ----------
def parse_ego(data):
    items = []
    for e in data.get("extensions", []):
        items.append({
            "uuid": e.get("uuid", ""), "name": e.get("name", ""), "creator": e.get("creator", ""),
            "description": (e.get("description") or "").strip(), "downloads": int(e.get("downloads") or 0),
            "page": EGO + e["link"] if e.get("link") else "", "homepage": e.get("url") or "",
            "screenshot": EGO + e["screenshot"] if e.get("screenshot") else "",
        })
    return items, int(data.get("numpages") or 1)


def search_extensions(shell_major, query="", sort="popularity", page=1):
    """回傳 (擴充功能清單, 總頁數)。"""
    q = urllib.parse.urlencode({"sort": sort, "shell_version": shell_major, "page": page, "search": query})
    return parse_ego(fetch_json(f"{EGO}/extension-query/?{q}"))


# ---------- 主題市集 ----------
def parse_ocs(data):
    items = []
    for d in data.get("data", []):
        files = []
        n = 1
        while d.get(f"downloadlink{n}"):
            files.append({"name": d.get(f"downloadname{n}", ""), "url": d[f"downloadlink{n}"],
                          "size": int(d.get(f"downloadsize{n}") or 0) * 1024, "md5": d.get(f"downloadmd5sum{n}", "")})
            n += 1
        previews = [d[f"previewpic{i}"] for i in range(1, MAX_PREVIEWS + 1) if d.get(f"previewpic{i}")]
        items.append({
            "id": str(d.get("id", "")), "name": d.get("name", ""), "author": d.get("personid", ""), "version": d.get("version", ""),
            "description": plain(d.get("description")), "downloads": int(d.get("downloads") or 0), "score": int(d.get("score") or 0),
            "preview": previews[0] if previews else "", "previews": previews, "page": d.get("detailpage", ""), "files": files,
        })
    return items, int(data.get("totalitems") or 0)


def search_themes(kind, query="", sort="down", page=0):
    """回傳 (主題清單, 總筆數)。page 從 0 開始。"""
    q = urllib.parse.urlencode({"format": "json", "categories": KINDS[kind]["category"], "sortmode": sort,
                                "pagesize": PAGE_SIZE, "page": page, "search": query})
    return parse_ocs(fetch_json(f"{OCS}?{q}"))


# ---------- 本機主題 ----------
def dirs_with(bases, marker):
    """bases 底下含有 marker 的子資料夾名稱，回傳 [(名稱, 名稱)] 供下拉選單使用。"""
    names = set()
    for base in bases:
        try:
            for d in os.scandir(os.path.expanduser(base)):
                if d.is_dir() and d.name != "default" and os.path.exists(os.path.join(d.path, marker)):
                    names.add(d.name)
        except OSError:
            pass
    return [(n, n) for n in sorted(names, key=str.lower)]


def icon_themes():
    """圖示主題：有 index.theme，而且不是只有游標的主題。"""
    cursors_only = ({n for n, _v in dirs_with(ICON_DIRS, "cursors")}
                    - {n for n, _v in dirs_with(ICON_DIRS, "scalable")} - {n for n, _v in dirs_with(ICON_DIRS, "16x16")})
    return [(n, v) for n, v in dirs_with(ICON_DIRS, "index.theme") if n not in cursors_only]


def is_theme(path, kind):
    path = Path(path)
    if not any((path / m).exists() for m in KINDS[kind]["markers"]):
        return False
    if kind == "icons":  # 只有游標的主題也有 index.theme，不算圖示主題
        return not (path / "cursors").exists() or any((path / d).exists() for d in ("scalable", "16x16", "apps", "symbolic"))
    return True


def installed_themes(kind):
    """[{"name", "path", "user"}]；同名時使用者安裝的優先。user 為 True 的才能移除。"""
    home = str(Path.home())
    found = {}
    for base in KINDS[kind]["dirs"]:
        base = os.path.expanduser(base)
        try:
            entries = sorted(os.scandir(base), key=lambda d: d.name.lower())
        except OSError:
            continue
        for d in entries:
            if d.is_dir() and d.name != "default" and is_theme(d.path, kind):
                user = d.path.startswith(home)
                if d.name not in found or user:
                    found[d.name] = {"name": d.name, "path": d.path, "user": user}
    return sorted(found.values(), key=lambda t: (not t["user"], t["name"].lower()))


def remove_theme(path):
    """把使用者安裝的主題移到垃圾桶。"""
    path = Path(path).resolve()
    if Path.home() not in path.parents:
        raise ValueError("only themes in the home folder can be removed")
    Gio.File.new_for_path(str(path)).trash(None)


# ---------- 下載與安裝 ----------
def download(url, dest, md5=""):
    if not url.startswith("https://"):
        raise ValueError("only https:// URLs are allowed")
    digest = hashlib.md5()
    total = 0
    req = urllib.request.Request(url, headers={"User-Agent": "Tuneup"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as out:
        while chunk := r.read(1 << 16):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("file too large")
            digest.update(chunk)
            out.write(chunk)
    if md5 and digest.hexdigest() != md5.lower():
        raise ValueError("checksum mismatch")


def safe_extract(archive, dest):
    """解壓縮到 dest；任何會寫到 dest 之外的成員都會讓整個動作失敗。"""
    dest = Path(dest).resolve()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                target = (dest / info.filename).resolve()
                if dest != target and dest not in target.parents:
                    raise ValueError(f"unsafe path in archive: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"symlink in zip archive: {info.filename}")
            z.extractall(dest)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            try:
                t.extractall(dest, filter="data")  # 拒絕絕對路徑、..、指向外部的連結與裝置檔
            except tarfile.FilterError as err:
                raise ValueError(f"unsafe archive: {err}") from err
    else:
        raise ValueError("not a tar or zip archive")


def find_themes(root, kind, depth=3):
    """在解壓縮後的資料夾裡找出主題資料夾（壓縮檔可能多包一層，或一次含多個變體）。"""
    root = Path(root)
    if is_theme(root, kind):
        return [root]
    if depth == 0:
        return []
    found = []
    for child in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        found += find_themes(child, kind, depth - 1)
    return found


def install_archive(kind, archive):
    """把壓縮檔裡的主題裝到使用者的主題資料夾，回傳安裝的主題名稱。同名的舊版本會先移到垃圾桶。"""
    target = KINDS[kind]["install"]
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target, prefix=".tuneup-") as tmp:
        safe_extract(archive, tmp)
        themes = [t for t in find_themes(tmp, kind) if t != Path(tmp)]
        if not themes:
            raise ValueError("no theme of this kind found in the archive")
        names = []
        for theme in themes:
            final = target / theme.name
            if final.exists():
                Gio.File.new_for_path(str(final)).trash(None)
            shutil.move(str(theme), str(final))
            names.append(theme.name)
    return names


def install_from_market(kind, file):
    """下載市集的一個檔案並安裝。file 是 search_themes() 回傳的 files 項目。"""
    with tempfile.TemporaryDirectory(prefix="tuneup-dl-") as tmp:
        archive = Path(tmp) / "theme-archive"
        download(file["url"], archive, file.get("md5", ""))
        return install_archive(kind, archive)
