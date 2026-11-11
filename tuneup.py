#!/usr/bin/env python3
"""Tuneup — GTK4 + libadwaita  — Ubuntu 調校與擴充功能管理。所有寫入都經過 store.apply()，會留下可還原的紀錄。。
用法:  python3 tuneup.py            開啟視窗
       python3 tuneup.py --shot DIR  將每個頁面截圖存到 DIR 後離開
       python3 tuneup.py --lang en   這次以指定語言開啟（system / zh_TW / en）
"""
import sys
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

# ---------- 多語言 ----------
# 原始字串為繁體中文；其他語言放在 i18n/<代碼>.json（{"中文原文": "譯文"}）。
# 新增語言：複製 i18n/en.json 改檔名與內容，再在 LANGS 加一列即可。
import json
import os
import shutil
import subprocess
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import store
import system

I18N_DIR = Path(__file__).resolve().parent / "i18n"
CONF = Path(GLib.get_user_config_dir()) / "tuneup" / "config.json"
LANGS = [("system", "跟隨系統"), ("zh_TW", "繁體中文"), ("en", "English")]  # 語言名稱用該語言自己的寫法，不翻譯
_catalog = {}
_missing = set()


def system_lang():
    env = os.environ.get("LANGUAGE") or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "zh_TW" if env.lower().startswith("zh") else "en"


def read_pref():
    try:
        return json.loads(CONF.read_text()).get("lang", "system")
    except (OSError, ValueError):
        return "system"


def save_pref(code):
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps({"lang": code}))


def load_lang(pref):
    """載入語言；找不到翻譯檔時退回中文原文。"""
    global _catalog
    code = system_lang() if pref == "system" else pref
    _catalog = {}
    f = I18N_DIR / f"{code}.json"
    if code != "zh_TW" and f.exists():
        _catalog = json.loads(f.read_text())
    _catalog["__code__"] = code


def _m(s):
    """翻譯後跳脫 & < >：Adw 的列標題、副標題、橫幅、提示都會解析 markup。"""
    t = _(s)
    return GLib.markup_escape_text(t) if isinstance(t, str) else t


def _(s):
    """翻譯一個字串；沒有譯文就原樣回傳（專有名詞、版本號等）。"""
    if not isinstance(s, str) or not s:
        return s
    t = _catalog.get(s)
    if t is None:
        if _catalog.get("__code__") != "zh_TW" and any("\u4e00" <= ch <= "\u9fff" for ch in s):
            _missing.add(s)
        return s
    return t


COLORS = ["#e95420", "#7a5af8", "#1f9d8a", "#d6336c", "#3b82f6", "#e08a00", "#16a34a", "#8b5cf6", "#0ea5e9"]
CSS = "".join(f".t{i}{{background:{c}}}" for i, c in enumerate(COLORS)) + """
/* 只有這幾個自訂樣式；間距尺度 6/12/18/24，圓角 8/99（見 DESIGN.md） */
.tile{border-radius:8px;color:white;font-weight:700;min-width:32px;min-height:32px}
.logo{background:linear-gradient(135deg,#ff6a2b,#c2410c);border-radius:12px;color:white;min-width:48px;min-height:48px}
.chip{border-radius:99px;padding:0 8px;min-height:0;font-size:11px;background:alpha(currentColor,.12)}
.chip.success{color:@success_color;background:alpha(@success_color,.15)}
.chip.warning{color:@warning_color;background:alpha(@warning_color,.15)}
.chip.error{color:@error_color;background:alpha(@error_color,.15)}
/* 列高固定：有副標題 64、單行 50；整個 app 用同一套字型（Noto Sans CJK TC）並寫死行高，否則中英混排時系統會混用兩套字型，文字位置逐列飄移 1–3px */
window,popover{font-family:"Noto Sans CJK TC","Noto Sans",sans-serif}
row .title{line-height:22px}
row .subtitle{line-height:18px}
row.two-line box.header{min-height:64px}
.diff{font-family:monospace;font-size:small;padding:12px}
.navigation-sidebar row.sep{background:none;min-height:0;padding:0}
.swatch{min-width:24px;min-height:24px;border-radius:99px;padding:0;border:2px solid transparent}
.swatch:checked{border-color:@window_fg_color}
"""


# ---------- 系統資料 ----------
EXT_STATE_ACTIVE, EXT_STATE_ERROR, EXT_STATE_OUT_OF_DATE = 1, 3, 4  # 取自 GNOME Shell 的 ExtensionState
AUTOSTART_DIR = Path(GLib.get_user_config_dir()) / "autostart"
MAX_IMPORT_BYTES = 1_000_000
RESET = object()  # 未套用變更中的「還原為預設值」


def load_shell_version():
    try:
        v = store.shell_call("Get", GLib.Variant("(ss)", (store.SHELL_BUS[0], "ShellVersion")), "org.freedesktop.DBus.Properties")[0]
        return v.split(".")[0]
    except GLib.Error:
        return ""


def load_extensions():
    """回傳 (擴充功能清單, 錯誤訊息)。GNOME Shell 沒有回應時清單為空。"""
    try:
        raw = store.shell_call("ListExtensions")[0]
    except GLib.Error as e:
        return [], e.message
    exts = []
    for uuid, d in raw.items():
        ver = d.get("version-name") or d.get("version")
        if isinstance(ver, float):
            ver = int(ver)
        desc = (d.get("description") or "").strip()
        exts.append({
            "uuid": uuid, "name": d.get("name") or uuid, "desc": desc.split("\n")[0], "full_desc": desc,
            "version": f"v{ver}" if ver else "", "state": int(d.get("state", 0)), "error": d.get("error", ""),
            "has_update": bool(d.get("hasUpdate")), "has_prefs": bool(d.get("hasPrefs")), "url": d.get("url", ""),
            "shell": d.get("shell-version", []), "system": d.get("type") == 1, "path": d.get("path", ""),
        })
    exts.sort(key=lambda e: e["name"].lower())
    return exts, None


def load_system():
    name = "Linux"
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                name = line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    shell = load_shell_version()
    session = {"wayland": "Wayland", "x11": "X11"}.get(os.environ.get("XDG_SESSION_TYPE", ""), "")
    parts = [f"GNOME {shell}" if shell else "", session, f"Kernel {os.uname().release}"]
    return name, " · ".join(p for p in parts if p), shell


def in_background(work, done):
    """在背景執行緒跑 work()，結束後在主執行緒呼叫 done(結果, 錯誤訊息)。"""
    def run():
        try:
            result, err = work(), None
        except (OSError, ValueError) as e:
            result, err = None, str(e)
        GLib.idle_add(lambda: done(result, err) and False)
    threading.Thread(target=run, daemon=True).start()


def fetch_json(url):
    if not url.startswith("https://"):
        raise ValueError("only https:// URLs are allowed")
    req = urllib.request.Request(url, headers={"User-Agent": "Tuneup"})
    with urllib.request.urlopen(req, timeout=8) as r:
        body = r.read(MAX_IMPORT_BYTES + 1)
    if len(body) > MAX_IMPORT_BYTES:
        raise ValueError("response too large")
    return json.loads(body)


def query_extensions(shell_major, search):
    q = urllib.parse.urlencode({"sort": "popularity", "shell_version": shell_major, "page": 1, "search": search})
    return fetch_json(f"https://extensions.gnome.org/extension-query/?{q}").get("extensions", [])[:15]


def dirs_with(bases, marker):
    """bases 底下含有 marker 的子資料夾名稱，用來列出已安裝的主題。"""
    names = set()
    for base in bases:
        try:
            for d in os.scandir(os.path.expanduser(base)):
                if d.is_dir() and d.name != "default" and os.path.exists(os.path.join(d.path, marker)):
                    names.add(d.name)
        except OSError:
            pass
    return [(n, n) for n in sorted(names, key=str.lower)]


ICON_DIRS = ("/usr/share/icons", "~/.local/share/icons", "~/.icons")
THEME_DIRS = ("/usr/share/themes", "~/.local/share/themes", "~/.themes")


def icon_themes():
    cursors_only = {n for n, _v in dirs_with(ICON_DIRS, "cursors")} - {n for n, _v in dirs_with(ICON_DIRS, "scalable")} - {n for n, _v in dirs_with(ICON_DIRS, "16x16")}
    return [(n, v) for n, v in dirs_with(ICON_DIRS, "index.theme") if n not in cursors_only]


# ---------- 調校項定義 ----------
# 每一項是 (類型, 標題, schema, key, 額外參數)。系統上不存在的 schema 或 key 會自動略過。
# reader(系統值) -> 控制項的值；writer(控制項的值, 目前的系統值) -> 要寫入的系統值。
# 多個項目可以共用同一個 key（例如標題列按鈕），所以 writer 會拿到目前的值來修改。

def sw(title, schema, key, reader=None, writer=None):
    return ("switch", title, schema, key, (reader, writer))


def en(title, schema, key, options):
    """options: [(標籤, 值)] 或回傳它的函式。"""
    return ("enum", title, schema, key, options)


def ch(title, schema, key, labels, reader, writer):
    return ("choice", title, schema, key, (labels, reader, writer))


def sp(title, schema, key, lo, hi, step):
    return ("spin", title, schema, key, (lo, hi, step))


def parse_layout(layout):
    left, _sep, right = layout.partition(":")
    names = [x for x in f"{left},{right}".split(",") if x]
    return {"left": "close" in left.split(","), "min": "minimize" in names, "max": "maximize" in names, "menu": "appmenu" in names}


def build_layout(p):
    buttons = [name for name, on in (("minimize", p["min"]), ("maximize", p["max"])) if on]
    menu = ["appmenu"] if p["menu"] else []
    if p["left"]:
        return ",".join(["close"] + buttons) + ":" + ",".join(menu)
    return ",".join(menu) + ":" + ",".join(buttons + ["close"])


def layout_switch(title, field):
    return sw(title, WM, "button-layout", lambda v: parse_layout(v)[field], lambda on, cur: build_layout({**parse_layout(cur), field: on}))


def xkb_choice(title, labels, values, prefixes):
    """xkb-options 裡同一類的選項只能擇一；values[0] 是空字串，代表不設定。"""
    def reader(options):
        return next((i for i, o in enumerate(values) if o and o in options), 0)

    def writer(i, options):
        kept = [o for o in options if not o.startswith(prefixes)]
        return kept + ([values[i]] if values[i] else [])
    return ch(title, "org.gnome.desktop.input-sources", "xkb-options", labels, reader, writer)


def xkb_switch(title, option):
    return sw(title, "org.gnome.desktop.input-sources", "xkb-options", lambda options: option in options,
              lambda on, options: [o for o in options if o != option] + ([option] if on else []))


ACCENTS = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800", "orange": "#ed5b00",
           "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac", "slate": "#6f8396", "brown": "#986a44"}
IF, WM, MUTTER = "org.gnome.desktop.interface", "org.gnome.desktop.wm.preferences", "org.gnome.mutter"
DOCK, DING = "org.gnome.shell.extensions.dash-to-dock", "org.gnome.shell.extensions.ding"
MOUSE, TOUCHPAD, KEYBOARD = "org.gnome.desktop.peripherals.mouse", "org.gnome.desktop.peripherals.touchpad", "org.gnome.desktop.peripherals.keyboard"
POWER, SOUND, PRIVACY, BACKGROUND = "org.gnome.settings-daemon.plugins.power", "org.gnome.desktop.sound", "org.gnome.desktop.privacy", "org.gnome.desktop.background"
TITLEBAR_ACTIONS = [("最大化", "toggle-maximize"), ("水平最大化", "toggle-maximize-horizontally"), ("垂直最大化", "toggle-maximize-vertically"),
                    ("最小化", "minimize"), ("捲起", "toggle-shade"), ("移到最下層", "lower"), ("選單", "menu"), ("無動作", "none")]
ACCEL = [("預設", "default"), ("平坦", "flat"), ("自適應", "adaptive")]
IDLE_ACTIONS = [("暫停", "suspend"), ("休眠", "hibernate"), ("關機", "shutdown"), ("登出", "logout"), ("關閉螢幕", "blank"), ("不執行動作", "nothing")]

# 某個設定只在另一個設定符合條件時才有作用：{被控制的 key: (控制它的 key, 條件)}
DEPENDS = {
    (IF, "font-rgba-order"): ((IF, "font-antialiasing"), lambda v: v == "rgba"),
    (WM, "num-workspaces"): ((MUTTER, "dynamic-workspaces"), lambda v: not v),
}

TWEAKS = [  # (分類, 圖示, [(群組, [項目])])；群組可以多帶一個說明：(群組, 說明, [項目])
    ("外觀", "applications-graphics-symbolic", [
        ("樣式", [
            en("色彩模式", IF, "color-scheme", [("預設", "default"), ("深色", "prefer-dark"), ("淺色", "prefer-light")]),
            ("accent", "強調色", IF, "accent-color", None),
            en("圖示主題", IF, "icon-theme", icon_themes),
            en("游標主題", IF, "cursor-theme", lambda: dirs_with(ICON_DIRS, "cursors")),
            sp("游標大小", IF, "cursor-size", 16, 96, 8),
            en("舊版應用程式主題", IF, "gtk-theme", lambda: dirs_with(THEME_DIRS, "gtk-3.0")),
            en("Shell 主題", "org.gnome.shell.extensions.user-theme", "name", lambda: [("預設", "")] + dirs_with(THEME_DIRS, "gnome-shell")),
            sw("動畫效果", IF, "enable-animations"),
        ]),
        ("桌布", [
            ("image", "桌布", BACKGROUND, "picture-uri", None),
            ("image", "深色模式的桌布", BACKGROUND, "picture-uri-dark", None),
            en("顯示方式", BACKGROUND, "picture-options", [("縮放填滿", "zoom"), ("等比縮放", "scaled"), ("置中", "centered"), ("延展", "stretched"),
                                                        ("拼貼", "wallpaper"), ("跨螢幕", "spanned"), ("不顯示", "none")]),
        ]),
    ]),
    ("字型", "font-x-generic-symbolic", [
        ("字型", [
            ("font", "介面字型", IF, "font-name", None),
            ("font", "文件字型", IF, "document-font-name", None),
            ("font", "等寬字型", IF, "monospace-font-name", None),
            ("font", "舊版視窗標題字型", WM, "titlebar-font", None),
        ]),
        ("算繪", "OLED 或次像素不是直條排列的螢幕（例如 QD-OLED），反鋸齒建議選「標準（灰階）」，否則文字邊緣會有彩邊", [
            en("微調", IF, "font-hinting", [("完整", "full"), ("中等", "medium"), ("輕微", "slight"), ("無", "none")]),
            en("反鋸齒", IF, "font-antialiasing", [("次像素（LCD 螢幕）", "rgba"), ("標準（灰階）", "grayscale"), ("無", "none")]),
            en("次像素順序", IF, "font-rgba-order", [("RGB", "rgb"), ("BGR", "bgr"), ("垂直 RGB", "vrgb"), ("垂直 BGR", "vbgr")]),
            sp("縮放係數", IF, "text-scaling-factor", 0.5, 3.0, 0.05),
        ]),
    ]),
    ("視窗", "view-restore-symbolic", [
        ("標題列按鈕", [
            ch("位置", WM, "button-layout", ["左", "右"], lambda v: 0 if parse_layout(v)["left"] else 1,
               lambda i, cur: build_layout({**parse_layout(cur), "left": i == 0})),
            layout_switch("最大化", "max"),
            layout_switch("最小化", "min"),
        ]),
        ("標題列動作", [
            en("雙擊", WM, "action-double-click-titlebar", TITLEBAR_ACTIONS),
            en("中鍵點擊", WM, "action-middle-click-titlebar", TITLEBAR_ACTIONS),
            en("右鍵點擊", WM, "action-right-click-titlebar", TITLEBAR_ACTIONS),
        ]),
        ("行為", [
            sw("對話框附著於父視窗", MUTTER, "attach-modal-dialogs"),
            sw("邊緣貼齊", MUTTER, "edge-tiling"),
            sw("新視窗置中", MUTTER, "center-new-windows"),
            sw("以右鍵調整視窗大小", WM, "resize-with-right-button"),
            en("視窗動作按鍵", WM, "mouse-button-modifier", [("Super", "<Super>"), ("Alt", "<Alt>"), ("停用", "disabled")]),
            en("視窗焦點", WM, "focus-mode", [("點擊取得焦點", "click"), ("滑過取得焦點", "sloppy"), ("焦點跟隨滑鼠", "mouse")]),
            sw("取得焦點時提升視窗", WM, "auto-raise"),
        ]),
    ]),
    ("工作區", "view-paged-symbolic", [
        ("工作區", [
            sw("動態工作區", MUTTER, "dynamic-workspaces"),
            sp("固定工作區的數量", WM, "num-workspaces", 1, 36, 1),
            sw("僅在主螢幕使用工作區", MUTTER, "workspaces-only-on-primary"),
        ]),
    ]),
    ("頂部列與 Dock", "view-grid-symbolic", [
        ("頂部列", [
            sw("時鐘顯示星期", IF, "clock-show-weekday"),
            sw("時鐘顯示日期", IF, "clock-show-date"),
            sw("時鐘顯示秒數", IF, "clock-show-seconds"),
            en("時鐘格式", IF, "clock-format", [("24 小時", "24h"), ("12 小時", "12h")]),
            sw("行事曆顯示週數", "org.gnome.desktop.calendar", "show-weekdate"),
            sw("顯示電量百分比", IF, "show-battery-percentage"),
            sw("活動總覽熱角", IF, "enable-hot-corners"),
        ]),
        ("Dock", [
            en("位置", DOCK, "dock-position", [("左", "LEFT"), ("下", "BOTTOM"), ("右", "RIGHT")]),
            sp("圖示大小", DOCK, "dash-max-icon-size", 16, 64, 2),
            sw("永遠顯示", DOCK, "dock-fixed"),
            sw("延伸到螢幕邊緣", DOCK, "extend-height"),
            sw("自動隱藏", DOCK, "autohide"),
            sw("顯示垃圾桶", DOCK, "show-trash"),
            sw("顯示掛載的裝置", DOCK, "show-mounts"),
            en("點擊圖示的動作", DOCK, "click-action", [("聚焦或顯示視窗", "focus-or-appspread"), ("最小化", "minimize"), ("最小化或總覽", "minimize-or-overview"),
                                                      ("輪流切換視窗", "cycle-windows"), ("預覽", "previews"), ("聚焦或預覽", "focus-or-previews")]),
        ]),
        ("桌面圖示", [
            sw("顯示家目錄", DING, "show-home"),
            sw("顯示垃圾桶", DING, "show-trash"),
            en("圖示大小", DING, "icon-size", [("極小", "tiny"), ("小", "small"), ("標準", "standard"), ("大", "large")]),
            en("新圖示的起始角落", DING, "start-corner", [("左上", "top-left"), ("右上", "top-right"), ("左下", "bottom-left"), ("右下", "bottom-right")]),
        ]),
    ]),
    ("鍵盤與滑鼠", "input-keyboard-symbolic", [
        ("鍵盤", [
            sw("顯示所有輸入來源", "org.gnome.desktop.input-sources", "show-all-sources"),
            sw("Emacs 輸入模式", IF, "gtk-key-theme", lambda v: v == "Emacs", lambda on, _cur: "Emacs" if on else "Default"),
            en("總覽快捷鍵", MUTTER, "overlay-key", [("左 Super", "Super_L"), ("右 Super", "Super_R")]),
            xkb_choice("Caps Lock 的行為", ["預設", "額外的 Ctrl", "Esc", "與 Esc 交換", "停用"],
                       ["", "caps:ctrl_modifier", "caps:escape", "caps:swapescape", "caps:none"], ("caps:",)),
            xkb_choice("Compose 鍵", ["停用", "右 Alt", "右 Ctrl", "選單鍵", "Caps Lock"],
                       ["", "compose:ralt", "compose:rctrl", "compose:menu", "compose:caps"], ("compose:",)),
            xkb_switch("交換 Alt 與 Super", "altwin:swap_alt_win"),
            sp("按鍵重複延遲（毫秒）", KEYBOARD, "delay", 100, 2000, 50),
            sp("按鍵重複間隔（毫秒）", KEYBOARD, "repeat-interval", 10, 100, 1),
        ]),
        ("滑鼠", [
            sw("中鍵貼上", IF, "gtk-enable-primary-paste"),
            sw("按 Ctrl 顯示指標位置", IF, "locate-pointer"),
            en("加速設定檔", MOUSE, "accel-profile", ACCEL),
            sp("指標速度", MOUSE, "speed", -1.0, 1.0, 0.1),
            sw("自然捲動", MOUSE, "natural-scroll"),
            sw("左手模式", MOUSE, "left-handed"),
        ]),
        ("觸控板", [
            sw("打字時停用", TOUCHPAD, "disable-while-typing"),
            en("點擊模擬", TOUCHPAD, "click-method", [("預設", "default"), ("手指數量", "fingers"), ("區域", "areas"), ("停用", "none")]),
            en("加速設定檔", TOUCHPAD, "accel-profile", ACCEL),
            sp("指標速度", TOUCHPAD, "speed", -1.0, 1.0, 0.1),
            sw("自然捲動", TOUCHPAD, "natural-scroll"),
            sw("輕觸點擊", TOUCHPAD, "tap-to-click"),
            sw("雙指捲動", TOUCHPAD, "two-finger-scrolling-enabled"),
        ]),
    ]),
    ("音效", "audio-speakers-symbolic", [
        ("音效", [
            en("系統音效主題", SOUND, "theme-name", lambda: dirs_with(("/usr/share/sounds", "~/.local/share/sounds"), "index.theme")),
            sw("事件音效", SOUND, "event-sounds"),
            sw("音量可超過 100%", SOUND, "allow-volume-above-100-percent"),
        ]),
    ]),
    ("電源", "battery-symbolic", [
        ("電源", [
            en("螢幕關閉時間", "org.gnome.desktop.session", "idle-delay", [("1 分鐘", 60), ("5 分鐘", 300), ("10 分鐘", 600), ("15 分鐘", 900), ("永不", 0)]),
            sw("閒置時調暗螢幕", POWER, "idle-dim"),
            en("接上電源閒置時", POWER, "sleep-inactive-ac-type", IDLE_ACTIONS),
            en("使用電池閒置時", POWER, "sleep-inactive-battery-type", IDLE_ACTIONS),
            en("電源鍵", POWER, "power-button-action", [("暫停", "suspend"), ("休眠", "hibernate"), ("詢問", "interactive"), ("不執行動作", "nothing")]),
        ]),
    ]),
    ("隱私", "preferences-system-privacy-symbolic", [
        ("紀錄", [
            sw("記錄最近開啟的檔案", PRIVACY, "remember-recent-files"),
            sw("自動清除暫存檔", PRIVACY, "remove-old-temp-files"),
            sw("自動清空垃圾桶", PRIVACY, "remove-old-trash-files"),
            sp("幾天後清除", PRIVACY, "old-files-age", 1, 365, 1),
            sw("傳送診斷資料給 Canonical", PRIVACY, "report-technical-problems"),
        ]),
        ("裝置與鎖定", [
            sw("定位服務", "org.gnome.system.location", "enabled"),
            sw("停用攝影機", PRIVACY, "disable-camera"),
            sw("停用麥克風", PRIVACY, "disable-microphone"),
            sw("自動鎖定螢幕", "org.gnome.desktop.screensaver", "lock-enabled"),
            sw("顯示通知橫幅", "org.gnome.desktop.notifications", "show-banners"),
            sw("鎖定畫面顯示通知", "org.gnome.desktop.notifications", "show-in-lock-screen"),
        ]),
    ]),
]
QUICK = [
    sw("深色模式", IF, "color-scheme", lambda v: v == "prefer-dark", lambda on, _cur: "prefer-dark" if on else "default"),
    sw("夜間護眼", "org.gnome.settings-daemon.plugins.color", "night-light-enabled"),
    sw("動畫效果", IF, "enable-animations"),
]
MODULES = [
    ("擴充功能管理", "E", True, "安裝、啟用、解除安裝、設定 GNOME 擴充功能"),
    ("系統調校", "T", True, "GNOME Tweaks 的全部功能，加上變更預覽與還原"),
    ("設定檔與歷史", "P", True, "打包、分享、還原整套設定"),
    ("啟動應用程式", "S", True, "登入時自動啟動的程式"),
    ("備份與還原", "B", True, "系統還原點、個人檔案備份、完整映像檔的引導"),
    ("應用程式", "A", False, "統一管理 apt、Snap、Flatpak，附來源與大小"),
    ("系統清理", "C", True, "舊核心、快取、孤立套件與日誌，先預覽再清"),
    ("驅動與硬體", "D", True, "電池健康、韌體版本、建議的驅動程式"),
    ("外掛 SDK", "+", False, "讓社群以 Python / JS 寫自己的模組並分享"),
]
SIDEBAR = (  # (id, 標題, 圖示)；None = 分隔線。調校分類直接由 TWEAKS 產生
    [("dash", "總覽", "go-home-symbolic"), ("ext", "擴充功能", "application-x-addon-symbolic"), None]
    + [(f"tw{i}", name, icon) for i, (name, icon, _groups) in enumerate(TWEAKS)]
    + [("startup", "啟動應用程式", "system-run-symbolic"), None,
       ("cleanup", "系統清理", "user-trash-symbolic"), ("hardware", "驅動與硬體", "computer-symbolic"),
       ("backup", "備份與還原", "drive-harddisk-symbolic"), None,
       ("pf", "設定檔", "emblem-favorite-symbolic"), ("hist", "歷史", "document-open-recent-symbolic"), None,
       ("mods", "模組中心", "view-app-grid-symbolic")]
)
def all_items():
    return [item for _n, _i, groups in TWEAKS for g in groups for item in g[-1]]


ALL_KEYS = list(dict.fromkeys((item[2], item[3]) for item in all_items()))
KEY_TITLES = {}
for _item in all_items():
    KEY_TITLES.setdefault((_item[2], _item[3]), _item[1])
KEY_TITLES[store.MASTER] = "全部擴充功能"


def esc(s):
    return GLib.markup_escape_text(s)


def same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) < 1e-6
    return a == b


def tile(text, i):
    lb = Gtk.Label(label=(text or "?")[0].upper())
    lb.add_css_class("tile")
    lb.add_css_class(f"t{i % len(COLORS)}")
    lb.set_valign(Gtk.Align.CENTER)
    return lb


def chip(text, kind="neutral"):
    """狀態膠囊（一列最多一個）。"""
    lb = Gtk.Label(label=_(text))
    lb.add_css_class("chip")
    lb.add_css_class(kind)
    lb.set_valign(Gtk.Align.CENTER)
    return lb


def value(text):
    """數值（版本、數量）：灰色小字，無底。來自系統的文字，不翻譯。"""
    lb = Gtk.Label(label=text, ellipsize=3, max_width_chars=28)
    lb.add_css_class("caption")
    lb.add_css_class("dim-label")
    lb.set_valign(Gtk.Align.CENTER)
    return lb


def btn(label, css=None, icon=None, on_click=None):
    b = Gtk.Button(label=_(label)) if label else Gtk.Button(icon_name=icon)
    if css:
        for c in css.split():
            b.add_css_class(c)
    b.set_valign(Gtk.Align.CENTER)
    if on_click:
        b.connect("clicked", lambda *_a: on_click())
    return b


def scroll(w):
    s = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    s.set_child(w)
    return s


def group(title=None, desc=None):
    g = Adw.PreferencesGroup()
    if title:
        g.set_title(_m(title))
    if desc:
        g.set_description(_m(desc))
    return g


def row(title, sub=None, prefix=None, suffix=(), data=False):
    """data=True：標題與副標題是系統資料或已翻譯好的句子，不再經過翻譯。"""
    tr = esc if data else _m
    r = Adw.ActionRow(title=tr(title))
    if sub:
        r.set_subtitle(tr(sub))
    if prefix:
        r.add_prefix(prefix)
    for s in suffix:
        r.add_suffix(s)
    return r


def when(timestamp):
    return GLib.DateTime.new_from_unix_local(timestamp).format("%Y-%m-%d %H:%M")


class Tuneup(Adw.Application):
    def __init__(self, shot_dir=None, lang=None):
        super().__init__(application_id="io.github.doeshing.Tuneup")
        self.shot_dir = shot_dir
        self.lang_pref = lang or read_pref()  # --lang 只影響這次執行，不會存檔
        load_lang(self.lang_pref)
        self.pending = {}    # (schema, key) -> 要寫入的值，或 RESET
        self.key_rows = {}   # (schema, key) -> [(show, reset_button)]
        self.key_widgets = {}  # (schema, key) -> [列]
        self.loading = False
        self.search_text = ""

    # ---------- 共用對話框 ----------
    def confirm(self, heading, body, action_label, on_yes, destructive=False):
        d = Adw.AlertDialog(heading=_(heading), body=body)
        d.add_response("cancel", _("取消"))
        d.add_response("ok", _(action_label))
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", lambda _d, r: r == "ok" and on_yes())
        d.present(self.win)

    def prompt(self, heading, label, action_label, on_text):
        entry = Adw.EntryRow(title=_(label))
        box = Gtk.ListBox()
        box.add_css_class("boxed-list")
        box.append(entry)
        d = Adw.AlertDialog(heading=_(heading), extra_child=box)
        d.add_response("cancel", _("取消"))
        d.add_response("ok", _(action_label))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", lambda _d, r: r == "ok" and entry.get_text().strip() and on_text(entry.get_text().strip()))
        d.present(self.win)

    def param_menu(self, items):
        """items: [(標籤, action 名稱, 字串參數)]"""
        m = Gio.Menu()
        for label, action, param in items:
            it = Gio.MenuItem.new(_(label), None)
            it.set_action_and_target_value(f"app.{action}", GLib.Variant("s", param))
            m.append_item(it)
        b = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=m, valign=Gtk.Align.CENTER)
        b.add_css_class("flat")
        return b

    def next_icon(self):
        return Gtk.Image(icon_name="go-next-symbolic")

    def nav_row(self, title, sub, target, suffix=None):
        r = row(_(title), sub, data=True)
        if suffix:
            r.add_suffix(suffix)
        r.add_suffix(self.next_icon())
        r.set_activatable(True)
        r.connect("activated", lambda *_a: self.goto(target))
        return r

    def reload(self, page=None):
        self.build_ui(page or self.stack.get_visible_child_name())

    def ext_states(self):
        return {e["uuid"]: e["state"] == EXT_STATE_ACTIVE for e in self.exts}

    # ---------- 未套用的變更 ----------
    def shown_value(self, schema, key):
        """畫面上應該顯示的值：有未套用的變更就顯示它，否則是系統目前的值。"""
        st = store.settings(schema)
        staged = self.pending.get((schema, key))
        if staged is RESET:
            return st.get_default_value(key).unpack()
        return st.get_value(key).unpack() if staged is None else staged

    def stage(self, schema, key, new):
        if self.loading:
            return
        st = store.settings(schema)
        k = (schema, key)
        if new is RESET:
            if st.get_user_value(key) is None:
                self.pending.pop(k, None)
            else:
                self.pending[k] = RESET
        elif same(new, st.get_value(key).unpack()):
            self.pending.pop(k, None)
        else:
            self.pending[k] = new
        self.refresh_key(k)
        self.update_depends()
        self.update_banner()

    def refresh_key(self, k):
        staged = self.pending.get(k)
        customized = staged is not RESET and (staged is not None or store.settings(k[0]).get_user_value(k[1]) is not None)
        self.loading = True
        for show, reset in self.key_rows.get(k, []):
            show(self.shown_value(*k))
            reset.set_visible(customized)
        self.loading = False

    def update_depends(self):
        """依 DEPENDS 停用目前沒有作用的列。"""
        for k, (controller, condition) in DEPENDS.items():
            if store.has_key(*controller):
                for w in self.key_widgets.get(k, []):
                    w.set_sensitive(condition(self.shown_value(*controller)))

    def update_banner(self):
        n = len(self.pending)
        self.banner.set_title(_m("{n} 項未套用").format(n=n))
        self.banner.set_revealed(n > 0)

    def apply_pending(self):
        changes = []
        for (schema, key), new in self.pending.items():
            text = None if new is RESET else store.to_variant(schema, key, new).print_(False)
            changes.append(store.gsettings_change(schema, key, text))
        self.commit("tweak", changes, "已套用，可在「歷史」還原")

    def commit(self, action, changes, message, label="", page=None):
        """所有寫入系統的動作都經過這裡：寫入、記錄、重新載入畫面。"""
        try:
            entry = store.apply(action, changes, label)
        except GLib.Error as e:
            self.reload(page)
            self.toast_raw(_("無法套用：{error}").format(error=e.message))
            return
        self.pending = {}
        self.reload(page)
        self.toast(message if entry else "沒有需要變更的項目")

    def discard_pending(self):
        self.pending = {}
        self.reload()

    def customized(self):
        """各分類中，使用者改過（與預設值不同）的調校項數量。"""
        counts = []
        for name, _icon, groups in TWEAKS:
            keys = {(item[2], item[3]) for g in groups for item in g[-1]}
            counts.append((name, sum(1 for s, k in keys if store.has_key(s, k) and store.settings(s).get_user_value(k) is not None)))
        return counts

    # ---------- 總覽 ----------
    def page_dash(self):
        p = Adw.PreferencesPage()
        g = group()
        logo = Gtk.Image(icon_name="distributor-logo", pixel_size=32)
        logo.add_css_class("logo")
        g.add(row(self.sys_name, self.sys_detail, logo, data=True))
        p.add(g)

        active = sum(1 for e in self.exts if e["state"] == EXT_STATE_ACTIVE)
        updates = [e for e in self.exts if e["has_update"]]
        broken = [e for e in self.exts if e["state"] in (EXT_STATE_ERROR, EXT_STATE_OUT_OF_DATE)]
        counts = self.customized()
        history = store.load_history()
        matching = [pf["name"] for pf in self.profiles if not self.profile_diff(pf)]
        g = group("概覽")
        g.add(self.nav_row("擴充功能", _("已安裝 {n} 個，{m} 個啟用").format(n=len(self.exts), m=active), "ext",
                           chip(_("{n} 個可更新").format(n=len(updates)), "warning") if updates else None))
        detail = " · ".join(f"{_(name)} {n}" for name, n in counts if n) or _("全部為預設值")
        g.add(self.nav_row("已自訂的調校項", detail, "tw0", value(str(sum(n for _name, n in counts)))))
        if matching:
            pf_sub = matching[0]
        else:
            pf_sub = _("目前的狀態不符合任何設定檔") if self.profiles else _("尚未建立設定檔")
        g.add(self.nav_row("目前設定檔", pf_sub, "pf"))
        hist_sub = _("{n} 筆紀錄，最近一次在 {time}").format(n=len(history), time=when(history[-1]["time"])) if history else _("尚無紀錄")
        g.add(self.nav_row("歷史", hist_sub, "hist"))
        p.add(g)

        g = group("待處理")
        for e in broken:
            title = _("{name} 發生錯誤").format(name=e["name"]) if e["state"] == EXT_STATE_ERROR else _("{name} 與目前的 GNOME Shell 不相容").format(name=e["name"])
            g.add(row(title, e["error"] or e["uuid"], Gtk.Image(icon_name="dialog-error-symbolic"), [btn("檢視", on_click=lambda: self.goto("ext"))], data=True))
        if updates:
            g.add(row(_("{n} 個擴充功能有新版本").format(n=len(updates)), ", ".join(e["name"] for e in updates),
                      Gtk.Image(icon_name="folder-download-symbolic"), [btn("檢視", on_click=lambda: self.goto("ext"))], data=True))
        if not broken and not updates:
            g.add(row("沒有待處理的事項", "擴充功能都正常運作，也沒有可用的更新", Gtk.Image(icon_name="emblem-ok-symbolic")))
        p.add(g)

        g = group("快速開關")
        for item in QUICK:
            w = self.tweak_row(item)
            if w:
                g.add(w)
        p.add(g)
        return p

    # ---------- 擴充功能 ----------
    def sub_page(self, title, content):
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        tv.set_content(content)
        self.mark_rows(tv)
        return Adw.NavigationPage(title=title, child=tv)

    def ext_chip(self, e):
        if e["state"] == EXT_STATE_ERROR:
            return chip("錯誤", "error")
        if e["state"] == EXT_STATE_OUT_OF_DATE:
            return chip("不相容", "error")
        if e["has_update"]:
            return chip("有更新", "warning")
        return None

    def toggle_extension(self, e, want):
        if self.loading or want == (e["state"] == EXT_STATE_ACTIVE):
            return
        message = _("已啟用 {name}").format(name=e["name"]) if want else _("已停用 {name}").format(name=e["name"])
        try:
            store.apply("extension", [store.extension_change(e["uuid"], e["name"], not want, want)])
        except GLib.Error as err:
            message = _("無法套用：{error}").format(error=err.message)
        # GNOME Shell 需要一點時間更新狀態，稍後再重新讀取
        GLib.timeout_add(400, lambda: (self.reload("ext"), self.toast_raw(message), False)[2])

    def set_master(self, enabled):
        if self.loading:
            return
        text = store.to_variant(*store.MASTER, not enabled).print_(False)
        self.commit("tweak", [store.gsettings_change(*store.MASTER, text)],
                    "已恢復擴充功能" if enabled else "已暫停所有擴充功能", page="ext")

    def uninstall_extension(self, e):
        def run():
            try:
                store.shell_call("UninstallExtension", GLib.Variant("(s)", (e["uuid"],)))
                message = _("已解除安裝 {name}").format(name=e["name"])
            except GLib.Error as err:
                message = _("無法套用：{error}").format(error=err.message)
            GLib.timeout_add(400, lambda: (self.reload("ext"), self.toast_raw(message), False)[2])
        self.confirm("解除安裝這個擴充功能？", _("{name} 的檔案會被移除，這個動作無法從歷史還原。").format(name=e["name"]), "解除安裝", run, destructive=True)

    def install_extension(self, uuid, name, button):
        """交給 GNOME Shell 下載安裝；Shell 會自己跳出確認視窗。"""
        button.set_sensitive(False)
        button.set_label(_("安裝中…"))

        def finished(bus, result):
            try:
                outcome = bus.call_finish(result).unpack()[0]
            except GLib.Error as err:
                outcome = err.message
            if outcome == "successful":
                self.reload("ext")
                self.toast_raw(_("已安裝 {name}").format(name=name))
            else:
                button.set_sensitive(True)
                button.set_label(_("安裝"))
                self.toast_raw(_("沒有安裝 {name}（{reason}）").format(name=name, reason=outcome))
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        bus.call(store.SHELL_BUS[0], store.SHELL_BUS[1], store.SHELL_BUS[0], "InstallRemoteExtension",
                 GLib.Variant("(s)", (uuid,)), None, Gio.DBusCallFlags.NONE, 120000, None, finished)

    def ext_detail(self, i):
        e = self.exts[i]
        pg = Adw.PreferencesPage()
        g = group()
        r = row(e["name"], e["uuid"], tile(e["name"], i), data=True)
        c = self.ext_chip(e)
        if c:
            r.add_suffix(c)
        if e["version"]:
            r.add_suffix(value(e["version"]))
        g.add(r)
        if e["full_desc"]:
            d = Adw.ActionRow(title=esc(e["full_desc"]), title_lines=0)
            d.add_css_class("wrap")  # 說明全文允許換行，mark_rows 不會把它截成一行
            g.add(d)
        if e["error"]:
            g.add(row(_("錯誤訊息"), e["error"], data=True))
        pg.add(g)

        g = group("設定")
        sw_row = Adw.SwitchRow(title=_m("啟用"), active=e["state"] == EXT_STATE_ACTIVE)
        sw_row.connect("notify::active", lambda w, _p: self.toggle_extension(e, w.get_active()))
        g.add(sw_row)
        if e["has_prefs"]:
            g.add(row("開啟偏好設定", suffix=[btn("開啟", on_click=lambda: subprocess.Popen(["gnome-extensions", "prefs", e["uuid"]]))]))
        pg.add(g)

        g = group("資訊")
        g.add(row(_("來源"), _("系統內建") if e["system"] else _("使用者安裝"), data=True))
        if e["url"]:
            g.add(row(_("網址"), e["url"], data=True))
        if e["shell"]:
            g.add(row(_("支援的 Shell 版本"), ", ".join(e["shell"]), data=True))
        g.add(row(_("路徑"), e["path"], data=True))
        pg.add(g)
        if not e["system"]:
            g = group()
            g.add(row("解除安裝", None, None, [btn("解除安裝", "destructive-action", on_click=lambda: self.uninstall_extension(e))]))
            pg.add(g)
        self.ext_nav.push(self.sub_page(e["name"], pg))

    def check_updates(self):
        try:
            store.shell_call("CheckForUpdates")
            self.toast("已要求 GNOME Shell 檢查更新")
        except GLib.Error as err:
            self.toast_raw(_("無法套用：{error}").format(error=err.message))

    def show_updates(self):
        up = Adw.PreferencesPage()
        g = group()
        g.add(row("檢查更新", "GNOME Shell 會在背景下載，並在下次登入時套用", None, [btn("檢查", "suggested-action", on_click=self.check_updates)]))
        up.add(g)
        updates = [(i, e) for i, e in enumerate(self.exts) if e["has_update"]]
        g = group("等待套用的更新")
        for i, e in updates:
            g.add(row(e["name"], e["version"] or e["uuid"], tile(e["name"], i), [chip("下次登入時套用", "warning")], data=True))
        if not updates:
            g.add(row("全部都是最新版本", "GNOME Shell 沒有回報任何可用的更新", Gtk.Image(icon_name="emblem-ok-symbolic")))
        up.add(g)
        self.ext_nav.push(self.sub_page(_("更新"), up))

    def show_explore(self):
        dp = Adw.PreferencesPage()
        g = group()
        entry = Adw.EntryRow(title=_("搜尋 extensions.gnome.org"), show_apply_button=True)
        g.add(entry)
        dp.add(g)
        results = group("熱門", _("支援 GNOME {v} 的擴充功能").format(v=self.shell_version))
        dp.add(results)
        shown = []
        installed = {e["uuid"] for e in self.exts}

        def fill(items, err):
            for r in shown:
                results.remove(r)
            shown.clear()
            if err:
                shown.append(row(_("無法連線到 extensions.gnome.org"), err, Gtk.Image(icon_name="network-offline-symbolic"), data=True))
            elif not items:
                shown.append(row("找不到符合的擴充功能", "換個關鍵字試試"))
            for i, d in enumerate(items or []):
                uuid, name = d.get("uuid", ""), d.get("name", "")
                if uuid in installed:
                    b = btn("已安裝")
                    b.set_sensitive(False)
                else:
                    b = btn("安裝")
                    b.connect("clicked", lambda button, uuid=uuid, name=name: self.install_extension(uuid, name, button))
                desc = (d.get("description") or "").strip().split("\n")[0]
                shown.append(row(name, desc, tile(name, i), [b], data=True))
            for r in shown:
                results.add(r)
            self.mark_rows(results)

        def search(*_a):
            text = entry.get_text().strip()
            results.set_title(_m("搜尋結果") if text else _m("熱門"))
            fill([], None)
            results.remove(shown.pop())
            shown.append(row("載入中…"))
            results.add(shown[0])
            in_background(lambda: query_extensions(self.shell_version, text), fill)

        entry.connect("apply", search)
        entry.connect("entry-activated", search)
        search()
        self.ext_nav.push(self.sub_page(_("探索擴充功能"), dp))

    def page_ext(self):
        nav = Adw.NavigationView()
        self.ext_nav = nav
        p = Adw.PreferencesPage()
        g = group()
        master = Adw.SwitchRow(title=_m("全部擴充功能"), subtitle=_m("關閉即進入安全模式"))
        master_on = store.has_key(*store.MASTER) and not store.settings(store.MASTER[0]).get_boolean(store.MASTER[1])
        master.set_active(master_on)
        master.set_sensitive(store.has_key(*store.MASTER))
        master.connect("notify::active", lambda w, _p: self.set_master(w.get_active()))
        g.add(master)
        p.add(g)

        g = group()
        n = sum(1 for e in self.exts if e["has_update"])
        r = row(_("更新"), _("{n} 個擴充功能有新版本").format(n=n) if n else _("全部都是最新版本"), None,
                ([chip(str(n), "warning")] if n else []) + [self.next_icon()], data=True)
        r.set_activatable(True)
        r.connect("activated", lambda *_a: self.show_updates())
        g.add(r)
        r = row("探索", "從 extensions.gnome.org 搜尋與安裝", None, [self.next_icon()])
        r.set_activatable(True)
        r.connect("activated", lambda *_a: self.show_explore())
        g.add(r)
        p.add(g)

        g = group("已安裝")
        if self.ext_error:
            g.add(row(_("無法讀取擴充功能"), self.ext_error, Gtk.Image(icon_name="dialog-error-symbolic"), data=True))
        elif not self.exts:
            g.add(row("尚未安裝任何擴充功能", "可以從「探索」安裝"))
        for i, e in enumerate(self.exts):
            r = row(e["name"], e["desc"] or e["uuid"], tile(e["name"], i), data=True)
            c = self.ext_chip(e)
            if c:
                r.add_suffix(c)
            if e["version"]:
                r.add_suffix(value(e["version"]))
            toggle = Gtk.Switch(active=e["state"] == EXT_STATE_ACTIVE, valign=Gtk.Align.CENTER, sensitive=master_on)
            toggle.connect("notify::active", lambda w, _p, e=e: self.toggle_extension(e, w.get_active()))
            r.add_suffix(toggle)
            r.add_suffix(self.next_icon())
            r.set_activatable(True)
            r.connect("activated", lambda _r, i=i: self.ext_detail(i))
            g.add(r)
        p.add(g)
        nav.add(Adw.NavigationPage(title=_("擴充功能"), child=p))
        return nav

    # ---------- 系統調校 ----------
    def tweak_row(self, item):
        """依目前的值建立一列；系統沒有這個 key 時回傳 None。"""
        kind, title, schema, key, extra = item
        if not store.has_key(schema, key):
            return None
        k = (schema, key)
        v = self.shown_value(schema, key)
        sub = esc(f"{schema} {key}")

        if kind == "switch":
            reader, writer = extra
            w = Adw.SwitchRow(title=_m(title), subtitle=sub, active=reader(v) if reader else v)
            w.connect("notify::active", lambda w, _p: self.stage(schema, key, writer(w.get_active(), self.shown_value(schema, key)) if writer else w.get_active()))

            def show(val):
                w.set_active(reader(val) if reader else val)
        elif kind == "choice":
            labels, reader, writer = extra
            w = Adw.ComboRow(title=_m(title), subtitle=sub, model=Gtk.StringList.new([_(x) for x in labels]), selected=reader(v))
            w.connect("notify::selected", lambda w, _p: self.stage(schema, key, writer(w.get_selected(), self.shown_value(schema, key))))

            def show(val):
                w.set_selected(reader(val))
        elif kind == "enum":
            options = list(extra() if callable(extra) else extra)
            values = [val for _label, val in options]
            for extra_value in (v, store.settings(schema).get_default_value(key).unpack()):
                if extra_value not in values:  # 目前值或預設值不在預設選項中，照實列出
                    options.append((str(extra_value), extra_value))
                    values.append(extra_value)
            w = Adw.ComboRow(title=_m(title), subtitle=sub, model=Gtk.StringList.new([_(label) for label, _val in options]), selected=values.index(v))
            w.connect("notify::selected", lambda w, _p: self.stage(schema, key, values[w.get_selected()]))

            def show(val):
                if val in values:
                    w.set_selected(values.index(val))
        elif kind == "spin":
            lo, hi, step = extra
            whole = isinstance(step, int)
            w = Adw.SpinRow.new_with_range(lo, hi, step)
            w.set_title(_m(title))
            w.set_subtitle(sub)
            w.set_digits(0 if whole else 2)
            w.set_value(v)
            w.connect("notify::value", lambda w, _p: self.stage(schema, key, int(round(w.get_value())) if whole else round(w.get_value(), 2)))

            def show(val):
                w.set_value(val)
        elif kind == "accent":
            w = Adw.ActionRow(title=_m(title), subtitle=sub)
            names = store.settings(schema).props.settings_schema.get_key(key).get_range().unpack()[1]
            sbox = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            buttons = {}
            for name in names:
                t = Gtk.ToggleButton(tooltip_text=name, group=next(iter(buttons.values()), None))
                t.add_css_class("swatch")
                t.add_css_class("circular")
                cp = Gtk.CssProvider()
                cp.load_from_string(f"button{{background:{ACCENTS.get(name, '#888')};}}")
                t.get_style_context().add_provider(cp, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                t.set_active(name == v)
                t.connect("toggled", lambda b, name=name: b.get_active() and self.stage(schema, key, name))
                buttons[name] = t
                sbox.append(t)
            w.add_suffix(sbox)

            def show(val):
                if val in buttons:
                    buttons[val].set_active(True)
        elif kind == "font":
            w = Adw.ActionRow(title=_m(title), subtitle=sub)
            fb = Gtk.FontDialogButton(dialog=Gtk.FontDialog(), valign=Gtk.Align.CENTER, font_desc=Pango.FontDescription.from_string(v))
            fb.connect("notify::font-desc", lambda b, _p: self.stage(schema, key, b.get_font_desc().to_string()))
            w.add_suffix(fb)

            def show(val):
                fb.set_font_desc(Pango.FontDescription.from_string(val))
        else:  # image：選擇圖片檔，值是 file:// 網址
            w = Adw.ActionRow(title=_m(title), subtitle=sub)
            name = value(os.path.basename(urllib.parse.unquote(v)))
            w.add_suffix(name)

            def picked(dialog, result):
                try:
                    self.stage(schema, key, dialog.open_finish(result).get_uri())
                except GLib.Error:
                    pass  # 使用者取消選擇
            w.add_suffix(btn("選擇…", on_click=lambda: Gtk.FileDialog(default_filter=Gtk.FileFilter(mime_types=["image/*"])).open(self.win, None, picked)))

            def show(val):
                name.set_label(os.path.basename(urllib.parse.unquote(val)))

        reset = btn(None, "flat", "edit-undo-symbolic", on_click=lambda: self.stage(schema, key, RESET))
        reset.set_tooltip_text(_("還原為預設值"))
        w.add_suffix(reset)
        self.key_rows.setdefault(k, []).append((show, reset))
        self.key_widgets.setdefault(k, []).append(w)
        staged = self.pending.get(k)
        reset.set_visible(staged is not RESET and (staged is not None or store.settings(schema).get_user_value(key) is not None))
        return w

    def tweak_page(self, nid, groups):
        p = Adw.PreferencesPage()
        for gt, *desc, items in groups:
            rows = [w for w in (self.tweak_row(item) for item in items) if w]
            if not rows:
                continue
            self.page_words[nid] += [_(item[1]).lower() for item in items] + [item[3] for item in items]
            self.page_keys.setdefault(nid, []).extend((item[2], item[3]) for item in items if store.has_key(item[2], item[3]))
            g = group(gt, *desc)
            for w in rows:
                g.add(w)
            p.add(g)
        return p

    # ---------- 啟動應用程式 ----------
    def autostart_entries(self):
        entries = []
        if AUTOSTART_DIR.is_dir():
            for path in sorted(AUTOSTART_DIR.glob("*.desktop")):
                info = Gio.DesktopAppInfo.new_from_filename(str(path))
                entries.append((path, info.get_name() if info else path.stem, (info.get_commandline() or "") if info else ""))
        return entries

    def add_autostart(self, info, dialog):
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy(info.get_filename(), AUTOSTART_DIR / os.path.basename(info.get_filename()))
            message = _("已加入 {name}").format(name=info.get_name())
        except OSError as err:
            message = _("無法套用：{error}").format(error=str(err))
        dialog.close()
        self.reload("startup")
        self.toast_raw(message)

    def choose_autostart(self):
        d = Adw.Dialog(title=_("選擇應用程式"), content_width=420, content_height=560)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        search = Gtk.SearchEntry(placeholder_text=_("搜尋"), margin_start=12, margin_end=12, margin_bottom=6)
        tv.add_top_bar(search)
        lb = Gtk.ListBox(margin_start=12, margin_end=12, margin_bottom=12, margin_top=6, valign=Gtk.Align.START)
        lb.add_css_class("boxed-list")
        existing = {path.name for path, _n, _c in self.autostart_entries()}
        apps = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_filename() and os.path.basename(a.get_filename()) not in existing]
        for info in sorted(apps, key=lambda a: a.get_name().lower()):
            r = Adw.ActionRow(title=esc(info.get_name()), activatable=True)
            icon = info.get_icon()
            if icon:
                r.add_prefix(Gtk.Image(gicon=icon, pixel_size=32))
            r.connect("activated", lambda _r, info=info: self.add_autostart(info, d))
            lb.append(r)
        lb.set_filter_func(lambda r: search.get_text().strip().lower() in r.get_title().lower())
        search.connect("search-changed", lambda *_a: lb.invalidate_filter())
        tv.set_content(scroll(lb))
        d.set_child(tv)
        d.present(self.win)

    def remove_autostart(self, path, name):
        def run():
            try:
                Gio.File.new_for_path(str(path)).trash(None)
                message = _("已移除 {name}，檔案在垃圾桶").format(name=name)
            except GLib.Error as err:
                message = _("無法套用：{error}").format(error=err.message)
            self.reload("startup")
            self.toast_raw(message)
        self.confirm("不再於登入時啟動？", _("{name} 的啟動項目會移到垃圾桶。").format(name=name), "移除", run, destructive=True)

    def page_startup(self):
        p = Adw.PreferencesPage()
        g = group()
        g.add(row("新增啟動應用程式", "登入後自動開啟", None, [btn("新增", "suggested-action", on_click=self.choose_autostart)]))
        p.add(g)
        g = group("登入時啟動")
        entries = self.autostart_entries()
        for i, (path, name, command) in enumerate(entries):
            g.add(row(name, command or path.name, tile(name, i), [btn("移除", on_click=lambda path=path, name=name: self.remove_autostart(path, name))], data=True))
        if not entries:
            g.add(row("沒有啟動應用程式", "這裡只列出你自己加入的項目，不含系統預設的"))
        p.add(g)
        return p

    # ---------- 系統層級操作（需要確認，部分需要管理員權限） ----------
    def run_system(self, heading, body, action_label, work, page, done_message):
        """確認後在背景執行 work() -> (結束碼, 輸出)，結束時重新載入 page 並回報。"""
        def finished(result, err):
            code = -1 if err else result[0]
            self.reload(page)
            if code == 0:
                self.toast(done_message)
            elif code in system.PKEXEC_DISMISSED:
                self.toast("已取消，沒有變更")
            else:
                self.toast_raw(_("無法套用：{error}").format(error=err or (result[1].strip().splitlines() or [str(code)])[-1]))
        self.confirm(heading, body, action_label, lambda: (self.toast("執行中…"), in_background(work, finished)), destructive=True)

    # ---------- 系統清理 ----------
    CLEANUP = {  # id -> (標題, 說明)
        "thumbnails": ("縮圖快取", "檔案管理員的預覽圖，之後會自動重新產生"),
        "trash": ("垃圾桶", "永久刪除垃圾桶裡的所有檔案"),
        "apt-cache": ("套件下載快取", "已下載的安裝檔；需要管理員權限"),
        "journal": ("系統日誌", "只保留最近 14 天；需要管理員權限"),
        "autoremove": ("不再需要的套件", "含舊核心與孤立的相依套件；需要管理員權限"),
    }

    def page_cleanup(self):
        p = Adw.PreferencesPage()
        g = group()
        total = value("…")
        g.add(row("可釋放的空間", "清理前會先確認；清掉的東西無法從歷史還原", None, [total]))
        p.add(g)
        items = group("項目")
        loading = row("載入中…")
        items.add(loading)
        p.add(items)

        def fill(found, err):
            items.remove(loading)
            if err:
                items.add(row(_("無法讀取"), err, data=True))
                return
            total.set_label(system.human(sum(i["size"] or 0 for i in found)))
            for i in found:
                title, desc = self.CLEANUP[i["id"]]
                amount = _("{n} 個套件").format(n=i["count"]) if i["size"] is None else system.human(i["size"])
                detail = ", ".join(i["packages"][:6]) if i.get("packages") else None
                b = btn("清理", on_click=lambda i=i, title=title, amount=amount, detail=detail: self.run_system(
                    _("清理{name}？").format(name=_(title)), "\n".join(x for x in (amount, detail, _("這個動作無法還原。")) if x),
                    "清理", lambda: system.clean(i["id"]), "cleanup", "已清理"))
                b.set_sensitive(bool(i["size"] or i["count"]))
                items.add(row(title, desc, None, [value(amount), b]))
            self.mark_rows(items)
        in_background(system.cleanup_items, fill)
        return p

    # ---------- 驅動與硬體 ----------
    def page_hardware(self):
        p = Adw.PreferencesPage()
        g = group()
        g.add(row(self.sys_name, self.sys_detail, Gtk.Image(icon_name="computer-symbolic"), data=True))
        p.add(g)
        battery, fw, drv = group("電池"), group("韌體"), group("驅動程式")
        placeholders = []
        for grp in (battery, fw, drv):
            placeholders.append(row("載入中…"))
            grp.add(placeholders[-1])
            p.add(grp)

        def fill(found, err):
            for grp, r in zip((battery, fw, drv), placeholders):
                grp.remove(r)
            if err:
                battery.add(row(_("無法讀取"), err, data=True))
                return
            for b in found["batteries"]:
                full, design = (float(b.get(k, "0").split()[0].replace(",", ".")) for k in ("energy-full", "energy-full-design"))
                health = f"{full / design * 100:.0f}%" if design else b.get("capacity", "")
                name = " ".join(x for x in (b.get("vendor"), b.get("model")) if x) or _("電池")
                detail = _("目前容量 {full}，設計容量 {design}").format(full=b.get("energy-full"), design=b.get("energy-full-design"))
                if b.get("charge-cycles"):
                    detail += " · " + _("充電 {n} 次").format(n=b["charge-cycles"])
                battery.add(row(name, detail, Gtk.Image(icon_name="battery-symbolic"), [value(_("健康度 {p}").format(p=health))], data=True))
            if not found["batteries"]:
                battery.add(row("沒有偵測到電池", "這台電腦可能是桌上型主機"))
            devices, updates = found["firmware"]
            if updates:
                fw.add(row(_("{n} 個裝置有韌體更新").format(n=updates), _("在終端機執行 fwupdmgr update 來安裝"), Gtk.Image(icon_name="software-update-available-symbolic"), data=True))
            for d in devices:
                fw.add(row(d["Name"], d.get("Vendor") or "", None, [value(str(d.get("Version", "")))], data=True))
            if not devices:
                fw.add(row("沒有可回報韌體的裝置", "fwupd 沒有找到支援的裝置"))
            for name in found["drivers"]:
                drv.add(row(name, _("ubuntu-drivers 建議安裝"), data=True))
            if not found["drivers"]:
                drv.add(row("沒有建議安裝的額外驅動程式", "目前的硬體不需要專有驅動"))
            if shutil.which("software-properties-gtk"):
                drv.add(row("管理驅動程式", "開啟系統的「額外驅動程式」", None,
                            [btn("開啟", on_click=lambda: subprocess.Popen(["software-properties-gtk", "--open-tab=4"]))]))
            self.mark_rows(p)
        in_background(lambda: {"batteries": system.batteries(), "firmware": system.firmware(), "drivers": system.drivers()}, fill)
        return p

    # ---------- 備份與還原 ----------
    def install_row(self, name, package, desc):
        b = btn("安裝", on_click=lambda: self.run_system(
            _("安裝 {name}？").format(name=name), _("會以管理員權限執行 apt-get install {package}。").format(package=package),
            "安裝", lambda: system.run_root(["apt-get", "install", "-y", package]), "backup", "已安裝"))
        return row(_("尚未安裝 {name}").format(name=name), _(desc), Gtk.Image(icon_name="dialog-information-symbolic"), [b], data=True)

    def page_backup(self):
        p = Adw.PreferencesPage()
        used, free = system.root_usage()
        tools = system.backup_tools()
        g = group()
        g.add(row("系統碟", _("已使用 {used}，剩餘 {free}").format(used=system.human(used), free=system.human(free)),
                  Gtk.Image(icon_name="drive-harddisk-symbolic"), data=False))
        p.add(g)

        g = group("系統還原點", "備份系統檔案與已安裝的套件，不含個人檔案；系統更新出問題時可以退回")
        if tools["timeshift"]:
            g.add(row("建立還原點", "需要管理員權限，可能要幾分鐘", None, [btn("建立", "suggested-action", on_click=lambda: self.run_system(
                "建立系統還原點？", _("Timeshift 會把目前的系統檔案複製到它設定的備份位置。"), "建立",
                lambda: system.run_root(["timeshift", "--create", "--comments", "Tuneup", "--scripted"], timeout=3600), "backup", "已建立還原點"))]))
            g.add(row("管理還原點", "列出、還原、刪除與排程，在 Timeshift 中進行", None,
                      [btn("開啟", on_click=lambda: subprocess.Popen(["timeshift-launcher"]))]))
        else:
            g.add(self.install_row("Timeshift", "timeshift", "Tuneup 透過 Timeshift 建立系統還原點"))
        p.add(g)

        g = group("個人檔案備份", "備份家目錄裡的文件、照片等到外接硬碟或網路位置")
        if tools["deja-dup"]:
            g.add(row("立即備份", "使用「備份」目前的設定", None, [btn("備份", on_click=lambda: subprocess.Popen(["deja-dup", "--backup"]))]))
            g.add(row("備份設定", "選擇資料夾、目的地與排程，或還原檔案", None, [btn("開啟", on_click=lambda: subprocess.Popen(["deja-dup"]))]))
        else:
            g.add(self.install_row("Déjà Dup", "deja-dup", "Tuneup 透過 Déjà Dup（備份）處理個人檔案"))
        p.add(g)

        g = group("完整映像檔", "整顆磁碟含開機區的備份。系統執行中無法做出可靠的映像，必須從隨身碟開機製作")
        g.add(row("需要的空間", "外接硬碟至少要有這麼多可用空間", None, [value(system.human(used))]))
        g.add(row("製作 Clonezilla 開機隨身碟", "官方說明與下載", None,
                  [btn("開啟", on_click=lambda: Gtk.UriLauncher(uri="https://clonezilla.org/clonezilla-live.php").launch(self.win, None, None))]))
        p.add(g)
        return p

    # ---------- 設定檔 ----------
    def profile_diff(self, profile):
        names = {e["uuid"]: e["name"] for e in self.exts}
        return store.profile_changes(profile, ALL_KEYS, self.ext_states(), names)

    def find_profile(self, name):
        return next((pf for pf in self.profiles if pf["name"] == name), None)

    def create_profile(self, name):
        store.save_profile(store.capture(name, ALL_KEYS, self.ext_states()))
        self.reload("pf")
        self.toast_raw(_("已儲存設定檔「{name}」").format(name=name))

    def apply_profile(self, name):
        profile = self.find_profile(name)
        changes = self.profile_diff(profile)
        lines = [self.change_line(c) for c in changes[:12]]
        if len(changes) > 12:
            lines.append(_("…以及另外 {n} 項").format(n=len(changes) - 12))
        self.confirm(_("套用設定檔「{name}」？").format(name=name), "\n".join(lines), "套用",
                     lambda: self.commit("profile", changes, "已套用，可在「歷史」還原", label=name, page="pf"))

    def export_profile(self, name):
        profile = self.find_profile(name)

        def saved(dialog, result):
            try:
                path = dialog.save_finish(result).get_path()
            except GLib.Error:
                return  # 使用者取消
            try:
                Path(path).write_text(json.dumps(profile, ensure_ascii=False, indent=1))
                self.toast_raw(_("已匯出到 {path}").format(path=path))
            except OSError as err:
                self.toast_raw(_("無法套用：{error}").format(error=str(err)))
        Gtk.FileDialog(initial_name=f"{name}.tuneup").save(self.win, None, saved)

    def delete_profile(self, name):
        def run():
            store.delete_profile(name)
            self.reload("pf")
        self.confirm("刪除這個設定檔？", _("「{name}」會移到垃圾桶，系統設定不受影響。").format(name=name), "刪除", run, destructive=True)

    def add_imported(self, data, err=None):
        if err or not store.valid_profile(data):
            self.toast_raw(_("無法匯入：{error}").format(error=err or _("不是有效的 Tuneup 設定檔")))
            return
        store.save_profile({"name": data["name"].strip(), "created": data.get("created", 0), "settings": data["settings"], "extensions": data.get("extensions", {})})
        self.reload("pf")
        self.toast_raw(_("已匯入設定檔「{name}」，尚未套用").format(name=data["name"].strip()))

    def import_file(self):
        def opened(dialog, result):
            try:
                path = Path(dialog.open_finish(result).get_path())
            except GLib.Error:
                return  # 使用者取消
            try:
                if path.stat().st_size > MAX_IMPORT_BYTES:
                    raise ValueError("file too large")
                self.add_imported(json.loads(path.read_text()))
            except (OSError, ValueError) as err:
                self.add_imported(None, str(err))
        Gtk.FileDialog().open(self.win, None, opened)

    def import_url(self):
        self.prompt("從網址匯入", "https://…", "匯入", lambda url: in_background(lambda: fetch_json(url), self.add_imported))

    def page_pf(self):
        p = Adw.PreferencesPage()
        g = group()
        g.add(row("從目前狀態建立設定檔", "記下所有調校值與擴充功能的啟用狀態", None,
                  [btn("建立", "suggested-action", on_click=lambda: self.prompt("建立設定檔", "名稱", "建立", self.create_profile))]))
        p.add(g)
        g = group("我的設定檔")
        for i, pf in enumerate(self.profiles):
            name = pf["name"]
            diff = self.profile_diff(pf)
            sub = _("{a} 項調校 · {b} 個擴充功能").format(a=len(pf["settings"]), b=len(pf.get("extensions", {})))
            r = row(name, sub, tile(name, i), data=True)
            if diff:
                r.add_suffix(btn("套用", on_click=lambda name=name: self.apply_profile(name)))
            else:
                r.add_suffix(chip("使用中", "success"))
            r.add_suffix(self.param_menu([("更新為目前狀態", "pf-update", name), ("匯出", "pf-export", name), ("刪除", "pf-delete", name)]))
            g.add(r)
        if not self.profiles:
            g.add(row("尚無設定檔", "建立後會顯示在這裡", Gtk.Image(icon_name="emblem-favorite-symbolic")))
        p.add(g)
        g = group("匯入", "匯入只會加入清單，不會立刻套用；套用前可以先看差異")
        g.add(row("從檔案匯入", "選擇 .tuneup 設定檔", Gtk.Image(icon_name="document-open-symbolic"), [btn("選擇檔案", on_click=self.import_file)]))
        g.add(row("從網址匯入", "GitHub Gist 的 raw 連結或任何 https 網址", Gtk.Image(icon_name="insert-link-symbolic"), [btn("貼上網址", on_click=self.import_url)]))
        p.add(g)
        return p

    # ---------- 歷史 ----------
    def change_title(self, c):
        if c["type"] == "extension":
            return (_("啟用 {name}") if c["new"] else _("停用 {name}")).format(name=c["name"])
        return _(KEY_TITLES.get((c["schema"], c["key"]), c["key"]))

    def change_line(self, c):
        if c["type"] == "extension":
            return self.change_title(c)
        default = _("預設值")
        return f"{self.change_title(c)}: {c['old'] or default} → {c['new'] or default}"

    def entry_title(self, e):
        if e["action"] == "profile":
            return _("套用設定檔「{name}」").format(name=e["label"])
        if e["action"] == "undo":
            return _("還原單次變更")
        if e["action"] == "restore":
            return _("還原到先前的時間點")
        if len(e["changes"]) == 1:
            return self.change_title(e["changes"][0])
        return _("{n} 項變更").format(n=len(e["changes"]))

    def diff_markup(self, e):
        default, on, off = _("預設值"), _("啟用"), _("停用")
        lines = []
        for c in e["changes"]:
            if c["type"] == "extension":
                name, old, new = c["name"], off if not c["old"] else on, on if c["new"] else off
            else:
                name, old, new = c["key"], c["old"] or default, c["new"] or default
            lines.append(f'<span foreground="#ff6b6b">- {esc(name)}: {esc(str(old))}</span>')
            lines.append(f'<span foreground="#4fd18b">+ {esc(name)}: {esc(str(new))}</span>')
        return "\n".join(lines)

    def undo_entry(self, entry_id):
        try:
            done = store.undo(entry_id)
        except GLib.Error as err:
            return self.toast_raw(_("無法套用：{error}").format(error=err.message))
        self.pending = {}
        GLib.timeout_add(300, lambda: (self.reload("hist"), self.toast("已還原" if done else "沒有需要變更的項目"), False)[2])

    def restore_entry(self, entry_id):
        def run():
            try:
                done = store.restore_to(int(entry_id))
            except GLib.Error as err:
                return self.toast_raw(_("無法套用：{error}").format(error=err.message))
            self.pending = {}
            GLib.timeout_add(300, lambda: (self.reload("hist"), self.toast("已還原" if done else "沒有需要變更的項目"), False)[2])
        self.confirm("還原到這個時間點？", _("在這之後的所有變更都會被反轉。這次還原也會記入歷史，可以再退回。"), "還原", run)

    def page_hist(self):
        p = Adw.PreferencesPage()
        history = store.load_history()
        g = group()
        g.add(row("每次變更都會自動記錄", "還原本身也會記入歷史，所以可以再退回", None, [value(_("{n} 筆").format(n=len(history)))]))
        p.add(g)
        g = group("時間軸")
        for n, e in enumerate(reversed(history[-50:])):
            ex = Adw.ExpanderRow(title=esc(self.entry_title(e)), subtitle=esc(when(e["time"])))
            ex.add_prefix(Gtk.Image(icon_name="document-open-recent-symbolic"))
            lb = Gtk.Label(use_markup=True, label=self.diff_markup(e), xalign=0, selectable=True, wrap=True, max_width_chars=1)
            lb.add_css_class("diff")
            r = Adw.ActionRow()
            r.set_child(lb)
            ex.add_row(r)
            ar = Adw.ActionRow()
            ar.add_suffix(btn("還原這次變更", on_click=lambda entry_id=e["id"]: self.undo_entry(entry_id)))
            if n > 0:  # 最新的一筆就是現在的狀態，沒有「之後」可以還原
                ar.add_suffix(self.param_menu([("還原到此時間點", "restore-to", str(e["id"]))]))
            ex.add_row(ar)
            g.add(ex)
        if not history:
            g.add(row("尚無紀錄", "透過 Tuneup 套用的變更會顯示在這裡", Gtk.Image(icon_name="document-open-recent-symbolic")))
        p.add(g)
        return p

    # ---------- 模組中心 ----------
    def page_mods(self):
        p = Adw.PreferencesPage()
        live = sum(1 for m in MODULES if m[2])
        g = group()
        g.add(row("模組", "每個功能是一個模組：頁面 + 動作 + 還原邏輯", None,
                  [value(_("{a} 內建 · {b} 規劃中").format(a=live, b=len(MODULES) - live))]))
        p.add(g)
        for title, is_live in (("內建模組", True), ("規劃中", False)):
            g = group(title)
            for i, (n, letter, on, d) in enumerate(MODULES):
                if on == is_live:
                    r = row(n, d, tile(letter, i))
                    if on:
                        r.add_suffix(chip("內建", "success"))
                    g.add(r)
            p.add(g)
        return p

    # ---------- 框架 ----------
    def toast(self, t):
        self.toast_raw(_(t))

    def toast_raw(self, text):
        self.toasts.add_toast(Adw.Toast(title=esc(text), timeout=3))

    def goto(self, nid):
        r = self.rows.get(nid)
        if r:
            self.nav_list.select_row(r)

    def on_nav(self, lb, r):
        if r is None or not hasattr(r, "nid"):
            return
        self.stack.set_visible_child_name(r.nid)
        self.content_page.set_title(r.ntitle)
        self.split.set_show_content(True)
        if self.search_text:
            for k in self.page_keys.get(r.nid, []):
                if self.search_text in _(KEY_TITLES[k]).lower() or self.search_text in k[1]:
                    self.key_widgets[k][0].grab_focus()  # 頁面會自動捲到取得焦點的列
                    break

    def build_sidebar(self):
        lb = Gtk.ListBox()
        lb.add_css_class("navigation-sidebar")
        self.rows = {}
        badges = {"ext": sum(1 for e in self.exts if e["has_update"])}
        for item in SIDEBAR:
            if item is None:
                sep = Gtk.ListBoxRow(selectable=False, activatable=False, focusable=False)
                sep.set_child(Gtk.Separator(margin_top=6, margin_bottom=6))
                sep.nid = None
                sep.add_css_class("sep")
                lb.append(sep)
                continue
            nid, title, icon = item
            r = Gtk.ListBoxRow()
            h = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6)
            h.append(Gtk.Image(icon_name=icon))
            h.append(Gtk.Label(label=_(title), xalign=0, hexpand=True))
            if badges.get(nid):
                bl = Gtk.Label(label=str(badges[nid]))
                bl.add_css_class("caption")
                bl.add_css_class("accent")
                h.append(bl)
            r.set_child(h)
            r.nid, r.ntitle = nid, _(title)
            self.rows[nid] = r
            lb.append(r)
        lb.connect("row-selected", self.on_nav)
        return lb

    def add_actions(self):
        simple = {"theme": self.flip_theme, "reload": lambda: self.reload(), "discard": self.discard_pending}
        for name, cb in simple.items():
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
        with_name = {
            "pf-update": lambda name: self.create_profile(name), "pf-export": self.export_profile,
            "pf-delete": self.delete_profile, "restore-to": self.restore_entry,
        }
        for name, cb in with_name.items():
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, p, cb=cb: cb(p.get_string()))
            self.add_action(a)
        la = Gio.SimpleAction.new_stateful("lang", GLib.VariantType.new("s"), GLib.Variant("s", self.lang_pref))
        la.connect("activate", self.on_lang)
        self.add_action(la)

    def do_activate(self):
        css = Gtk.CssProvider()
        css.load_from_string(CSS.replace("}", ";}"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.add_actions()
        self.win = Adw.ApplicationWindow(application=self, default_width=1100, default_height=720, title="Tuneup")
        self.build_ui()
        self.win.present()
        if self.shot_dir:
            GLib.timeout_add(900, self.shoot, 0)

    def on_lang(self, action, param):
        """切換語言：存偏好、載入翻譯、重建畫面並回到原本的頁面。"""
        code = param.get_string()
        action.set_state(param)
        self.lang_pref = code
        save_pref(code)
        load_lang(code)
        GLib.idle_add(lambda: self.reload() and False)

    def build_ui(self, page="dash"):
        """讀取系統目前的狀態並建立整個畫面；套用變更、重新整理與切換語言都走這裡。"""
        self.key_rows = {}
        self.key_widgets = {}
        self.page_keys = {}
        self.page_words = {item[0]: [] for item in SIDEBAR if item}
        self.sys_name, self.sys_detail, self.shell_version = load_system()
        self.exts, self.ext_error = load_extensions()
        self.profiles = store.load_profiles()
        self.page_words["ext"] = [e["name"].lower() for e in self.exts]
        self.toasts = Adw.ToastOverlay()
        self.split = split = Adw.NavigationSplitView(min_sidebar_width=240, max_sidebar_width=280)

        # 側欄：搜尋鈕 + 標題 + 主選單（同 Settings）
        self.nav_list = lb = self.build_sidebar()
        sb_tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        sbtn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=_("搜尋"))
        hb.pack_start(sbtn)
        menu = Gio.Menu()
        menu.append(_("重新整理"), "app.reload")
        menu.append(_("捨棄未套用的變更"), "app.discard")
        menu.append(_("切換深淺色"), "app.theme")
        langs = Gio.Menu()
        for code, name in LANGS:
            langs.append(_(name) if code == "system" else name, f"app.lang::{code}")
        menu.append_submenu(_("語言"), langs)
        hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True))
        hb.set_title_widget(Adw.WindowTitle(title="Tuneup", subtitle=""))
        sb_tv.add_top_bar(hb)
        sr = Gtk.SearchBar()
        se = Gtk.SearchEntry(placeholder_text=_("搜尋設定"), hexpand=True)
        sr.set_child(se)
        sr.connect_entry(se)
        sbtn.bind_property("active", sr, "search-mode-enabled", 1 | 2)
        sb_tv.add_top_bar(sr)
        se.connect("search-changed", self.on_search)
        sb_tv.set_content(scroll(lb))
        split.set_sidebar(Adw.NavigationPage(title="Tuneup", child=sb_tv))

        # 內容
        self.stack = Adw.ViewStack()
        ctv = Adw.ToolbarView()
        ctv.add_top_bar(Adw.HeaderBar())
        self.banner = Adw.Banner(title="", button_label=_("套用變更"))
        self.banner.connect("button-clicked", lambda *_a: self.apply_pending())
        ctv.add_top_bar(self.banner)
        self.stack.add_named(self.page_dash(), "dash")
        self.stack.add_named(self.page_ext(), "ext")
        for i, (_name, _icon, groups) in enumerate(TWEAKS):
            self.stack.add_named(self.tweak_page(f"tw{i}", groups), f"tw{i}")
        self.stack.add_named(self.page_startup(), "startup")
        self.stack.add_named(self.page_cleanup(), "cleanup")
        self.stack.add_named(self.page_hardware(), "hardware")
        self.stack.add_named(self.page_backup(), "backup")
        self.stack.add_named(self.page_pf(), "pf")
        self.stack.add_named(self.page_hist(), "hist")
        self.stack.add_named(self.page_mods(), "mods")
        ctv.set_content(self.stack)
        self.content_page = Adw.NavigationPage(title=_("總覽"), child=ctv)
        split.set_content(self.content_page)

        self.mark_rows(split)
        self.toasts.set_child(split)
        self.win.set_content(self.toasts)
        self.update_depends()
        self.update_banner()
        lb.select_row(self.rows.get(page) or self.rows["dash"])

    def mark_rows(self, w):
        """有副標題的列加上 two-line，讓列高一致（見 DESIGN.md §2）。"""
        c = w.get_first_child()
        while c:
            if isinstance(c, Adw.ActionRow):
                if not c.has_css_class("wrap"):
                    c.set_title_lines(1)      # 譯文再長也只佔一行，超出以 … 截斷
                c.set_subtitle_lines(1)
                if c.get_subtitle():
                    c.add_css_class("two-line")
            self.mark_rows(c)
            c = c.get_next_sibling()

    def on_search(self, entry):
        """過濾側欄：頁面標題或頁面裡任何一個設定名稱符合就留下。"""
        q = entry.get_text().strip().lower()
        self.search_text = q

        def match(r):
            if getattr(r, "nid", None) is None:
                return not q
            return not q or q in r.ntitle.lower() or any(q in word for word in self.page_words.get(r.nid, []))
        self.nav_list.set_filter_func(match)

    def flip_theme(self):
        m = Adw.StyleManager.get_default()
        m.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT if m.get_dark() else Adw.ColorScheme.FORCE_DARK)

    # ---------- 截圖 ----------
    def snap(self, name):
        w = self.win
        pa = Gtk.WidgetPaintable.new(w)
        s = Gtk.Snapshot()
        pa.snapshot(s, w.get_width(), w.get_height())
        node = s.to_node()
        if node is None:  # 視窗還沒畫出來
            return False
        w.get_native().get_renderer().render_texture(node, None).save_to_png(f"{self.shot_dir}/{name}.png")
        return True

    def shoot(self, i):
        ids = [x[0] for x in SIDEBAR if x]
        if i < len(ids):
            self.goto(ids[i])
            GLib.timeout_add(400, lambda: (GLib.timeout_add(80, self.shoot, i + (1 if self.snap(f"{i:02d}-{ids[i]}") else 0)), False)[1])
        else:
            self.quit()
        return False


if __name__ == "__main__":
    shot = None
    if "--shot" in sys.argv:
        shot = sys.argv[sys.argv.index("--shot") + 1]
    lang = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else None
    app = Tuneup(shot, lang)
    rc = app.run([sys.argv[0]])
    if _missing and os.environ.get("TUNEUP_I18N_CHECK"):
        print("缺少翻譯：", *sorted(_missing), sep="\n  ")
    sys.exit(rc)
