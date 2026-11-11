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
