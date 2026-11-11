"""Tuneup 的寫入與紀錄層：套用變更、歷史、還原、設定檔。不依賴 GTK。

所有會改動系統的動作都從 apply() 進入，所以每一次變更都會留下可還原的紀錄。
"""
import json
import time
from pathlib import Path

from gi.repository import Gio, GLib

DATA_DIR = Path(GLib.get_user_data_dir()) / "tuneup"
HISTORY_FILE = DATA_DIR / "history.json"
PROFILE_DIR = DATA_DIR / "profiles"
SHELL_BUS = ("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions")
MASTER = ("org.gnome.shell", "disable-user-extensions")

_settings = {}


def settings(schema):
    """取得 Gio.Settings；系統沒有這個 schema 時回傳 None。"""
    if schema not in _settings:
        found = Gio.SettingsSchemaSource.get_default().lookup(schema, True)
        _settings[schema] = Gio.Settings.new(schema) if found else None
    return _settings[schema]


def has_key(schema, key):
    st = settings(schema)
    return st is not None and st.props.settings_schema.has_key(key)


def key_type(schema, key):
    return settings(schema).props.settings_schema.get_key(key).get_value_type()


def to_variant(schema, key, value):
    """把 Python 值轉成這個 key 型別的 GVariant。"""
    t = key_type(schema, key).dup_string()
    if t == "d":
        value = float(value)
    elif t in ("i", "u", "n", "q", "x", "t"):
        value = int(value)
    return GLib.Variant(t, value)


def user_text(schema, key):
    """使用者設定的值（GVariant 文字）；仍是預設值時回傳 None。"""
    v = settings(schema).get_user_value(key)
    return v.print_(False) if v is not None else None


def shell_call(method, args=None, iface=SHELL_BUS[0], timeout=3000):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    return bus.call_sync(SHELL_BUS[0], SHELL_BUS[1], iface, method, args, None, Gio.DBusCallFlags.NONE, timeout, None).unpack()


# ---------- 變更 ----------
# 一筆變更是 dict：
#   {"type": "gsettings", "schema", "key", "old", "new"}   old/new 是 GVariant 文字，None 代表預設值
#   {"type": "extension", "uuid", "name", "old", "new"}    old/new 是 bool（是否啟用）

def gsettings_change(schema, key, new_text):
    return {"type": "gsettings", "schema": schema, "key": key, "old": user_text(schema, key), "new": new_text}


def extension_change(uuid, name, old, new):
    return {"type": "extension", "uuid": uuid, "name": name, "old": old, "new": new}


def _write(change, target):
    if change["type"] == "extension":
        shell_call("EnableExtension" if target else "DisableExtension", GLib.Variant("(s)", (change["uuid"],)))
        return
    schema, key = change["schema"], change["key"]
    if not has_key(schema, key):
        return
    st = settings(schema)
    if target is None:
        st.reset(key)
    else:
        st.set_value(key, GLib.Variant.parse(key_type(schema, key), target, None, None))


def apply(action, changes, label=""):
    """寫入一組變更並記入歷史。沒有實際差異的項目會被略過；全部略過時回傳 None。"""
    changes = [c for c in changes if c["old"] != c["new"]]
    if not changes:
        return None
    for c in changes:
        _write(c, c["new"])
    Gio.Settings.sync()
    entry = {"id": time.time_ns(), "time": int(time.time()), "action": action, "label": label, "changes": changes}
    history = load_history()
    history.append(entry)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1))
    return entry


def load_history():
    """歷史紀錄，舊到新。檔案不存在或損毀時視為空。"""
    try:
        data = json.loads(HISTORY_FILE.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _reverse(changes):
    return [dict(c, old=c["new"], new=c["old"]) for c in reversed(changes)]


def undo(entry_id):
    """只還原某一次變更。"""
    for e in load_history():
        if e["id"] == entry_id:
            return apply("undo", _reverse(e["changes"]), str(entry_id))
    return None


def restore_to(entry_id):
    """還原到某一次變更剛完成時的狀態：把它之後的變更由新到舊全部反轉。"""
    later = []
    for e in load_history():
        if e["id"] > entry_id:
            later.append(e)
    changes = []
    for e in reversed(later):
        changes += _reverse(e["changes"])
    # 同一個設定被改過多次時，只保留「現在的值 → 最早的舊值」
    merged = {}
    for c in changes:
        ident = (c["type"], c.get("schema"), c.get("key"), c.get("uuid"))
        if ident in merged:
            merged[ident]["new"] = c["new"]
        else:
            merged[ident] = dict(c)
    return apply("restore", list(merged.values()), str(entry_id))


# ---------- 設定檔 ----------
# {"name", "created", "settings": {"<schema> <key>": GVariant 文字}, "extensions": {uuid: bool}}
# settings 只記錄與預設值不同的項目；套用時，設定檔沒列的已知項目會還原為預設值。

def capture(name, keys, extensions):
    """keys: [(schema, key)]；extensions: {uuid: 是否啟用}"""
    saved = {}
    for schema, key in keys:
        if has_key(schema, key):
            text = user_text(schema, key)
            if text is not None:
                saved[f"{schema} {key}"] = text
    return {"name": name, "created": int(time.time()), "settings": saved, "extensions": dict(extensions)}


def valid_profile(data):
    """檢查外部來的設定檔（檔案或網址）結構是否正確。"""
    return (isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"].strip()
            and isinstance(data.get("settings"), dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in data["settings"].items())
            and isinstance(data.get("extensions", {}), dict) and all(isinstance(v, bool) for v in data.get("extensions", {}).values()))


def profile_changes(profile, keys, extensions, names):
    """套用這個設定檔會造成的變更。只處理 keys 內的已知設定，設定檔裡其他內容一律忽略。"""
    changes = []
    for schema, key in keys:
        if not has_key(schema, key):
            continue
        target = profile["settings"].get(f"{schema} {key}")
        if target is not None:
            try:
                target = GLib.Variant.parse(key_type(schema, key), target, None, None).print_(False)
            except GLib.Error:
                continue  # 值的格式不符這個 key 的型別，略過
        c = gsettings_change(schema, key, target)
        if c["old"] != c["new"]:
            changes.append(c)
    for uuid, want in profile.get("extensions", {}).items():
        if uuid in extensions and extensions[uuid] != want:
            changes.append(extension_change(uuid, names.get(uuid, uuid), extensions[uuid], want))
    return changes


def _profile_path(name):
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name).strip() or "profile"
    return PROFILE_DIR / f"{safe}.json"


def save_profile(profile):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    path = _profile_path(profile["name"])
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=1))
    return path


def load_profiles():
    profiles = []
    if PROFILE_DIR.is_dir():
        for path in sorted(PROFILE_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if valid_profile(data):
                profiles.append(data)
    return profiles


def delete_profile(name):
    """移到垃圾桶，而不是直接刪除。"""
    Gio.File.new_for_path(str(_profile_path(name))).trash(None)
