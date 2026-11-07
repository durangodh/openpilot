#!/usr/bin/env python3
"""HUDPRM1 voice setting requests: search, get, range check and the driving gate."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from selfdrive.eon_cluster import hud_remote  # noqa: E402
from selfdrive.eon_cluster.voice_params import REPLY_PREFIX, VoiceParams, load_definitions  # noqa: E402

DEFS = {
  "TFollowGap2": {"group": "종방향", "name": "TFollowGap2", "title": "TF(2) FOLLOW TIME", "descr": "차간거리 2단 시간",
                  "min": 80, "max": 200, "default": 120, "unit": 5},
  "SteerActuatorDelay": {"group": "횡방향", "name": "SteerActuatorDelay", "title": "STEER DELAY", "descr": "조향 지연",
                         "min": 0, "max": 100, "default": 50, "unit": 1},
}


class FakeParams:
  def __init__(self):
    self.store = {"TFollowGap2": b"120"}

  def get(self, name):
    return self.store.get(name)

  def put(self, name, value):
    self.store[name] = value.encode()


def request(vp, moving=False, **body):
  raw = vp.handle(b"HUDPRM1 " + json.dumps(body).encode(), moving)
  assert raw.startswith(REPLY_PREFIX)
  return json.loads(raw[len(REPLY_PREFIX):])


class VoiceParamsTest(unittest.TestCase):
  def setUp(self):
    self.params = FakeParams()
    self.vp = VoiceParams(self.params, DEFS)

  def test_search_finds_korean_description(self):
    out = request(self.vp, id="a", op="search", keywords=["차간"])
    self.assertEqual(out["id"], "a")
    self.assertEqual(out["results"][0]["name"], "TFollowGap2")
    self.assertEqual(out["results"][0]["value"], 120)

  def test_get_missing_default(self):
    out = request(self.vp, op="get", name="SteerActuatorDelay")
    self.assertEqual(out["setting"]["value"], 50)

  def test_set_in_range(self):
    out = request(self.vp, op="set", name="TFollowGap2", value=140)
    self.assertTrue(out["ok"])
    self.assertEqual((out["before"], out["after"]), (120, 140))
    self.assertEqual(self.params.store["TFollowGap2"], b"140")

  def test_set_out_of_range_rejected(self):
    out = request(self.vp, op="set", name="TFollowGap2", value=500)
    self.assertFalse(out["ok"])
    self.assertEqual(self.params.store["TFollowGap2"], b"120")

  def test_unknown_setting_rejected(self):
    self.assertFalse(request(self.vp, op="set", name="Nope", value=1)["ok"])

  def test_driving_gate(self):
    self.assertFalse(request(self.vp, True, op="set", name="SteerActuatorDelay", value=30)["ok"])
    self.assertNotIn("SteerActuatorDelay", self.params.store)
    self.assertTrue(request(self.vp, True, op="set", name="TFollowGap2", value=130)["ok"])

  def test_press_only_gap(self):
    pressed = []
    def press(cmd):
      pressed.append(cmd)
      return "accepted"
    self.assertTrue(json.loads(self.vp.handle(b'HUDPRM1 {"op":"press","cmd":"gap"}', True, press)[len(REPLY_PREFIX):])["ok"])
    self.assertFalse(json.loads(self.vp.handle(b'HUDPRM1 {"op":"press","cmd":"cancel"}', True, press)[len(REPLY_PREFIX):])["ok"])
    self.assertEqual(pressed, ["gap"])

  def test_memo_line(self):
    with tempfile.TemporaryDirectory() as tmp:
      out = self.vp.memo("출발할 때 흔들림", ["TFollowGap2", "Nope"], "차속 12km/h", memo_dir=tmp, now=0)
      self.assertTrue(out["ok"])
      with open(os.path.join(tmp, out["file"]), encoding="utf-8") as f:
        line = f.read()
      self.assertIn("차속 12km/h", line)
      self.assertIn("TFollowGap2=120", line)
      self.assertIn("메모: 출발할 때 흔들림", line)
      self.assertTrue(line.endswith("\n"))
      self.assertFalse(self.vp.memo("  ", [], "", memo_dir=tmp)["ok"])

  def test_foreign_datagram_ignored(self):
    self.assertIsNone(self.vp.handle(b"HUD1", False))
    self.assertIsNone(self.vp.handle(b"HUDPRM1 not-json", False))

  def test_real_apilot_json_loads(self):
    defs = load_definitions()
    self.assertGreater(len(defs), 100)


class VoiceRequestTest(unittest.TestCase):
  def test_voice_command_bumps_seq(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = os.path.join(tmp, "voice.json")
      old = hud_remote.VOICE_FILE
      hud_remote.VOICE_FILE = path
      try:
        clock = [10.0]
        sync = hud_remote.RemoteCommandSync(FakeParams(), command_file=os.path.join(tmp, "c.json"),
                                            lane_file=os.path.join(tmp, "l.json"), clock=lambda: clock[0])
        allowed = hud_remote.allowed_commands(False, False, False, False, False)
        self.assertIn("voice", allowed)
        self.assertEqual(sync.apply("voice", allowed), "accepted")
        self.assertEqual(hud_remote.voice_request(path, clock=lambda: 11.0), (1, "ai"))
        self.assertEqual(sync.apply("voice", allowed), "accepted")
        self.assertEqual(hud_remote.voice_request(path, clock=lambda: 11.0), (2, "ai"))
        self.assertEqual(sync.apply("nav_voice", allowed), "accepted")
        self.assertEqual(hud_remote.voice_request(path, clock=lambda: 11.0), (3, "nav"))
        self.assertIsNone(hud_remote.voice_request(path, clock=lambda: 20.0))
      finally:
        hud_remote.VOICE_FILE = old


if __name__ == "__main__":
  unittest.main()
