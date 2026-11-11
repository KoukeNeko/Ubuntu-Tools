"""PipeWire 的音訊裝置狀態與設定。不依賴 GTK。

歷史紀錄用名稱（node.name、device.name、profile 名稱）而不是 PipeWire 的物件編號，
因為編號在每次重新啟動後都會改變。
"""
import json
import re
import subprocess
from pathlib import Path

from gi.repository import GLib

from system import run

CONFIG_FILE = Path(GLib.get_user_config_dir()) / "pipewire" / "pipewire.conf.d" / "90-tuneup.conf"
RATES = [44100, 48000, 88200, 96000, 176400, 192000]
QUANTA = [64, 128, 256, 512, 1024, 2048]
SERVICES = ["pipewire", "pipewire-pulse", "wireplumber"]
IGNORED_MIDI = ("System", "Midi Through", "PipeWire")


def parse_dump(objects):
    """pw-dump 的輸出 -> {"devices", "sinks", "sources", "default_sink", "default_source", "clock"}"""
    state = {"devices": [], "sinks": [], "sources": [], "default_sink": None, "default_source": None, "clock": {}}
    for o in objects:
        kind = o.get("type", "").rsplit(":", 1)[-1]
        info = o.get("info") or {}
        props = info.get("props") or o.get("props") or {}  # Metadata 的 props 在最外層
        params = info.get("params") or {}
        if kind == "Metadata":
            for entry in o.get("metadata", []):
                key, val = entry.get("key"), entry.get("value")
                if props.get("metadata.name") == "default" and isinstance(val, dict):
                    if key == "default.audio.sink":
                        state["default_sink"] = val.get("name")
                    elif key == "default.audio.source":
                        state["default_source"] = val.get("name")
                elif props.get("metadata.name") == "settings" and key.startswith("clock."):
                    state["clock"][key[len("clock."):]] = val
        elif kind == "Device" and props.get("media.class") == "Audio/Device":
            active = (params.get("Profile") or [{}])[0]
            state["devices"].append({
                "id": o["id"], "name": props.get("device.name", ""), "description": props.get("device.description") or props.get("device.name", ""),
                "profile": active.get("name"),
                "profiles": [{"index": p["index"], "name": p["name"], "description": p.get("description", p["name"])}
                             for p in params.get("EnumProfile", []) if p.get("available") != "no"],
            })
        elif kind == "Node" and props.get("media.class") in ("Audio/Sink", "Audio/Source"):
            fmt = (params.get("Format") or [{}])[0]
            node = {"id": o["id"], "name": props.get("node.name", ""), "description": props.get("node.description") or props.get("node.name", ""),
                    "device_id": props.get("device.id"), "format": fmt.get("format"), "rate": fmt.get("rate"), "channels": fmt.get("channels")}
            state["sinks" if props["media.class"] == "Audio/Sink" else "sources"].append(node)
    return state


def read_state():
    """目前的音訊狀態；PipeWire 沒有執行時回傳 None。"""
    rc, out = run(["pw-dump"])
    if rc != 0:
        return None
    try:
        return parse_dump(json.loads(out))
    except ValueError:
        return None


def clock_int(state, key):
    """clock 設定在 pw-dump 裡可能是數字或字串。"""
    try:
        return int(state["clock"].get(key, 0))
    except (TypeError, ValueError):
        return 0


def allowed_rates(state):
    return [int(x) for x in re.findall(r"\d+", str(state["clock"].get("allowed-rates", "")))]


def parse_midi(text):
    """aconnect -l 的輸出 -> 實體 MIDI 裝置名稱（略過系統內建的虛擬埠）"""
    names = re.findall(r"^client \d+: '([^']*)'", text, re.M)
    return [n for n in names if not n.startswith(IGNORED_MIDI)]


def midi_devices():
    return parse_midi(run(["aconnect", "-l"])[1])


# ---------- 寫入 ----------
# 一筆音訊變更：{"type": "audio", "what", "name", "label", "old", "new"}
#   what = default-sink / default-source：old/new 是 node.name
#   what = profile：name 是 device.name，old/new 是 profile 名稱
#   what = force-rate / force-quantum：old/new 是數字，0 代表不強制
#   what = allowed-rates：old/new 是取樣率清單
#   what = config：old/new 是開機預設的設定 dict，None 代表沒有設定檔

def change(what, old, new, name="", label=""):
    return {"type": "audio", "what": what, "name": name, "label": label, "old": old, "new": new}


def _must(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip() or f"{cmd[0]} failed")


def config_text(cfg):
    lines = [f"    default.clock.{key} = {val}" for key, val in (
        ("rate", cfg.get("rate")), ("quantum", cfg.get("quantum")),
        ("allowed-rates", "[ " + " ".join(str(r) for r in cfg["allowed-rates"]) + " ]" if cfg.get("allowed-rates") else None),
    ) if val]
    return "# 由 Tuneup 產生；刪除這個檔案即可還原\ncontext.properties = {\n" + "\n".join(lines) + "\n}\n"


def read_config():
    """Tuneup 寫入的開機預設；沒有設定檔時回傳 None。"""
    try:
        text = CONFIG_FILE.read_text()
    except OSError:
        return None
    cfg = {}
    for key in ("rate", "quantum"):
        m = re.search(rf"default\.clock\.{key} = (\d+)", text)
        if m:
            cfg[key] = int(m.group(1))
    m = re.search(r"default\.clock\.allowed-rates = \[([^\]]*)\]", text)
    if m:
        cfg["allowed-rates"] = [int(x) for x in m.group(1).split()]
    return cfg


def write(c, target):
    """把一筆音訊變更的目標值寫入系統。失敗時丟出 RuntimeError。"""
    what = c["what"]
    if what in ("default-sink", "default-source", "profile"):
        state = read_state()
        if state is None:
            raise RuntimeError("PipeWire is not running")
        if what == "profile":
            device = next((d for d in state["devices"] if d["name"] == c["name"]), None)
            profile = device and next((p for p in device["profiles"] if p["name"] == target), None)
            if not profile:
                raise RuntimeError(f"profile not available: {target}")
            _must(["wpctl", "set-profile", str(device["id"]), str(profile["index"])])
        else:
            nodes = state["sinks" if what == "default-sink" else "sources"]
            node = next((n for n in nodes if n["name"] == target), None)
            if not node:
                raise RuntimeError(f"device not connected: {target}")
            _must(["wpctl", "set-default", str(node["id"])])
    elif what in ("force-rate", "force-quantum"):
        _must(["pw-metadata", "-n", "settings", "0", f"clock.{what}", str(int(target or 0))])
    elif what == "allowed-rates":
        _must(["pw-metadata", "-n", "settings", "0", "clock.allowed-rates", "[ " + " ".join(str(r) for r in target) + " ]"])
    elif what == "config":
        if target is None:
            CONFIG_FILE.unlink(missing_ok=True)
        else:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(config_text(target))
        _must(["systemctl", "--user", "restart", *SERVICES])
