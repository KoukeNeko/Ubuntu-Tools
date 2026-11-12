"""讀取系統狀態（清理、硬體、備份）與執行需要管理員權限的指令。不依賴 GTK。

讀取都不需要權限；會改動系統的指令一律由介面在使用者確認後呼叫 run_root()，
由 pkexec 跳出系統的密碼視窗。
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

UNITS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
PKEXEC_DISMISSED = (126, 127)  # 使用者取消授權，或授權失敗


def run(cmd, timeout=20):
    """執行指令，回傳 (結束碼, 標準輸出)。指令不存在或逾時時結束碼為 -1。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    return p.returncode, p.stdout


def run_root(cmd, timeout=900):
    """以管理員權限執行；回傳 (結束碼, 輸出)。"""
    return run(["pkexec", *cmd], timeout)


def human(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def parse_journal_usage(text):
    """'Archived and active journals take up 24.0M in the file system.' -> 位元組數"""
    m = re.search(r"take up ([\d.]+)\s*([BKMGT])", text)
    return int(float(m.group(1)) * UNITS[m.group(2)]) if m else 0


def parse_autoremove(text):
    """apt-get -s autoremove 的輸出 -> 會被移除的套件名稱"""
    return re.findall(r"^Remv (\S+)", text, re.M)


# ---------- 清理 ----------
THUMBNAILS = Path.home() / ".cache" / "thumbnails"
TRASH = Path.home() / ".local" / "share" / "Trash"
APT_CACHE = Path("/var/cache/apt/archives")
JOURNAL_KEEP = "14d"


def cleanup_items():
    """可清理的項目。size 是可釋放的位元組數；root 為 True 的需要管理員權限。"""
    packages = parse_autoremove(run(["apt-get", "-s", "autoremove"])[1])
    return [
        {"id": "thumbnails", "size": dir_size(THUMBNAILS), "root": False, "count": None},
        {"id": "trash", "size": dir_size(TRASH), "root": False, "count": None},
        {"id": "apt-cache", "size": sum(f.stat().st_size for f in APT_CACHE.glob("*.deb")) if APT_CACHE.is_dir() else 0, "root": True, "count": None},
        {"id": "journal", "size": parse_journal_usage(run(["journalctl", "--disk-usage"])[1]), "root": True, "count": None},
        {"id": "autoremove", "size": None, "root": True, "count": len(packages), "packages": packages},
    ]


def clean(item_id):
    """執行清理，回傳 (結束碼, 輸出)。"""
    if item_id == "thumbnails":
        shutil.rmtree(THUMBNAILS, ignore_errors=True)
        return 0, ""
    if item_id == "trash":
        return run(["gio", "trash", "--empty"])
    commands = {
        "apt-cache": ["apt-get", "clean"],
        "journal": ["journalctl", f"--vacuum-time={JOURNAL_KEEP}"],
        "autoremove": ["apt-get", "-y", "autoremove"],
    }
    return run_root(commands[item_id])


# ---------- 硬體 ----------
def parse_upower(text):
    """upower -i 的輸出 -> {欄位: 值}"""
    info = {}
    for line in text.splitlines():
        key, sep, val = line.partition(":")
        if sep and val.strip():
            info[key.strip()] = val.strip()
    return info


def batteries():
    result = []
    for path in run(["upower", "-e"])[1].split():
        if "battery" in path.lower():
            info = parse_upower(run(["upower", "-i", path])[1])
            if info.get("energy-full"):
                result.append(info)
    return result


def firmware():
    """回傳 (裝置清單, 可更新數量)；fwupd 不存在時是 ([], 0)。"""
    rc, out = run(["fwupdmgr", "get-devices", "--json"])
    try:
        devices = [d for d in json.loads(out).get("Devices", []) if d.get("Name")] if rc == 0 else []
    except ValueError:
        devices = []
    rc, out = run(["fwupdmgr", "get-updates", "--json"])
    try:
        updates = len(json.loads(out).get("Devices", [])) if rc == 0 else 0
    except ValueError:
        updates = 0
    return devices, updates


def drivers():
    """ubuntu-drivers 建議的驅動套件；沒有建議時是空清單。"""
    rc, out = run(["ubuntu-drivers", "list"], timeout=40)
    return [line.split(",")[0].strip() for line in out.splitlines() if line.strip()] if rc == 0 else []


# ---------- 備份 ----------
def backup_tools():
    return {"timeshift": shutil.which("timeshift") is not None, "deja-dup": shutil.which("deja-dup") is not None}


def root_usage():
    usage = shutil.disk_usage("/")
    return usage.used, usage.free


# ---------- 應用程式 ----------
SNAP_DIR = Path("/var/lib/snapd/snaps")
SNAP_DESKTOP_DIR = Path("/var/lib/snapd/desktop/applications")
SNAP_SYSTEM_NOTES = ("base", "snapd", "core", "gadget", "kernel")
# 移除這些套件會讓系統無法開機或登入；模擬結果只要碰到就拒絕
PROTECTED_PREFIXES = ("ubuntu-desktop", "ubuntu-minimal", "ubuntu-standard", "gnome-shell", "gdm3", "systemd", "linux-image",
                      "linux-generic", "grub", "sudo", "apt", "dpkg", "libc6", "network-manager", "python3-minimal", "snapd")


def parse_snap_list(text):
    """snap list 的輸出 -> [{"name", "version", "rev", "publisher", "system"}]"""
    snaps = []
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 6:
            snaps.append({"name": cols[0], "version": cols[1], "rev": cols[2], "publisher": cols[4].rstrip("*✓"),
                          "system": any(note in SNAP_SYSTEM_NOTES for note in cols[5].split(","))})
    return snaps


def snaps():
    found = parse_snap_list(run(["snap", "list"])[1])
    for s in found:
        # 沒有桌面啟動項目的是執行環境或主題這類共用元件，移除會弄壞其他 Snap
        if not any(SNAP_DESKTOP_DIR.glob(f"{s['name']}_*.desktop")):
            s["system"] = True
        try:
            s["size"] = (SNAP_DIR / f"{s['name']}_{s['rev']}.snap").stat().st_size
        except OSError:
            s["size"] = 0
    return found


def parse_dpkg(text):
    """dpkg-query 以 tab 分隔的輸出 -> [{"name", "size", "version", "summary"}]，size 是位元組"""
    packages = []
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) == 4 and cols[1].isdigit():
            packages.append({"name": cols[0], "size": int(cols[1]) * 1024, "version": cols[2], "summary": cols[3]})
    return packages


def apt_manual():
    """使用者手動安裝的 apt 套件（不含被相依帶進來的）。"""
    names = run(["apt-mark", "showmanual"])[1].split()
    if not names:
        return []
    return parse_dpkg(run(["dpkg-query", "-W", "-f=${Package}\t${Installed-Size}\t${Version}\t${binary:Summary}\n", *names])[1])


def removal_plan(package):
    """模擬移除，回傳 (會被移除的套件, 是否碰到系統必要套件)。"""
    removed = parse_autoremove(run(["apt-get", "-s", "remove", package])[1])
    return removed, any(name.startswith(PROTECTED_PREFIXES) for name in removed)


# ---------- 系統更新 ----------
def parse_upgradable(text):
    """apt list --upgradable 的輸出 -> [{"name", "new", "old"}]"""
    return [{"name": m[0], "new": m[1], "old": m[2]}
            for m in re.findall(r"^([^/\s]+)/\S+ (\S+) \S+ \[upgradable from: ([^\]]+)\]", text, re.M)]


def upgradable():
    return parse_upgradable(run(["apt", "list", "--upgradable"])[1])


# ---------- 錯誤記錄 ----------
def parse_journal(text):
    """journalctl -o json 的輸出 -> 依來源與訊息合併後的清單，最近的在前。"""
    merged = {}
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("MESSAGE")
        if not isinstance(message, str):  # 二進位訊息在 JSON 裡是數字陣列
            continue
        source = entry.get("SYSLOG_IDENTIFIER") or entry.get("_COMM") or "?"
        item = merged.setdefault((source, message), {"source": source, "message": message, "count": 0, "time": 0})
        item["count"] += 1
        item["time"] = max(item["time"], int(entry.get("__REALTIME_TIMESTAMP", 0)) // 1_000_000)
    return sorted(merged.values(), key=lambda i: i["time"], reverse=True)


def boot_errors(limit=500):
    return parse_journal(run(["journalctl", "-b", "-p", "err", "-o", "json", "--no-pager", "-n", str(limit)])[1])


# ---------- 搜尋與安裝 ----------
def parse_apt_search(text, query):
    """apt-cache search 的輸出 -> [{"name", "summary"}]，名稱完全相符或以關鍵字開頭的排前面。"""
    found = [{"name": name, "summary": summary} for name, summary in re.findall(r"^(\S+) - (.*)$", text, re.M)]
    return sorted(found, key=lambda p: (p["name"] != query, not p["name"].startswith(query), p["name"]))


def parse_snap_find(text):
    """snap find 的輸出 -> [{"name", "version", "publisher", "summary"}]"""
    found = []
    for line in text.splitlines()[1:]:
        cols = re.split(r"\s{2,}", line.strip(), maxsplit=4)
        if len(cols) == 5:
            found.append({"name": cols[0], "version": cols[1], "publisher": cols[2].rstrip("*✓"), "summary": cols[4]})
    return found


def search_apps(query, limit=12):
    query = query.strip().lstrip("-").lower()  # 不讓關鍵字被當成指令選項
    if not query:
        return {"apt": [], "snap": []}
    return {"apt": parse_apt_search(run(["apt-cache", "search", "--names-only", query])[1], query)[:limit],
            "snap": parse_snap_find(run(["snap", "find", query], timeout=6)[1])[:limit]}  # 商店連不上時不要讓使用者等太久


# ---------- 更新紀錄與暫緩 ----------
APT_HISTORY = Path("/var/log/apt/history.log")


def parse_apt_history(text):
    """apt 的 history.log -> [{"time", "command", "install", "upgrade", "remove"}]，最近的在前。"""
    entries = []
    for block in text.split("\n\n"):
        fields = dict(re.findall(r"^([A-Za-z-]+): (.*)$", block, re.M))
        if "Start-Date" not in fields:
            continue
        count = {kind: len(re.findall(r"\S+ \(", fields.get(kind.capitalize(), ""))) for kind in ("install", "upgrade", "remove")}
        entries.append({"time": " ".join(fields["Start-Date"].split()), "command": fields.get("Commandline", ""), **count})
    return entries[::-1]


def apt_history(limit=15):
    try:
        return parse_apt_history(APT_HISTORY.read_text(errors="replace"))[:limit]
    except OSError:
        return []


def held():
    return run(["apt-mark", "showhold"])[1].split()


# ---------- 鍵盤配置選項 ----------
XKB_RULES = Path("/usr/share/X11/xkb/rules/evdev.lst")


def parse_xkb_options(text):
    """evdev.lst 的「! option」區段 -> [(群組說明, [(選項, 說明)])]"""
    groups = []
    section = text.split("! option", 1)[1] if "! option" in text else ""
    for line in section.splitlines():
        if line.startswith("!"):
            break
        name, _sep, desc = line.strip().partition(" ")
        if not name:
            continue
        if ":" in name:
            if groups:
                groups[-1][1].append((name, desc.strip()))
        else:
            groups.append((desc.strip(), []))
    return [g for g in groups if g[1]]


def xkb_options():
    try:
        return parse_xkb_options(XKB_RULES.read_text(errors="replace"))
    except OSError:
        return []


# ---------- 軟體來源 ----------
APT_SOURCES = [Path("/etc/apt/sources.list"), Path("/etc/apt/sources.list.d")]


def parse_deb822(text):
    """.sources 檔 -> [{"uri", "suites", "enabled"}]"""
    sources = []
    for stanza in re.split(r"\n\s*\n", text):
        fields = {k.lower(): v.strip() for k, v in re.findall(r"^([A-Za-z-]+):\s*(.*)$", stanza, re.M)}
        if fields.get("uris"):
            sources.append({"uri": fields["uris"], "suites": fields.get("suites", ""), "enabled": fields.get("enabled", "yes").lower() != "no"})
    return sources


def parse_sources_list(text):
    """傳統的 .list 檔 -> [{"uri", "suites", "enabled"}]；被註解掉的 deb 行視為停用"""
    sources = []
    for line in text.splitlines():
        m = re.match(r"^\s*(#\s*)?deb(?:-src)?\s+(?:\[[^\]]*\]\s+)?(\S+)\s+(\S+)", line)
        if m:
            sources.append({"uri": m.group(2), "suites": m.group(3), "enabled": not m.group(1)})
    return sources


def apt_sources():
    files = [APT_SOURCES[0]] + (sorted(APT_SOURCES[1].iterdir()) if APT_SOURCES[1].is_dir() else [])
    found = []
    for path in files:
        if path.suffix not in (".sources", ".list"):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for src in (parse_deb822 if path.suffix == ".sources" else parse_sources_list)(text):
            found.append(dict(src, file=path.name))
    return found


# ---------- 闔上螢幕時不暫停 ----------
# 做法與 GNOME Tweaks 相同：登入時啟動一個持有 handle-lid-switch 抑制鎖的程序。
LID_AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "tuneup-ignore-lid.desktop"
LID_COMMAND = ["systemd-inhibit", "--what=handle-lid-switch", "--who=Tuneup", "--why=lid", "sleep", "infinity"]


def lid_ignored():
    return LID_AUTOSTART.exists()


def set_lid_ignored(on):
    """立即生效，並在之後每次登入時維持。"""
    subprocess.run(["pkill", "-f", "systemd-inhibit --what=handle-lid-switch --who=Tuneup"], check=False)
    if on:
        LID_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        LID_AUTOSTART.write_text("[Desktop Entry]\nType=Application\nName=Tuneup: ignore lid close\n"
                                 f"Exec={' '.join(LID_COMMAND)}\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n")
        subprocess.Popen(LID_COMMAND, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        LID_AUTOSTART.unlink(missing_ok=True)


# ---------- 還原點與備份狀態 ----------
# Timeshift 把還原點放在備份裝置的 /timeshift/snapshots；備份裝置就是系統碟時不需要權限就能讀。
TIMESHIFT_SNAPSHOTS = Path("/timeshift/snapshots")
STALE_BACKUP_DAYS = 30


def restore_points(base=TIMESHIFT_SNAPSHOTS):
    """[{"name", "time", "tags", "comments"}]，最近的在前；讀不到時是空清單。"""
    points = []
    try:
        folders = [d for d in base.iterdir() if d.is_dir()]
    except OSError:
        return points
    for folder in folders:
        try:
            info = json.loads((folder / "info.json").read_text())
            points.append({"name": folder.name, "time": int(info.get("created", 0)),
                           "tags": info.get("tags", ""), "comments": info.get("comments", "")})
        except (OSError, ValueError):
            continue  # 建立到一半或損毀的還原點
    return sorted(points, key=lambda p: p["time"], reverse=True)


def delete_restore_point(name):
    return run_root(["timeshift", "--delete", "--snapshot", name, "--scripted"], timeout=3600)


def parse_iso_time(text):
    """Déjà Dup 記錄的時間（ISO 8601）-> epoch 秒；空字串或格式不對時回傳 None。"""
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(text).timestamp()) if text else None
    except ValueError:
        return None


def last_file_backup():
    return parse_iso_time(run(["gsettings", "get", "org.gnome.DejaDup", "last-backup"])[1].strip().strip("'"))
