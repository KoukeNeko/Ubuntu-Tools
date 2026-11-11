"""audio.py 解析與設定檔的測試；不會呼叫 PipeWire。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio  # noqa: E402

DUMP = [
    {"id": 30, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "settings"}, "metadata": [
        {"key": "log.level", "value": 2}, {"key": "clock.rate", "value": 48000},
        {"key": "clock.allowed-rates", "value": "[ 44100 48000 ]"}, {"key": "clock.force-rate", "value": "0"}]},
    {"id": 31, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"}, "metadata": [
        {"key": "default.configured.audio.sink", "value": {"name": "other"}},
        {"key": "default.audio.sink", "value": {"name": "sink-a"}}, {"key": "default.audio.source", "value": {"name": "src-a"}}]},
    {"id": 40, "type": "PipeWire:Interface:Device", "info": {"props": {"media.class": "Audio/Device", "device.name": "card-a", "device.description": "Card A"},
        "params": {"Profile": [{"index": 1, "name": "stereo"}], "EnumProfile": [
            {"index": 0, "name": "off", "description": "Off", "available": "yes"}, {"index": 1, "name": "stereo", "description": "Stereo", "available": "unknown"},
            {"index": 2, "name": "surround", "description": "Surround", "available": "no"}]}}},
    {"id": 41, "type": "PipeWire:Interface:Device", "info": {"props": {"media.class": "Video/Device", "device.name": "cam"}, "params": {}}},
    {"id": 50, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Sink", "node.name": "sink-a", "node.description": "Sink A", "device.id": 40},
        "params": {"Format": [{"format": "S32LE", "rate": 48000, "channels": 2}]}}},
    {"id": 51, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Source", "node.name": "src-a", "device.id": 40}, "params": {}}},
    {"id": 52, "type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Stream/Output/Audio", "node.name": "app"}, "params": {}}},
]


class AudioTest(unittest.TestCase):
    def setUp(self):
        self.state = audio.parse_dump(DUMP)

    def test_defaults_and_clock(self):
        self.assertEqual((self.state["default_sink"], self.state["default_source"]), ("sink-a", "src-a"))
        self.assertEqual(audio.clock_int(self.state, "rate"), 48000)
        self.assertEqual(audio.clock_int(self.state, "force-rate"), 0)
        self.assertEqual(audio.clock_int(self.state, "missing"), 0)
        self.assertEqual(audio.allowed_rates(self.state), [44100, 48000])

    def test_devices_skip_unavailable_profiles_and_non_audio(self):
        self.assertEqual([d["name"] for d in self.state["devices"]], ["card-a"])
        device = self.state["devices"][0]
        self.assertEqual(device["profile"], "stereo")
        self.assertEqual([p["name"] for p in device["profiles"]], ["off", "stereo"])

    def test_nodes(self):
        self.assertEqual([(n["name"], n["rate"], n["channels"]) for n in self.state["sinks"]], [("sink-a", 48000, 2)])
        self.assertEqual(self.state["sources"][0]["description"], "src-a")
        self.assertIsNone(self.state["sources"][0]["rate"])

    def test_midi(self):
        text = "client 0: 'System' [type=kernel]\nclient 14: 'Midi Through' [type=kernel]\nclient 24: 'MiniFuse 1' [type=kernel,card=2]\nclient 142: 'PipeWire-System' [type=user]\n"
        self.assertEqual(audio.parse_midi(text), ["MiniFuse 1"])

    def test_config_text_roundtrip(self):
        cfg = {"rate": 96000, "quantum": 256, "allowed-rates": [44100, 48000, 96000]}
        text = audio.config_text(cfg)
        self.assertIn("default.clock.rate = 96000", text)
        original = audio.CONFIG_FILE
        try:
            import tempfile
            from pathlib import Path
            audio.CONFIG_FILE = Path(tempfile.mkdtemp()) / "90-tuneup.conf"
            self.assertIsNone(audio.read_config())
            audio.CONFIG_FILE.write_text(text)
            self.assertEqual(audio.read_config(), cfg)
            audio.CONFIG_FILE.write_text(audio.config_text({"quantum": 128}))
            self.assertEqual(audio.read_config(), {"quantum": 128})
        finally:
            audio.CONFIG_FILE = original


if __name__ == "__main__":
    unittest.main()
