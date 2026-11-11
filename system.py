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
