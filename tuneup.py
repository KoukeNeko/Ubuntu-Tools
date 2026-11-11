#!/usr/bin/env python3
"""Tuneup — GTK4 + libadwaita UI 原型（資料皆為示範，不會修改系統）。
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
.swatch.on{border-color:@window_fg_color}
"""

EXT = [  # name, author, ver, desc, enabled, state
    ("Blur my Shell", "aunetx", "v66", "為面板、Dock、概覽加入模糊背景", True, "upd"),
    ("Dash to Dock", "micxgx", "v95", "把 Dock 固定在螢幕邊緣，可自訂大小與行為", True, "err"),
    ("Vitals", "corecoding", "v68", "在頂部列顯示 CPU、記憶體、溫度與網速", True, ""),
    ("Caffeine", "eonpatapon", "v54", "一鍵暫停螢幕自動鎖定與休眠", False, "upd"),
    ("Clipboard Indicator", "Tudmotu", "v67", "剪貼簿歷史，面板內快速貼上", True, ""),
    ("AppIndicator Support", "ubuntu", "v59", "顯示系統匣圖示（Dropbox、Discord…）", True, ""),
    ("GSConnect", "dlandau", "v58", "與 Android 手機互通：通知、檔案、剪貼簿", False, ""),
    ("Just Perfection", "jrahmatzadeh", "v32", "細部隱藏與調整 GNOME Shell 介面元素", True, ""),
]
DISC = [
    ("Space Bar", "★ 4.7 · 182k", "在面板顯示工作區，像 i3 一樣切換"),
    ("Tiling Shell", "★ 4.8 · 96k", "進階視窗貼齊與分割區域"),
    ("Search Light", "★ 4.6 · 41k", "把搜尋做成 Spotlight 風格"),
    ("Quick Settings Tweaker", "★ 4.5 · 73k", "自訂快速設定面板項目與順序"),
    ("Rounded Window Corners", "★ 4.7 · 118k", "為所有視窗套上圓角"),
    ("Desktop Cube", "★ 4.4 · 38k", "工作區切換使用立方體特效"),
]
# 調校：分類 -> 群組 -> 設定 (類型, 標題, gsettings 鍵, 選項/範圍, 預設值)
TWEAKS = [
    ("外觀", "applications-graphics-symbolic", [
        ("主題", [
            ("combo", "色彩模式", "org.gnome.desktop.interface color-scheme", ["淺色", "深色", "自動"], 1),
            ("color", "強調色", "accent-color", COLORS[:6], 0),
            ("combo", "圖示主題", "icon-theme", ["Yaru", "Papirus", "Adwaita"], 1),
            ("combo", "游標主題", "cursor-theme", ["Yaru", "Bibata", "DMZ"], 0),
        ]),
        ("字體", [
            ("combo", "介面字體", "font-name", ["Ubuntu Sans 11", "Noto Sans CJK TC 11", "Inter 11"], 0),
            ("combo", "等寬字體", "monospace-font-name", ["Ubuntu Mono 13", "JetBrains Mono 12"], 1),
            ("spin", "字體縮放", "text-scaling-factor", (0.8, 1.6, 0.05), 1.15),
        ]),
    ]),
    ("視窗", "view-restore-symbolic", [
        ("標題列", [
            ("combo", "視窗按鈕位置", "button-layout", ["左", "右"], 0),
            ("combo", "雙擊標題列", "action-double-click-titlebar", ["最大化", "最小化", "無動作"], 0),
        ]),
        ("行為", [
            ("combo", "焦點模式", "focus-mode", ["點擊", "滑鼠移過"], 0),
            ("switch", "邊緣貼齊", "edge-tiling", None, True),
            ("switch", "對話框附著於父視窗", "attach-modal-dialogs", None, True),
        ]),
    ]),
    ("頂部列與 Dock", "view-grid-symbolic", [
        ("頂部列", [
            ("switch", "時鐘顯示秒數", "clock-show-seconds", None, False),
            ("switch", "顯示星期", "clock-show-weekday", None, True),
            ("switch", "顯示電量百分比", "show-battery-percentage", None, True),
        ]),
        ("Dock", [
            ("combo", "位置", "dock-position", ["左", "下", "右"], 0),
            ("spin", "圖示大小", "dash-max-icon-size", (24, 64, 2), 44),
            ("switch", "永遠顯示", "dock-fixed", None, True),
        ]),
    ]),
    ("鍵盤與滑鼠", "input-keyboard-symbolic", [
        ("觸控板", [
            ("switch", "自然捲動", "natural-scroll", None, True),
            ("switch", "輕觸點擊", "tap-to-click", None, True),
        ]),
        ("鍵盤", [
            ("combo", "Caps Lock 改為", "caps-remap", ["Caps Lock", "Ctrl", "Esc"], 1),
            ("spin", "按鍵重複速度", "repeat-interval", (10, 80, 1), 30),
        ]),
    ]),
    ("電源", "battery-symbolic", [
        ("電源", [
            ("combo", "電源模式", "power-profile", ["省電", "平衡", "效能"], 1),
            ("combo", "螢幕關閉時間", "idle-delay", ["1 分鐘", "5 分鐘", "10 分鐘", "永不"], 1),
            ("combo", "闔上螢幕時", "lid-close", ["休眠", "關閉螢幕", "不執行動作"], 1),
        ]),
    ]),
    ("隱私", "preferences-system-privacy-symbolic", [
        ("隱私", [
            ("switch", "記錄最近開啟的檔案", "remember-recent-files", None, True),
            ("switch", "傳送診斷資料給 Canonical", "report-technical-problems", None, False),
            ("switch", "定位服務", "location-enabled", None, False),
        ]),
    ]),
]
PROFILES = [
    ("工作模式", "專注、低干擾　深色 · Dock 在左 · 12 個擴充", True),
    ("遊戲", "效能優先　關閉動畫 · 6 個擴充 · Dock 隱藏", False),
    ("極簡", "接近原版 GNOME　淺色 · 5 個擴充", False),
    ("Mac 風格", "模糊、圓角、底部 Dock　Blur my Shell · McMojave 圖示", False),
]
HISTORY = [
    ("啟用 Vitals", "擴充功能 · 今天 10:41", "- vitals@CoreCoding.com: disabled\n+ vitals@CoreCoding.com: enabled"),
    ("視窗按鈕位置", "系統調校 · 今天 09:58", "- button-layout: 'appmenu:minimize,maximize,close'\n+ button-layout: 'close,minimize,maximize:appmenu'"),
    ("套用設定檔「工作模式」", "設定檔 · 今天 09:12", "+ 12 個擴充功能已啟用\n- 5 個擴充功能已停用\n+ color-scheme: prefer-dark\n+ dock-position: left"),
    ("解除安裝 Dash to Panel", "擴充功能 · 昨天 21:30", "- dash-to-panel@jderose9.github.com (v68)"),
    ("字體縮放", "系統調校 · 昨天 21:04", "- text-scaling-factor: 1.0\n+ text-scaling-factor: 1.15"),
]
MODULES = [
    ("擴充功能管理", "E", True, "安裝、啟用、更新、設定 GNOME 擴充功能"),
    ("系統調校", "T", True, "gsettings / dconf 的視覺化介面"),
    ("設定檔與快照", "P", True, "打包、分享、還原整套設定"),
    ("應用程式", "A", False, "統一管理 apt、Snap、Flatpak，附來源與大小"),
    ("啟動項目", "S", False, "登入時自動啟動的程式與 systemd 使用者服務"),
    ("系統清理", "C", False, "舊核心、快取、孤立套件與日誌，先預覽再清"),
    ("驅動與硬體", "D", False, "顯卡驅動切換、韌體更新、電池健康"),
    ("外掛 SDK", "+", False, "讓社群以 Python / JS 寫自己的模組並分享"),
]
SIDEBAR = [  # id, 標題, 圖示, 徽章；None = 分隔線（同 Settings 的分組）
    ("dash", "總覽", "go-home-symbolic", None),
    ("ext", "擴充功能", "application-x-addon-symbolic", "3"),
    None,
    ("tw0", "外觀", "applications-graphics-symbolic", None),
    ("tw1", "視窗", "view-restore-symbolic", None),
    ("tw2", "頂部列與 Dock", "view-grid-symbolic", None),
    ("tw3", "鍵盤與滑鼠", "input-keyboard-symbolic", None),
    ("tw4", "電源", "battery-symbolic", None),
    ("tw5", "隱私", "preferences-system-privacy-symbolic", None),
    None,
    ("pf", "設定檔", "emblem-favorite-symbolic", None),
    ("hist", "快照與歷史", "document-open-recent-symbolic", None),
    None,
    ("mods", "模組中心", "view-app-grid-symbolic", "8"),
]


def tile(text, i):
    lb = Gtk.Label(label=_(text)[0].upper())
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
    """數值（版本、數量）：灰色小字，無底。"""
    lb = Gtk.Label(label=_(text))
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


def row(title, sub=None, prefix=None, suffix=()):
    r = Adw.ActionRow(title=_m(title))
    if sub:
        r.set_subtitle(_m(sub))
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

    def more_menu(self, labels):
        m = Gio.Menu()
        for t in labels:
            m.append(_(t), "app.todo")
        b = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=m, valign=Gtk.Align.CENTER)
        b.add_css_class("flat")
        return b

    def next_icon(self):
        return Gtk.Image(icon_name="go-next-symbolic")

    def nav_row(self, title, sub, target, prefix=None, suffix=None):
        r = row(title, sub, prefix)
        if suffix:
            r.add_suffix(suffix)
        r.add_suffix(self.next_icon())
        r.set_activatable(True)
        r.connect("activated", lambda *_: self.goto(target))
        return r

    # ---------- 總覽（仿「關於」頁） ----------
    def page_dash(self):
        p = Adw.PreferencesPage()
        g = group()
        logo = Gtk.Image(icon_name="distributor-logo", pixel_size=32)
        logo.add_css_class("logo")
        g.add(row("Ubuntu 24.04.2 LTS", "GNOME 46 · Wayland · Kernel 6.8.0-52", logo, [chip("已保護", "success")]))
        p.add(g)

        g = group("概覽")
        g.add(self.nav_row("擴充功能", "已安裝 24 個，21 個啟用", "ext", suffix=chip("3 個可更新", "warning")))
        g.add(self.nav_row("已自訂的調校項", "外觀 8 · 視窗 5 · 其他 4", "tw0", suffix=value("17")))
        g.add(self.nav_row("目前設定檔", "另有 3 組可切換", "pf", suffix=value("工作模式")))
        p.add(g)

        g = group("快照", "每次套用變更前會自動建立快照，隨時可還原")
        g.add(self.nav_row("上次快照　今天 09:12", "查看完整歷史", "hist"))
        p.add(g)

        g = group("待處理")
        for t, s, icon, b in [("Dash to Dock 發生錯誤", "與 GNOME Shell 46 不相容", "dialog-error-symbolic", btn("檢視")),
                              ("3 個擴充功能有新版本", "Blur my Shell、Caffeine、Vitals", "folder-download-symbolic", btn("全部更新", "suggested-action")),
                              ("建議：啟用夜間護眼排程", "你的螢幕常在 22:00 後使用", "night-light-symbolic", btn("前往設定"))]:
            g.add(row(t, s, Gtk.Image(icon_name=icon), [b]))
        p.add(g)

        g = group("快速開關")
        for t, s, on in [("深色模式", "外觀", True), ("夜間護眼", "22:00 – 06:00", False),
                         ("動畫效果", "視窗與工作區", True), ("省電模式", "自動", False)]:
            g.add(Adw.SwitchRow(title=_m(t), subtitle=_m(s), active=on))
        p.add(g)
        return p

    # ---------- 擴充功能（仿 Settings 子頁面導覽） ----------
    def sub_page(self, title, content):
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        tv.set_content(content)
        self.mark_rows(tv)
        return Adw.NavigationPage(title=_(title), child=tv)

    def ext_detail(self, i):
        e = EXT[i]
        pg = Adw.PreferencesPage()
        g = group()
        r = row(e[0], f"by {e[1]} · {e[2]}", tile(e[0], i))
        r.add_suffix(chip("不相容 GNOME 46" if e[5] == "err" else "相容 GNOME 46", "error" if e[5] == "err" else "success"))
        g.add(r)
        g.add(row("說明", e[3]))
        pg.add(g)
        g = group("設定")
        g.add(Adw.SwitchRow(title=_m("啟用"), active=e[4]))
        g.add(Adw.ComboRow(title=_m("位置"), model=Gtk.StringList.new([_("頂部列"), _("底部")])))
        g.add(row("開啟完整偏好設定", suffix=[btn("開啟")]))
        pg.add(g)
        g = group("資訊")
        g.add(row("權限", "讀取視窗、修改面板"))
        g.add(row("來源", "extensions.gnome.org"))
        g.add(row("支援的 Shell 版本", "45、46、47"))
        pg.add(g)
        g = group()
        g.add(row("解除安裝", None, None, [btn("解除安裝", "destructive-action")]))
        pg.add(g)
        self.ext_nav.push(self.sub_page(e[0], pg))

    def page_ext(self):
        nav = Adw.NavigationView()
        self.ext_nav = nav
        p = Adw.PreferencesPage()
        g = group()
        g.add(Adw.SwitchRow(title=_m("全部擴充功能"), subtitle=_m("GNOME Shell 46　關閉即進入安全模式"), active=True))
        p.add(g)

        g = group()
        # 更新子頁
        up = Adw.PreferencesPage()
        ug = group("3 個擴充功能有新版本")
        ug.set_header_suffix(btn("全部更新", "suggested-action"))
        for i, e in enumerate([EXT[0], EXT[3], EXT[2]]):
            ug.add(row(e[0], f"{e[2]} → v{int(e[2][1:]) + 1} · " + _("修正 Wayland 下的閃爍、更新翻譯"), tile(e[0], i), [btn("更新")]))
        up.add(ug)
        r = row("更新", "3 個擴充功能有新版本", None, [chip("3", "warning"), self.next_icon()])
        r.set_activatable(True)
        r.connect("activated", lambda *_: nav.push(self.sub_page("更新", up)))
        g.add(r)
        # 探索子頁
        dp = Adw.PreferencesPage()
        dg = group("熱門", "僅顯示支援 GNOME 46 的擴充功能")
        for i, d in enumerate(DISC):
            r2 = row(d[0], d[2], tile(d[0], i + 3))
            b = btn("安裝")
            b.connect("clicked", lambda b: (b.set_label(_("已安裝")), b.set_sensitive(False)))
            r2.add_suffix(value(d[1]))
            r2.add_suffix(b)
            dg.add(r2)
        dp.add(dg)
        r = row("探索", "從 extensions.gnome.org 安裝更多", None, [self.next_icon()])
        r.set_activatable(True)
        r.connect("activated", lambda *_: nav.push(self.sub_page("探索擴充功能", dp)))
        g.add(r)
        p.add(g)

        g = group("已安裝")
        for i, e in enumerate(EXT):
            r = Adw.ActionRow(title=GLib.markup_escape_text(e[0]), subtitle=_m(e[3]), activatable=True)
            r.add_prefix(tile(e[0], i))
            if e[5] == "err":
                r.add_suffix(chip("不相容", "error"))
            elif e[5] == "upd":
                r.add_suffix(chip("有更新", "warning"))
            r.add_suffix(value(e[2]))
            r.add_suffix(Gtk.Switch(active=e[4], valign=Gtk.Align.CENTER))
            r.add_suffix(self.next_icon())
            r.connect("activated", lambda _r, i=i: self.ext_detail(i))
            g.add(r)
        p.add(g)
        tv = Adw.ToolbarView()
        nav.add(Adw.NavigationPage(title=_("擴充功能"), child=p))
        return nav

    # ---------- 系統調校：每個分類是一個側欄項目 ----------
    def mark_dirty(self, *_):
        self.pending += 1
        self.update_banner()

    def update_banner(self):
        on_tweak = self.stack.get_visible_child_name().startswith("tw")
        self.banner.set_title(_m("{n} 項未套用　套用前會自動建立快照").format(n=self.pending))
        self.banner.set_revealed(self.pending > 0 and on_tweak)

    def tweak_page(self, groups):
        p = Adw.PreferencesPage()
        for gt, items in groups:
            g = group(gt)
            for kind, title, key, opt, val in items:
                if kind == "switch":
                    w = Adw.SwitchRow(title=_m(title), subtitle=key, active=val)
                    w.connect("notify::active", self.mark_dirty)
                elif kind == "combo":
                    w = Adw.ComboRow(title=_m(title), subtitle=key, model=Gtk.StringList.new([_(o) for o in opt]), selected=val)
                    w.connect("notify::selected", self.mark_dirty)
                elif kind == "spin":
                    w = Adw.SpinRow.new_with_range(*opt)
                    w.set_title(_m(title))
                    w.set_subtitle(key)
                    w.set_value(val)
                    w.set_digits(2 if opt[2] < 1 else 0)
                    w.connect("notify::value", self.mark_dirty)
                else:
                    w = Adw.ActionRow(title=_m(title), subtitle=key)
                    sbox = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
                    first = None
                    for j, c in enumerate(opt):
                        t = Gtk.ToggleButton()
                        t.add_css_class("swatch")
                        t.add_css_class("circular")
                        cp = Gtk.CssProvider()
                        cp.load_from_string(f"button{{background:{c};}}")
                        t.get_style_context().add_provider(cp, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                        if first is None:
                            first = t
                        else:
                            t.set_group(first)
                        t.set_active(j == val)
                        t.connect("toggled", lambda b: b.get_active() and self.mark_dirty())
                        sbox.append(t)
                    w.add_suffix(sbox)
                g.add(w)
            p.add(g)
        return p

    def apply_tw(self, *_):
        self.pending = 0
        self.update_banner()
        self.toast("已套用，並建立快照「調校 11:02」")

    # ---------- 設定檔 ----------
    def page_pf(self):
        p = Adw.PreferencesPage()
        g = group()
        g.add(row("從目前狀態建立設定檔", "把整套調校與擴充功能打包，一鍵切換、匯出、分享", None, [btn("建立", "suggested-action")]))
        p.add(g)
        g = group("我的設定檔")
        for i, (n, d, act) in enumerate(PROFILES):
            r = row(n, d, tile(n, i))
            if act:
                r.add_suffix(chip("使用中", "success"))
            else:
                r.add_suffix(btn("套用"))
            r.add_suffix(self.more_menu(["更新為目前狀態", "預覽差異", "匯出", "刪除"] if act else ["預覽差異", "匯出", "刪除"]))
            g.add(r)
        p.add(g)
        g = group("匯入")
        g.add(row("從檔案匯入", "選擇 .tuneup 設定檔", Gtk.Image(icon_name="document-open-symbolic"), [btn("選擇檔案")]))
        g.add(row("從網址匯入", "GitHub Gist 或任何公開網址", Gtk.Image(icon_name="insert-link-symbolic"), [btn("貼上網址")]))
        p.add(g)
        return p

    # ---------- 歷史 ----------
    def page_hist(self):
        p = Adw.PreferencesPage()
        g = group()
        g.add(Adw.SwitchRow(title=_m("變更前自動建立快照"), subtitle=_m("還原本身也會建立快照，所以可以再退回"), active=True))
        p.add(g)
        g = group("時間軸")
        for t, s, diff in HISTORY:
            ex = Adw.ExpanderRow(title=_m(t), subtitle=_m(s))
            ex.add_prefix(Gtk.Image(icon_name="document-open-recent-symbolic"))
            lines = []
            for ln in diff.split("\n"):
                ln = GLib.markup_escape_text(_(ln))
                color = "#4fd18b" if ln.startswith("+") else "#ff6b6b" if ln.startswith("-") else "inherit"
                lines.append(f'<span foreground="{color}">{ln}</span>')
            lb = Gtk.Label(use_markup=True, label="\n".join(lines), xalign=0, selectable=True, wrap=True, max_width_chars=1)
            lb.add_css_class("diff")
            r = Adw.ActionRow()
            r.set_child(lb)
            ex.add_row(r)
            ar = Adw.ActionRow()
            b = btn("還原到此時間點")
            b.connect("clicked", lambda *_: self.toast("已還原，並保留還原前的快照"))
            ar.add_suffix(b)
            ar.add_suffix(self.more_menu(["僅還原這一項", "匯出為設定檔"]))
            ex.add_row(ar)
            g.add(ex)
        p.add(g)
        return p

    # ---------- 模組中心 ----------
    def page_mods(self):
        p = Adw.PreferencesPage()
        g = group()
        g.add(row("模組", "每個功能是一個模組：頁面 + 動作 + 還原邏輯", None, [value("3 內建 · 5 規劃中")]))
        p.add(g)
        for title, live in (("內建模組", True), ("規劃中", False)):
            g = group(title)
            for i, (n, l, on, d) in enumerate(MODULES):
                if on != live:
                    continue
                r = row(n, d, tile(l, i))
                r.add_suffix(chip("內建", "success") if on else btn("想要 +1"))
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
        for item in SIDEBAR:
            if item is None:
                sep = Gtk.ListBoxRow(selectable=False, activatable=False, focusable=False)
                sep.set_child(Gtk.Separator(margin_top=6, margin_bottom=6))
                sep.nid = None
                sep.add_css_class("sep")
                lb.append(sep)
                continue
            nid, title, icon, badge = item
            r = Gtk.ListBoxRow()
            h = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6)
            h.append(Gtk.Image(icon_name=icon))
            h.append(Gtk.Label(label=_(title), xalign=0, hexpand=True))
            if badge:
                bl = Gtk.Label(label=badge)
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

        for name, cb in (("snapshot", lambda *_: self.toast("已建立快照")), ("todo", lambda *_: self.toast("示範畫面：尚未實作")), ("theme", self.flip_theme)):
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
        win = self.win
        self.pending = 0
        self.toasts = Adw.ToastOverlay()
        self.split = split = Adw.NavigationSplitView(min_sidebar_width=240, max_sidebar_width=280)

        # 側欄：搜尋鈕 + 標題 + 主選單（同 Settings）
        self.nav_list = lb = self.build_sidebar()
        sb_tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        sbtn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=_("搜尋"))
        hb.pack_start(sbtn)
        menu = Gio.Menu()
        menu.append(_("建立快照"), "app.snapshot")
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
        self.stack.add_named(self.page_dash(), "dash")
        self.stack.add_named(self.page_ext(), "ext")
        for i, (name, icon, groups) in enumerate(TWEAKS):
            self.stack.add_named(self.tweak_page(groups), f"tw{i}")
        self.stack.add_named(self.page_pf(), "pf")
        self.stack.add_named(self.page_hist(), "hist")
        self.stack.add_named(self.page_mods(), "mods")
        ctv = Adw.ToolbarView()
        ctv.add_top_bar(Adw.HeaderBar())
        self.banner = Adw.Banner(title="", button_label=_("套用變更"))
        self.banner.connect("button-clicked", self.apply_tw)
        ctv.add_top_bar(self.banner)
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
                c.set_title_lines(1)      # 譯文再長也只佔一行，超出以 … 截斷
                c.set_subtitle_lines(1)
                if c.get_subtitle():
                    c.add_css_class("two-line")
            self.mark_rows(c)
            c = c.get_next_sibling()

    def on_search(self, entry):
        q = entry.get_text().strip().lower()
        def f(r):
            if getattr(r, "nid", None) is None:
                return not q
            return not q or q in r.ntitle.lower()
        self.nav_list.set_filter_func(f)

    def flip_theme(self, *_):
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
            if ids[i] == "tw0":
                self.mark_dirty()
            GLib.timeout_add(500, lambda: (self.snap(f"{i:02d}-{ids[i]}"), GLib.timeout_add(100, self.shoot, i + 1), False)[2])
        else:
            if os.environ.get("TUNEUP_I18N_CHECK"):  # 把延遲建立的子頁面與提示也跑過一次
                for j in range(len(EXT)):
                    self.ext_detail(j)
                for t in ("已建立快照", "示範畫面：尚未實作", "已套用，並建立快照「調校 11:02」", "已還原，並保留還原前的快照", "已安裝"):
                    _(t)
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
