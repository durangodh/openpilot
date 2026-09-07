import importlib.util
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "timezoned.py"


def load_timezoned():
  # These tests also run on a host without compiled Params or EON hardware.
  modules = {
    "common.params": types.SimpleNamespace(Params=Mock()),
    "selfdrive.hardware": types.SimpleNamespace(EON=True, TICI=False),
    "selfdrive.swaglog": types.SimpleNamespace(cloudlog=Mock()),
  }
  spec = importlib.util.spec_from_file_location("timezoned_under_test", SOURCE)
  module = importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules, modules):
    spec.loader.exec_module(module)
  return module


class StopLoop(Exception):
  pass


class FakeParams:
  def __init__(self, timezone=None, offroad=True):
    self.values = {"Timezone": timezone, "IsOffroad": offroad}

  def get(self, key, encoding=None):
    return self.values.get(key)

  def get_bool(self, key):
    return bool(self.values.get(key))

  def put(self, key, value):
    self.values[key] = value


class TestTimezoned(unittest.TestCase):
  def setUp(self):
    self.tz = load_timezoned()

  def run_eon(self, params, transitions=()):
    steps = iter(transitions)

    def sleep(seconds):
      self.assertEqual(seconds, 5)
      try:
        params.values.update(next(steps))
      except StopIteration:
        raise StopLoop()

    with patch.object(self.tz.time, "sleep", side_effect=sleep):
      with self.assertRaises(StopLoop):
        self.tz.eon_main(params)

  def test_eon_uses_android_property_without_systemd(self):
    with patch.object(self.tz.subprocess, "check_call") as call:
      self.assertTrue(self.tz.set_timezone(self.tz.get_eon_timezones(), "Asia/Seoul"))
      call.assert_called_once_with(["setprop", "persist.sys.timezone", "Asia/Seoul"])

  def test_invalid_timezone_does_not_run_command(self):
    with patch.object(self.tz.subprocess, "check_call") as call:
      self.assertFalse(self.tz.set_timezone(self.tz.get_eon_timezones(), "Asia/Seoul; reboot"))
      call.assert_not_called()

  def test_command_failure_is_retryable(self):
    params = FakeParams("Asia/Tokyo")
    failure = subprocess.CalledProcessError(1, "setprop")
    with patch.object(self.tz.subprocess, "check_call", side_effect=[failure, None]) as call:
      self.run_eon(params, [{}])
      self.assertEqual(call.call_count, 2)

  def test_default_seoul_and_unchanged_value_not_reapplied(self):
    params = FakeParams()
    with patch.object(self.tz.subprocess, "check_call") as call:
      self.run_eon(params, [{}, {}])
      self.assertEqual(params.get("Timezone"), "Asia/Seoul")
      call.assert_called_once()

  def test_saved_timezone_preserved_and_change_deferred_until_offroad(self):
    params = FakeParams("America/New_York")
    transitions = [
      {"IsOffroad": False, "Timezone": "Asia/Tokyo"},
      {"IsOffroad": True},
      {},
    ]
    calls = []

    def apply(command):
      self.assertTrue(params.get_bool("IsOffroad"))
      calls.append(command[-1])

    with patch.object(self.tz.subprocess, "check_call", side_effect=apply):
      self.run_eon(params, transitions)
    self.assertEqual(calls, ["America/New_York", "Asia/Tokyo"])

  def test_onroad_start_does_not_change_timezone(self):
    with patch.object(self.tz.subprocess, "check_call") as call:
      self.run_eon(FakeParams("Asia/Seoul", offroad=False))
      call.assert_not_called()

  def test_eon_dispatch_needs_no_timezonefinder(self):
    with patch.dict(sys.modules, {"timezonefinder": None, "requests": None}):
      with patch.object(self.tz, "eon_main", side_effect=StopLoop) as run:
        with self.assertRaises(StopLoop):
          self.tz.main()
        run.assert_called_once()

  def test_tici_keeps_agnos_localtime_update(self):
    self.tz.EON, self.tz.TICI = False, True
    with patch.object(self.tz.subprocess, "check_call") as call:
      self.assertTrue(self.tz.set_timezone(["Asia/Seoul"], "Asia/Seoul"))
      self.assertEqual(call.call_count, 2)
      self.assertIn("/data/etc/localtime", call.call_args_list[0].args[0])
      self.assertIn("/data/etc/timezone", call.call_args_list[1].args[0])

  def test_pc_keeps_timedatectl(self):
    self.tz.EON, self.tz.TICI = False, False
    with patch.object(self.tz.subprocess, "check_call") as call:
      self.assertTrue(self.tz.set_timezone(["Asia/Seoul"], "Asia/Seoul"))
      call.assert_called_once_with("sudo timedatectl set-timezone Asia/Seoul", shell=True)


if __name__ == "__main__":
  unittest.main()
