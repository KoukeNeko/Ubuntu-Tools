#!/usr/bin/env python3
"""Tuneup — GTK4 + libadwaita （唯讀：顯示系統的真實狀態，這一版不會寫入任何設定）。
用法:  python3 tuneup.py            開啟視窗
       python3 tuneup.py --shot DIR  將每個頁面截圖存到 DIR 後離開
       python3 tuneup.py --lang en   這次以指定語言開啟（system / zh_TW / en）
"""
import sys
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

# ---------- 多語言 ----------
# 原始字串為繁體中文；其他語言放在 i18n/<代碼>.json（{"中文原文": "譯文"}）。
# 新增語言：複製 i18n/en.json 改檔名與內容，再在 LANGS 加一列即可。
import json
import os
import subprocess
import threading
import urllib.request
from pathlib import Path

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

# ---------- 真實資料來源（這一版只讀取，不寫入系統） ----------
READ_ONLY_MSG = "唯讀模式：這一版還不會寫入系統"
SHELL_BUS = ("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions")
EXT_STATE_ACTIVE, EXT_STATE_ERROR, EXT_STATE_OUT_OF_DATE = 1, 3, 4  # 取自 GNOME Shell 的 ExtensionState


def shell_call(method, args=None, iface=SHELL_BUS[0]):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    return bus.call_sync(SHELL_BUS[0], SHELL_BUS[1], iface, method, args, None, Gio.DBusCallFlags.NONE, 3000, None).unpack()


def load_shell_version():
    try:
        v = shell_call("Get", GLib.Variant("(ss)", (SHELL_BUS[0], "ShellVersion")), "org.freedesktop.DBus.Properties")[0]
        return v.split(".")[0]
    except GLib.Error:
        return ""


def load_extensions():
    """回傳 (擴充功能清單, 錯誤訊息)。GNOME Shell 沒有回應時清單為空。"""
    try:
        raw = shell_call("ListExtensions")[0]
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


def fetch_popular(shell_major, done):
    """背景向 extensions.gnome.org 取熱門清單，完成後在主執行緒呼叫 done(items, error)。"""
    def work():
        url = f"https://extensions.gnome.org/extension-query/?sort=popularity&shell_version={shell_major}&page=1"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Tuneup"})
            with urllib.request.urlopen(req, timeout=8) as r:
                items, err = json.load(r).get("extensions", [])[:12], None
        except (OSError, ValueError) as e:
            items, err = [], str(e)
        GLib.idle_add(lambda: done(items, err) and False)
    threading.Thread(target=work, daemon=True).start()


def themes_with(marker):
    """已安裝的圖示／游標主題：marker 是主題資料夾內必須存在的項目。"""
    names = set()
    for base in ("/usr/share/icons", os.path.expanduser("~/.local/share/icons"), os.path.expanduser("~/.icons")):
        try:
            for d in os.scandir(base):
                if d.is_dir() and os.path.exists(os.path.join(d.path, marker)) and d.name != "default":
                    names.add(d.name)
        except OSError:
            pass
    return [(n, n) for n in sorted(names, key=str.lower)]


def icon_themes():
    return [(n, v) for n, v in themes_with("index.theme") if not os.path.isdir(f"/usr/share/icons/{n}/cursors") or os.path.isdir(f"/usr/share/icons/{n}/scalable") or os.path.isdir(f"/usr/share/icons/{n}/16x16")]


def cursor_themes():
    return themes_with("cursors")


def buttons_on_left(layout):
    return 0 if "close" in layout.split(":")[0] else 1


def caps_remap(options):
    if "caps:ctrl_modifier" in options or "ctrl:nocaps" in options:
        return 1
    return 2 if "caps:escape" in options else 0


ACCENTS = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800", "orange": "#ed5b00",
           "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac", "slate": "#6f8396", "brown": "#986a44"}
IF, WM, MUTTER = "org.gnome.desktop.interface", "org.gnome.desktop.wm.preferences", "org.gnome.mutter"
DOCK, TOUCHPAD, KEYBOARD = "org.gnome.shell.extensions.dash-to-dock", "org.gnome.desktop.peripherals.touchpad", "org.gnome.desktop.peripherals.keyboard"
POWER = "org.gnome.settings-daemon.plugins.power"

# 調校：分類 -> 群組 -> (類型, 標題, schema, key, 額外參數)
#   switch: 額外參數可給 reader(value)->bool　enum: [(標籤, 值)] 或回傳它的函式
#   choice: ([標籤], reader(value)->索引)　spin: (最小, 最大, 步進)　accent / info: None
# 系統上不存在的 schema 或 key 會自動略過。
TWEAKS = [
    ("外觀", "applications-graphics-symbolic", [
        ("主題", [
            ("enum", "色彩模式", IF, "color-scheme", [("預設", "default"), ("深色", "prefer-dark"), ("淺色", "prefer-light")]),
            ("accent", "強調色", IF, "accent-color", None),
            ("enum", "圖示主題", IF, "icon-theme", icon_themes),
            ("enum", "游標主題", IF, "cursor-theme", cursor_themes),
        ]),
        ("字體", [
            ("info", "介面字體", IF, "font-name", None),
            ("info", "等寬字體", IF, "monospace-font-name", None),
            ("spin", "字體縮放", IF, "text-scaling-factor", (0.5, 3.0, 0.05)),
        ]),
    ]),
    ("視窗", "view-restore-symbolic", [
        ("標題列", [
            ("choice", "視窗按鈕位置", WM, "button-layout", (["左", "右"], buttons_on_left)),
            ("enum", "雙擊標題列", WM, "action-double-click-titlebar", [("最大化", "toggle-maximize"), ("最小化", "minimize"), ("無動作", "none")]),
        ]),
        ("行為", [
            ("enum", "焦點模式", WM, "focus-mode", [("點擊", "click"), ("滑鼠移過", "sloppy")]),
            ("switch", "邊緣貼齊", MUTTER, "edge-tiling", None),
            ("switch", "對話框附著於父視窗", MUTTER, "attach-modal-dialogs", None),
        ]),
    ]),
    ("頂部列與 Dock", "view-grid-symbolic", [
        ("頂部列", [
            ("switch", "時鐘顯示秒數", IF, "clock-show-seconds", None),
            ("switch", "顯示星期", IF, "clock-show-weekday", None),
            ("switch", "顯示電量百分比", IF, "show-battery-percentage", None),
        ]),
        ("Dock", [
            ("enum", "位置", DOCK, "dock-position", [("左", "LEFT"), ("下", "BOTTOM"), ("右", "RIGHT")]),
            ("spin", "圖示大小", DOCK, "dash-max-icon-size", (16, 64, 2)),
            ("switch", "永遠顯示", DOCK, "dock-fixed", None),
        ]),
    ]),
    ("鍵盤與滑鼠", "input-keyboard-symbolic", [
        ("觸控板", [
            ("switch", "自然捲動", TOUCHPAD, "natural-scroll", None),
            ("switch", "輕觸點擊", TOUCHPAD, "tap-to-click", None),
            ("spin", "指標速度", TOUCHPAD, "speed", (-1.0, 1.0, 0.1)),
        ]),
        ("鍵盤", [
            ("choice", "Caps Lock 改為", "org.gnome.desktop.input-sources", "xkb-options", (["Caps Lock", "Ctrl", "Esc"], caps_remap)),
            ("spin", "按鍵重複速度", KEYBOARD, "repeat-interval", (10, 100, 1)),
        ]),
    ]),
    ("電源", "battery-symbolic", [
        ("電源", [
            ("enum", "螢幕關閉時間", "org.gnome.desktop.session", "idle-delay", [("1 分鐘", 60), ("5 分鐘", 300), ("10 分鐘", 600), ("永不", 0)]),
            ("enum", "接上電源閒置時", POWER, "sleep-inactive-ac-type", [("休眠", "suspend"), ("不執行動作", "nothing")]),
            ("enum", "電源鍵", POWER, "power-button-action", [("休眠", "suspend"), ("詢問", "interactive"), ("不執行動作", "nothing")]),
        ]),
    ]),
    ("隱私", "preferences-system-privacy-symbolic", [
        ("隱私", [
            ("switch", "記錄最近開啟的檔案", "org.gnome.desktop.privacy", "remember-recent-files", None),
            ("switch", "傳送診斷資料給 Canonical", "org.gnome.desktop.privacy", "report-technical-problems", None),
            ("switch", "定位服務", "org.gnome.system.location", "enabled", None),
        ]),
    ]),
]
QUICK = [
    ("switch", "深色模式", IF, "color-scheme", lambda v: v == "prefer-dark"),
    ("switch", "夜間護眼", "org.gnome.settings-daemon.plugins.color", "night-light-enabled", None),
    ("switch", "動畫效果", IF, "enable-animations", None),
]
MODULES = [
    ("擴充功能管理", "E", True, "安裝、啟用、更新、設定 GNOME 擴充功能"),
    ("系統調校", "T", True, "gsettings / dconf 的視覺化介面"),
    ("設定檔與快照", "P", False, "打包、分享、還原整套設定"),
    ("應用程式", "A", False, "統一管理 apt、Snap、Flatpak，附來源與大小"),
    ("啟動項目", "S", False, "登入時自動啟動的程式與 systemd 使用者服務"),
    ("系統清理", "C", False, "舊核心、快取、孤立套件與日誌，先預覽再清"),
    ("驅動與硬體", "D", False, "顯卡驅動切換、韌體更新、電池健康"),
    ("外掛 SDK", "+", False, "讓社群以 Python / JS 寫自己的模組並分享"),
]
SIDEBAR = [  # id, 標題, 圖示；None = 分隔線（同 Settings 的分組）
    ("dash", "總覽", "go-home-symbolic"),
    ("ext", "擴充功能", "application-x-addon-symbolic"),
    None,
    ("tw0", "外觀", "applications-graphics-symbolic"),
    ("tw1", "視窗", "view-restore-symbolic"),
    ("tw2", "頂部列與 Dock", "view-grid-symbolic"),
    ("tw3", "鍵盤與滑鼠", "input-keyboard-symbolic"),
    ("tw4", "電源", "battery-symbolic"),
    ("tw5", "隱私", "preferences-system-privacy-symbolic"),
    None,
    ("pf", "設定檔", "emblem-favorite-symbolic"),
    ("hist", "快照與歷史", "document-open-recent-symbolic"),
    None,
    ("mods", "模組中心", "view-app-grid-symbolic"),
]


def esc(s):
    return GLib.markup_escape_text(s)


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
    lb = Gtk.Label(label=text)
    lb.add_css_class("caption")
    lb.add_css_class("dim-label")
    lb.set_valign(Gtk.Align.CENTER)
    return lb


def btn(label, css=None, icon=None):
    b = Gtk.Button(label=_(label)) if label else Gtk.Button(icon_name=icon)
    if css:
        for c in css.split():
            b.add_css_class(c)
    b.set_valign(Gtk.Align.CENTER)
    return b


def scroll(w):
    s = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    s.set_child(w)
    return s


def group(title=None, desc=None):
    g = Adw.PreferencesGroup()
    if title:
        g.set_title(_(title))
    if desc:
        g.set_description(_(desc))
    return g


def row(title, sub=None, prefix=None, suffix=(), data=False):
    """data=True：標題與副標題是系統資料（擴充功能名稱等），不經翻譯。"""
    tr = esc if data else _m
    r = Adw.ActionRow(title=tr(title))
    if sub:
        r.set_subtitle(tr(sub))
    if prefix:
        r.add_prefix(prefix)
    for s in suffix:
        r.add_suffix(s)
    return r


class Tuneup(Adw.Application):
    def __init__(self, shot_dir=None, lang=None):
        super().__init__(application_id="io.github.doeshing.Tuneup")
        self.shot_dir = shot_dir
        self.lang_pref = lang or read_pref()  # --lang 只影響這次執行，不會存檔
        load_lang(self.lang_pref)
        self.pending = 0
        self._settings = {}

    def gs(self, schema):
        """取得 Gio.Settings；系統沒有這個 schema 時回傳 None。"""
        if schema not in self._settings:
            found = Gio.SettingsSchemaSource.get_default().lookup(schema, True)
            self._settings[schema] = Gio.Settings.new(schema) if found else None
        return self._settings[schema]

    def has_key(self, schema, key):
        st = self.gs(schema)
        return st is not None and st.props.settings_schema.has_key(key)

    def read_only(self, *_args):
        self.toast(READ_ONLY_MSG)

    def more_menu(self, labels):
        m = Gio.Menu()
        for t in labels:
            m.append(_(t), "app.readonly")
        b = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=m, valign=Gtk.Align.CENTER)
        b.add_css_class("flat")
        return b

    def next_icon(self):
        return Gtk.Image(icon_name="go-next-symbolic")

    def nav_row(self, title, sub, target, suffix=None):
        r = row(title, sub)
        if suffix:
            r.add_suffix(suffix)
        r.add_suffix(self.next_icon())
        r.set_activatable(True)
        r.connect("activated", lambda *_a: self.goto(target))
        return r

    def customized(self):
        """各分類中，使用者改過（與預設值不同）的調校項數量。"""
        counts = []
        for name, _icon, groups in TWEAKS:
            n = sum(1 for _g, items in groups for _k, _t, schema, key, _x in items
                    if self.has_key(schema, key) and self.gs(schema).get_user_value(key) is not None)
            counts.append((name, n))
        return counts

    # ---------- 總覽（仿「關於」頁） ----------
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
        total = sum(n for _name, n in counts)
        g = group("概覽")
        g.add(self.nav_row("擴充功能", _("已安裝 {n} 個，{m} 個啟用").format(n=len(self.exts), m=active), "ext",
                           chip(_("{n} 個可更新").format(n=len(updates)), "warning") if updates else None))
        detail = " · ".join(f"{_(name)} {n}" for name, n in counts if n) or _("全部為預設值")
        g.add(self.nav_row("已自訂的調校項", detail, "tw0", value(str(total))))
        g.add(self.nav_row("目前設定檔", "尚未建立設定檔", "pf"))
        g.add(self.nav_row("快照", "尚無快照", "hist"))
        p.add(g)

        g = group("待處理")
        for e in broken:
            title = _("{name} 發生錯誤").format(name=e["name"]) if e["state"] == EXT_STATE_ERROR else _("{name} 與目前的 GNOME Shell 不相容").format(name=e["name"])
            b = btn("檢視")
            b.connect("clicked", lambda *_a: self.goto("ext"))
            g.add(row(title, e["error"] or e["uuid"], Gtk.Image(icon_name="dialog-error-symbolic"), [b], data=True))
        if updates:
            b = btn("檢視")
            b.connect("clicked", lambda *_a: self.goto("ext"))
            g.add(row(_("{n} 個擴充功能有新版本").format(n=len(updates)), "、".join(e["name"] for e in updates),
                      Gtk.Image(icon_name="folder-download-symbolic"), [b], data=True))
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

    # ---------- 擴充功能（仿 Settings 子頁面導覽） ----------
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

    def locked_switch(self, active):
        """唯讀模式下的開關：顯示真實狀態，但不可操作。"""
        return Gtk.Switch(active=active, valign=Gtk.Align.CENTER, sensitive=False, tooltip_text=_(READ_ONLY_MSG))

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
        sw = Adw.SwitchRow(title=_m("啟用"), active=e["state"] == EXT_STATE_ACTIVE, sensitive=False)
        g.add(sw)
        if e["has_prefs"]:
            b = btn("開啟")
            b.connect("clicked", lambda *_a: subprocess.Popen(["gnome-extensions", "prefs", e["uuid"]]))
            g.add(row("開啟偏好設定", suffix=[b]))
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
            b = btn("解除安裝", "destructive-action")
            b.connect("clicked", self.read_only)
            g.add(row("解除安裝", None, None, [b]))
            pg.add(g)
        self.ext_nav.push(self.sub_page(e["name"], pg))

    def show_updates(self):
        up = Adw.PreferencesPage()
        updates = [(i, e) for i, e in enumerate(self.exts) if e["has_update"]]
        g = group(_("{n} 個擴充功能有新版本").format(n=len(updates)) if updates else None)
        for i, e in updates:
            b = btn("更新")
            b.connect("clicked", self.read_only)
            g.add(row(e["name"], e["version"] or e["uuid"], tile(e["name"], i), [b], data=True))
        if not updates:
            g.add(row("全部都是最新版本", "GNOME Shell 沒有回報任何可用的更新", Gtk.Image(icon_name="emblem-ok-symbolic")))
        up.add(g)
        self.ext_nav.push(self.sub_page(_("更新"), up))

    def show_explore(self):
        dp = Adw.PreferencesPage()
        g = group("熱門", _("支援 GNOME {v} 的擴充功能，資料來自 extensions.gnome.org").format(v=self.shell_version))
        loading = row("載入中…")
        g.add(loading)
        dp.add(g)
        installed = {e["uuid"] for e in self.exts}

        def done(items, err):
            g.remove(loading)
            if err:
                g.add(row(_("無法連線到 extensions.gnome.org"), err, Gtk.Image(icon_name="network-offline-symbolic"), data=True))
            for i, d in enumerate(items):
                if d.get("uuid") in installed:
                    b = btn("已安裝")
                    b.set_sensitive(False)
                else:
                    b = btn("安裝")
                    b.connect("clicked", self.read_only)
                desc = (d.get("description") or "").strip().split("\n")[0]
                g.add(row(d.get("name", ""), desc, tile(d.get("name", ""), i), [b], data=True))
            self.mark_rows(g)

        fetch_popular(self.shell_version, done)
        self.ext_nav.push(self.sub_page(_("探索擴充功能"), dp))

    def page_ext(self):
        nav = Adw.NavigationView()
        self.ext_nav = nav
        p = Adw.PreferencesPage()
        g = group()
        master = Adw.SwitchRow(title=_m("全部擴充功能"), subtitle=_m("關閉即進入安全模式"), sensitive=False)
        if self.has_key("org.gnome.shell", "disable-user-extensions"):
            master.set_active(not self.gs("org.gnome.shell").get_boolean("disable-user-extensions"))
        g.add(master)
        p.add(g)

        g = group()
        n = sum(1 for e in self.exts if e["has_update"])
        r = row(_("更新"), _("{n} 個擴充功能有新版本").format(n=n) if n else _("全部都是最新版本"), None,
                ([chip(str(n), "warning")] if n else []) + [self.next_icon()], data=True)
        r.set_activatable(True)
        r.connect("activated", lambda *_a: self.show_updates())
        g.add(r)
        r = row("探索", "從 extensions.gnome.org 安裝更多", None, [self.next_icon()])
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
            r.add_suffix(self.locked_switch(e["state"] == EXT_STATE_ACTIVE))
            r.add_suffix(self.next_icon())
            r.set_activatable(True)
            r.connect("activated", lambda _r, i=i: self.ext_detail(i))
            g.add(r)
        p.add(g)
        nav.add(Adw.NavigationPage(title=_("擴充功能"), child=p))
        return nav

    # ---------- 系統調校：每個分類是一個側欄項目 ----------
    def mark_dirty(self, *_args):
        self.pending += 1
        self.update_banner()

    def update_banner(self):
        on_tweak = self.stack.get_visible_child_name().startswith("tw")
        self.banner.set_title(_m("{n} 項未套用　套用前會自動建立快照").format(n=self.pending))
        self.banner.set_revealed(self.pending > 0 and on_tweak)

    def tweak_row(self, item):
        """依目前的 gsettings 值建立一列；系統沒有這個 key 時回傳 None。"""
        kind, title, schema, key, extra = item
        if not self.has_key(schema, key):
            return None
        st = self.gs(schema)
        v = st.get_value(key).unpack()
        sub = esc(f"{schema} {key}")
        if kind == "switch":
            w = Adw.SwitchRow(title=_m(title), subtitle=sub, active=extra(v) if extra else v)
            w.connect("notify::active", self.mark_dirty)
        elif kind in ("enum", "choice"):
            if kind == "choice":
                labels, reader = extra
                labels, selected = [_(x) for x in labels], reader(v)
            else:
                options = list(extra() if callable(extra) else extra)
                if v not in [val for _label, val in options]:
                    options.append((str(v), v))  # 目前的值不在預設選項中，照實顯示
                labels = [_(label) for label, _val in options]
                selected = [val for _label, val in options].index(v)
            w = Adw.ComboRow(title=_m(title), subtitle=sub, model=Gtk.StringList.new(labels), selected=selected)
            w.connect("notify::selected", self.mark_dirty)
        elif kind == "spin":
            lo, hi, step = extra
            w = Adw.SpinRow.new_with_range(lo, hi, step)
            w.set_title(_m(title))
            w.set_subtitle(sub)
            w.set_digits(2 if step < 1 else 0)
            w.set_value(v)
            w.connect("notify::value", self.mark_dirty)
        elif kind == "accent":
            w = Adw.ActionRow(title=_m(title), subtitle=sub)
            names = st.props.settings_schema.get_key(key).get_range().unpack()[1]
            sbox = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            first = None
            for name in names:
                t = Gtk.ToggleButton(tooltip_text=name)
                t.add_css_class("swatch")
                t.add_css_class("circular")
                cp = Gtk.CssProvider()
                cp.load_from_string(f"button{{background:{ACCENTS.get(name, '#888')};}}")
                t.get_style_context().add_provider(cp, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                if first is None:
                    first = t
                else:
                    t.set_group(first)
                t.set_active(name == v)
                t.connect("toggled", lambda b: b.get_active() and self.mark_dirty())
                sbox.append(t)
            w.add_suffix(sbox)
        else:  # info：只顯示目前的值
            w = Adw.ActionRow(title=_m(title), subtitle=sub)
            w.add_suffix(value(str(v)))
        return w

    def tweak_page(self, groups):
        p = Adw.PreferencesPage()
        for gt, items in groups:
            rows = [w for w in (self.tweak_row(item) for item in items) if w]
            if not rows:
                continue
            g = group(gt)
            for w in rows:
                g.add(w)
            p.add(g)
        return p

    # ---------- 設定檔 ----------
    def page_pf(self):
        p = Adw.PreferencesPage()
        g = group()
        b = btn("建立", "suggested-action")
        b.connect("clicked", self.read_only)
        g.add(row("從目前狀態建立設定檔", "把整套調校與擴充功能打包，一鍵切換、匯出、分享", None, [b]))
        p.add(g)
        g = group("我的設定檔")
        g.add(row("尚無設定檔", "建立後會顯示在這裡", Gtk.Image(icon_name="emblem-favorite-symbolic")))
        p.add(g)
        g = group("匯入")
        for title, sub, icon, label in (("從檔案匯入", "選擇 .tuneup 設定檔", "document-open-symbolic", "選擇檔案"),
                                        ("從網址匯入", "GitHub Gist 或任何公開網址", "insert-link-symbolic", "貼上網址")):
            b = btn(label)
            b.connect("clicked", self.read_only)
            g.add(row(title, sub, Gtk.Image(icon_name=icon), [b]))
        p.add(g)
        return p

    # ---------- 歷史 ----------
    def page_hist(self):
        p = Adw.PreferencesPage()
        g = group()
        g.add(Adw.SwitchRow(title=_m("變更前自動建立快照"), subtitle=_m("還原本身也會建立快照，所以可以再退回"), active=True, sensitive=False))
        p.add(g)
        g = group("時間軸")
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
        g = group("架構")
        g.add(row("Tuneup Core", "狀態、快照、設定檔、搜尋", Gtk.Image(icon_name="emblem-system-symbolic")))
        g.add(row("模組 API", "模組註冊頁面、動作、快照與還原", Gtk.Image(icon_name="view-app-grid-symbolic")))
        g.add(row("後端轉接", "gsettings · dconf · apt · snap · flatpak · systemd", Gtk.Image(icon_name="network-wired-symbolic")))
        p.add(g)
        return p

    # ---------- 框架 ----------
    def toast(self, t):
        self.toasts.add_toast(Adw.Toast(title=_m(t), timeout=2))

    def goto(self, nid):
        r = self.rows.get(nid)
        if r:
            self.nav_list.select_row(r)

    def on_nav(self, lb, r):
        if r is None or not hasattr(r, "nid"):
            return
        self.stack.set_visible_child_name(r.nid)
        self.content_page.set_title(r.ntitle)
        self.update_banner()
        self.split.set_show_content(True)

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

    def do_activate(self):
        css = Gtk.CssProvider()
        css.load_from_string(CSS.replace("}", ";}"))
        from gi.repository import Gdk
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        for name, cb in (("readonly", self.read_only), ("theme", self.flip_theme), ("reload", lambda *_a: self.build_ui(self.stack.get_visible_child_name()))):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        la = Gio.SimpleAction.new_stateful("lang", GLib.VariantType.new("s"), GLib.Variant("s", self.lang_pref))
        la.connect("activate", self.on_lang)
        self.add_action(la)

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
        page = self.stack.get_visible_child_name()
        GLib.idle_add(lambda: self.build_ui(page) and False)

    def build_ui(self, page="dash"):
        """讀取系統目前的狀態並建立整個畫面；重新整理與切換語言都走這裡。"""
        win = self.win
        self.pending = 0
        self.sys_name, self.sys_detail, self.shell_version = load_system()
        self.exts, self.ext_error = load_extensions()
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
        menu.append(_("切換深淺色"), "app.theme")
        langs = Gio.Menu()
        for code, name in LANGS:
            langs.append(_(name) if code == "system" else name, f"app.lang::{code}")
        menu.append_submenu(_("語言"), langs)
        hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True))
        hb.set_title_widget(Adw.WindowTitle(title="Tuneup", subtitle=""))
        sb_tv.add_top_bar(hb)
        sr = Gtk.SearchBar()
        se = Gtk.SearchEntry(placeholder_text=_("搜尋"), hexpand=True)
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
        self.banner.connect("button-clicked", self.read_only)
        ctv.add_top_bar(self.banner)
        self.stack.add_named(self.page_dash(), "dash")
        self.stack.add_named(self.page_ext(), "ext")
        for i, (_name, _icon, groups) in enumerate(TWEAKS):
            self.stack.add_named(self.tweak_page(groups), f"tw{i}")
        self.stack.add_named(self.page_pf(), "pf")
        self.stack.add_named(self.page_hist(), "hist")
        self.stack.add_named(self.page_mods(), "mods")
        self.pending = 0
        ctv.set_content(self.stack)
        self.content_page = Adw.NavigationPage(title=_("總覽"), child=ctv)
        split.set_content(self.content_page)

        self.mark_rows(split)
        self.toasts.set_child(split)
        win.set_content(self.toasts)
        lb.select_row(self.rows[page])

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
        q = entry.get_text().strip().lower()

        def match(r):
            if getattr(r, "nid", None) is None:
                return not q
            return not q or q in r.ntitle.lower()
        self.nav_list.set_filter_func(match)

    def flip_theme(self, *_args):
        m = Adw.StyleManager.get_default()
        m.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT if m.get_dark() else Adw.ColorScheme.FORCE_DARK)

    # ---------- 截圖 ----------
    def snap(self, name):
        w = self.win
        pa = Gtk.WidgetPaintable.new(w)
        s = Gtk.Snapshot()
        pa.snapshot(s, w.get_width(), w.get_height())
        node = s.to_node()
        tex = w.get_native().get_renderer().render_texture(node, None)
        tex.save_to_png(f"{self.shot_dir}/{name}.png")

    def shoot(self, i):
        ids = [x[0] for x in SIDEBAR if x]
        if i < len(ids):
            self.goto(ids[i])
            GLib.timeout_add(500, lambda: (self.snap(f"{i:02d}-{ids[i]}"), GLib.timeout_add(100, self.shoot, i + 1), False)[2])
        else:
            if os.environ.get("TUNEUP_I18N_CHECK"):  # 把延遲建立的子頁面與提示也跑過一次
                for j in range(len(self.exts)):
                    self.ext_detail(j)
                self.show_updates()
                _(READ_ONLY_MSG)
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
