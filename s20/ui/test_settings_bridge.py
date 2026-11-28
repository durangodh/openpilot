import json, os, tempfile, unittest

from s20.ui.settings_bridge import SettingsBridge


class FakeParams:
  def __init__(self):
    self.d = {}

  def get_bool(self, k):
    return bool(self.d.get(k, False))

  def put_bool(self, k, v):
    self.d[k] = bool(v)

  def get(self, k, return_default=False):
    if k in self.d:
      return self.d[k]
    return {"LongitudinalPersonality": 1, "InitialCruiseSpeedKph": 40}.get(k) if return_default else None

  def put(self, k, v):
    self.d[k] = v

  def remove(self, k):
    self.d.pop(k, None)


class TestSettingsBridge(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.p = FakeParams()
    self.b = SettingsBridge(self.p, os.path.join(self.tmp.name, "cmd"))

  def tearDown(self):
    self.tmp.cleanup()

  def send(self, cmd, engaged=False):
    with open(os.path.join(self.b.cmd_dir, f"{len(os.listdir(self.b.cmd_dir)):03d}.json"), "w") as f:
      json.dump(cmd, f)
    self.b.poll(engaged)
    return self.b.last

  def test_bool_and_defaults(self):
    v = self.b.values()
    self.assertEqual(v["LongitudinalPersonality"], 1)
    self.assertFalse(v["IsMetric"])
    self.assertTrue(self.send({"id": "a", "op": "put_bool", "key": "IsMetric", "value": True})["ok"])
    self.assertTrue(self.b.values()["IsMetric"])
    self.assertEqual(os.listdir(self.b.cmd_dir), [])

  def test_rejects_unknown_and_out_of_range(self):
    self.assertFalse(self.send({"op": "put_bool", "key": "DongleId", "value": True})["ok"])
    self.assertFalse(self.send({"op": "put_int", "key": "LongitudinalPersonality", "value": 5})["ok"])
    self.assertTrue(self.send({"op": "put_int", "key": "InitialCruiseSpeedKph", "value": 100})["ok"])
    self.assertEqual(self.p.d["InitialCruiseSpeedKph"], 100)

  def test_restart_keys_and_calibration_blocked_while_engaged(self):
    self.assertFalse(self.send({"op": "put_bool", "key": "OpenpilotEnabledToggle", "value": False}, engaged=True)["ok"])
    self.assertFalse(self.send({"op": "reset_calib"}, engaged=True)["ok"])
    self.p.d["CalibrationParams"] = b"x"
    self.assertTrue(self.send({"op": "reset_calib"})["ok"])
    self.assertNotIn("CalibrationParams", self.p.d)
    self.assertTrue(self.p.d["OnroadCycleRequested"])

  def test_debug_modes_exclusive(self):
    self.send({"op": "put_bool", "key": "JoystickDebugMode", "value": True})
    self.send({"op": "put_bool", "key": "LateralManeuverMode", "value": True})
    self.assertFalse(self.p.d["JoystickDebugMode"])
    self.assertTrue(self.p.d["LateralManeuverMode"])

  def test_bad_file_is_dropped(self):
    with open(os.path.join(self.b.cmd_dir, "bad.json"), "w") as f:
      f.write("{not json")
    self.b.poll(False)
    self.assertFalse(self.b.last["ok"])
    self.assertEqual(os.listdir(self.b.cmd_dir), [])


if __name__ == "__main__":
  unittest.main()
