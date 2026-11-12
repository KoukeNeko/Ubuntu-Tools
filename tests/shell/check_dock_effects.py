#!/usr/bin/env python3
"""在隔離的 GNOME Shell 裡實際執行 Dock 效果擴充功能，檢查放大、彈跳與懸停預覽。

這個擴充功能跑在桌面本體裡，語法正確不代表有作用，而在 Wayland 上重新載入又必須登出。
所以這裡另外啟動一個不顯示畫面的 GNOME Shell：獨立的訊息匯流排、獨立的設定、
獨立的擴充功能資料夾，不會動到正在使用的桌面。

用法:  python3 tests/shell/check_dock_effects.py [LEFT|BOTTOM|RIGHT] [--keep]
需要:  gnome-shell、Ubuntu Dock、gnome-calculator，以及 dbus-run-session 與 dbus-daemon。
       系統沒有後兩者時（Ubuntu 改用 dbus-broker），可以不安裝、只解開套件：
           apt download dbus-daemon && dpkg -x dbus-daemon_*.deb /tmp/dbus
           TUNEUP_DBUS_BIN=/tmp/dbus/usr/bin python3 tests/shell/check_dock_effects.py
結束碼: 0 全部通過，1 有檢查失敗，77 缺少必要工具而略過。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UUID = "tuneup-dock-effects@koukeneko.github.io"
RUN_SECONDS = 24
SKIP = 77

# 注入到擴充功能 enable() 結尾的自我測試：用虛擬滑鼠操作 Dock，把觀察到的狀態印成 TUNEUP-TEST 行。
SELFTEST = r'''
        this._testEvents = 0;
        const realOnEvent = this._onEvent.bind(this);
        this._onEvent = (dock, event) => { this._testEvents++; return realOnEvent(dock, event); };
        GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 7, () => { safely('selftest', () => this._selfTest()); return GLib.SOURCE_REMOVE; });
    }

    _selfTest() {
        const report = (name, value) => console.log(`TUNEUP-TEST ${name}=${value}`);
        const later = (ms, fn) => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { safely('selftest@' + ms, fn); return GLib.SOURCE_REMOVE; });
        const dock = findDocks(Main.layoutManager.uiGroup)[0];
        report('docks', this._docks.size);
        if (!dock)
            return;
        const items = this._items(dock);
        report('items', items.length);
        const pointer = global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        this._testPointer = pointer;
        const moveTo = (x, y) => pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
        const hover = item => {
            const [x, y] = item.get_transformed_position();
            const [w, h] = item.get_transformed_size();
            moveTo(x + w / 2 - 3, y + h / 2);
            later(120, () => moveTo(x + w / 2, y + h / 2));
        };
        const away = () => moveTo(640, 400);
        const maxScale = () => Math.max(...items.map(i => i.child.scale_x)).toFixed(2);
        const overflow = item => {   // 圖示畫出來的範圍是否超出 Dock 的長條
            const {vertical, bounds} = this._geometry(dock);
            const [x, y] = item.child.get_transformed_position();
            const [w, h] = item.child.get_transformed_size();
            return vertical ? x + w > bounds[0] + bounds[2] + 1 || x < bounds[0] - 1 : y < bounds[1] - 1 || y + h > bounds[1] + bounds[3] + 1;
        };
        const target = items[Math.min(2, items.length - 1)];

        hover(target);
        later(500, () => {
            report('events', this._testEvents);
            report('magnified_scale', maxScale());
            report('magnified_overflows', overflow(target));
            report('unclipped_while_magnified', this._unclipped.has(dock));
            report('pointer_still_on_target', this._itemAt(dock, ...global.get_pointer().slice(0, 2)) === target);
        });
        later(900, away);
        later(1500, () => {
            report('reset_scale', maxScale());
            report('clips_restored_after_leave', !this._unclipped.has(dock));
        });

        // 真的啟動一個程式：檢查彈跳，之後用它的視窗檢查懸停預覽
        later(1700, () => {
            this._testBounces = 0;
            const realBounce = this._bounce.bind(this);
            this._bounce = (...args) => { this._testBounces++; return realBounce(...args); };
            Shell.AppSystem.get_default().lookup_app('org.gnome.Calculator.desktop')?.launch(0, -1, Shell.AppLaunchGpu.DEFAULT);
        });
        later(6500, () => {
            const item = this._items(dock).find(i => i.child.app?.get_id() === 'org.gnome.Calculator.desktop');
            report('bounces_on_launch', this._testBounces);
            report('app_windows', item ? this._windowsOf(item).length : -1);
            this._testApp = item;
            if (item)
                hover(item);
        });
        later(7700, () => report('preview_opens', !!this._testApp?.child._previewMenu?.isOpen));
        later(8200, away);
        later(9600, () => {
            report('preview_closes', !this._testApp?.child._previewMenu?.isOpen);
            report('reset_after_preview', maxScale());
            report('clips_restored_after_preview', !this._unclipped.has(dock));
            report('done', true);
        });
    }
'''

# 檢查項目：(名稱, 判斷函式, 說明)
CHECKS = [
    ("docks", lambda v: int(v) >= 1, "找到 Dock"),
    ("items", lambda v: int(v) >= 3, "找到 Dock 上的圖示"),
    ("events", lambda v: int(v) > 0, "收到滑鼠事件"),
    ("magnified_scale", lambda v: float(v) > 1.3, "滑鼠下的圖示有放大"),
    ("magnified_overflows", lambda v: v == "true", "放大的圖示超出 Dock 的範圍"),
    ("unclipped_while_magnified", lambda v: v == "true", "放大時解除 Dock 的裁切"),
    ("pointer_still_on_target", lambda v: v == "true", "放大後滑鼠仍在同一個圖示上"),
    ("reset_scale", lambda v: float(v) < 1.01, "滑鼠離開後還原大小"),
    ("clips_restored_after_leave", lambda v: v == "true", "離開後還原 Dock 的裁切"),
    ("bounces_on_launch", lambda v: int(v) >= 1, "啟動程式時圖示彈跳"),
    ("app_windows", lambda v: int(v) >= 1, "測試用的程式開出視窗"),
    ("preview_opens", lambda v: v == "true", "滑鼠停留後開啟視窗預覽"),
    ("preview_closes", lambda v: v == "true", "滑鼠離開後收起預覽"),
    ("reset_after_preview", lambda v: float(v) < 1.01, "預覽收起後還原大小"),
    ("clips_restored_after_preview", lambda v: v == "true", "預覽收起後還原 Dock 的裁切"),
]


def find_tool(name):
    extra = os.environ.get("TUNEUP_DBUS_BIN")
    return shutil.which(name, path=f"{extra}{os.pathsep}{os.environ['PATH']}" if extra else None)


def main():
    position = next((a for a in sys.argv[1:] if not a.startswith("--")), "LEFT").upper()
    keep = "--keep" in sys.argv
    run_session, daemon = find_tool("dbus-run-session"), find_tool("dbus-daemon")
    missing = [n for n, p in (("gnome-shell", shutil.which("gnome-shell")), ("gnome-calculator", shutil.which("gnome-calculator")),
                              ("dbus-run-session", run_session), ("dbus-daemon", daemon)) if not p]
    if missing:
        print("略過：找不到 " + "、".join(missing) + "（做法見這個檔案開頭的說明）")
        return SKIP

    work = Path(tempfile.mkdtemp(prefix="tuneup-shell-"))
    ext = work / "data" / "gnome-shell" / "extensions" / UUID
    shutil.copytree(REPO / "extension" / UUID, ext)
    subprocess.run(["glib-compile-schemas", str(ext / "schemas")], check=True)
    source = (ext / "extension.js").read_text()
    anchor = "        safely('attach', () => this._attachDocks());\n    }\n"
    if source.count(anchor) != 1:
        print("失敗：找不到注入自我測試的位置，enable() 的結尾改過了")
        return 1
    (ext / "extension.js").write_text(source.replace(anchor, anchor[:-len("    }\n")] + SELFTEST))

    settings = work / "config" / "glib-2.0" / "settings"
    settings.mkdir(parents=True)
    (settings / "keyfile").write_text(
        "[org/gnome/shell]\n"
        f"enabled-extensions=['ubuntu-dock@ubuntu.com', '{UUID}']\n"
        "favorite-apps=['org.gnome.Nautilus.desktop', 'org.gnome.TextEditor.desktop', 'org.gnome.Calculator.desktop']\n"
        "welcome-dialog-last-shown-version='99'\n\n"
        "[org/gnome/shell/extensions/dash-to-dock]\n"
        f"dock-position='{position}'\n")

    env = {**os.environ, "GSETTINGS_BACKEND": "keyfile", "XDG_CONFIG_HOME": str(work / "config"), "XDG_DATA_HOME": str(work / "data")}
    command = [run_session, f"--dbus-daemon={daemon}", "--", "timeout", str(RUN_SECONDS), "gnome-shell", "--headless", "--wayland",
               "--no-x11", "--virtual-monitor", "1280x800", "--wayland-display", f"tuneup-test-{os.getpid()}"]
    log = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
    (work / "shell.log").write_text(log)

    results = dict(re.findall(r"TUNEUP-TEST (\w+)=(\S+)", log))
    errors = re.findall(r"tuneup-dock-effects: .*", log)
    failed = 0
    print(f"Dock 位置：{position}")
    for name, passes, description in CHECKS:
        value = results.get(name)
        try:
            ok = value is not None and passes(value)
        except ValueError:
            ok = False
        failed += not ok
        print(f"  {'通過' if ok else '失敗'}  {description}（{name}={value}）")
    for line in errors:
        failed += 1
        print(f"  失敗  擴充功能回報錯誤：{line}")
    if "done" not in results:
        failed += 1
        print("  失敗  自我測試沒有跑完")

    if keep or failed:
        print(f"記錄保留在 {work / 'shell.log'}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
