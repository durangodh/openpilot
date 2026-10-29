#!/usr/bin/env python3
"""EON Bluetooth remote: gestures, wake guard, link classification, command journal, kernel event decoding."""
import os
import struct
import tempfile
import time
import unittest

from selfdrive.eon_cluster import bt_remote, bt_remote_core as core
from selfdrive.eon_cluster import hud_remote as hr


class Clock:
  def __init__(self):
    self.now = 100.0

  def __call__(self):
    return self.now


class FakeParams:
  def __init__(self):
    self.store = {"EonClusterHudNavApp": b"1"}

  def get(self, name):
    return self.store.get(name)

  def put(self, name, value):
    self.store[name] = value.encode() if isinstance(value, str) else value


class TestGestures(unittest.TestCase):
  def setUp(self):
    self.out = []
    self.g = core.Gestures({"key:30@short": "res", "key:31@long": "set_long", "key:32@double": "gap",
                            "key:32@short": "cancel"}, lambda t, g, at: self.out.append((t, g)))

  def test_short_fires_on_release(self):
    self.g.key("key:30", 1, 0.0)
    self.assertEqual(self.out, [])
    self.g.key("key:30", 0, 0.1)
    self.assertEqual(self.out, [("key:30", "short")])

  def test_long_fires_while_held_once(self):
    self.g.key("key:31", 1, 0.0)
    for i in range(1, 17):
      self.g.tick(i * 0.1)
    self.g.key("key:31", 0, 1.7)
    self.assertEqual(self.out, [("key:31", "long")])

  def test_double_and_delayed_single(self):
    self.g.key("key:32", 1, 0.0)
    self.g.key("key:32", 0, 0.05)
    self.g.key("key:32", 1, 0.15)
    self.g.key("key:32", 0, 0.2)
    self.assertEqual(self.out, [("key:32", "double")])
    self.g.key("key:32", 1, 1.0)
    self.g.key("key:32", 0, 1.05)
    self.g.tick(1.45)
    self.assertEqual(self.out[-1], ("key:32", "short"))

  def test_blocked_hold_never_fires(self):
    self.g.key("key:31", 1, 0.0)
    self.g.cancel_disallowed(frozenset())
    for i in range(1, 14):
      self.g.tick(i * 0.1)
    self.assertEqual(self.out, [])


class TestLinkTracker(unittest.TestCase):
  def test_idle_disconnect_is_sleep_and_busy_disconnect_is_lost(self):
    link = core.LinkTracker()
    self.assertEqual(link.link, hr.LINK_NONE)
    link.connect(0.0)
    self.assertEqual(link.link, hr.LINK_OK)
    link.input(10.0)
    self.assertIn("sleep", link.disconnect(10.0 + core.SLEEP_IDLE_S + 1))
    self.assertEqual(link.link, hr.LINK_SLEEP)
    link.connect(200.0)
    link.input(205.0)
    self.assertIn("LOST", link.disconnect(210.0))
    self.assertEqual(link.link, hr.LINK_LOST)
    self.assertEqual((link.connects, link.sleeps, link.losses), (2, 1, 1))

  def test_wake_guard_window(self):
    link = core.LinkTracker()
    link.connect(50.0)
    self.assertTrue(link.guarded(50.5))
    self.assertFalse(link.guarded(50.0 + core.WAKE_IGNORE_S + 0.01))

  def test_summary_reports_latency(self):
    link = core.LinkTracker()
    link.connect(0.0)
    link.latency(20)
    link.latency(40)
    self.assertIn("평균 30ms 최대 40ms", link.summary())


class TestMappingValidation(unittest.TestCase):
  def test_mappings_keep_only_known_tokens_and_commands(self):
    raw = '{"key:30@short":"res","hat:x:+@long":"lane_right","blebtn:A@short":"res","key:31@short":"rm -rf"}'
    self.assertEqual(bt_remote.mappings(raw), {"key:30@short": "res", "hat:x:+@long": "lane_right"})
    self.assertEqual(bt_remote.mappings("not json"), {})

  def test_learn_request(self):
    nonce = "0" * 32
    self.assertEqual(bt_remote.learn_request(f"gap|double|{nonce}"), ("gap", "double", nonce))
    self.assertIsNone(bt_remote.learn_request(f"gap|triple|{nonce}"))
    self.assertIsNone(bt_remote.learn_request("gap|short|xyz"))


class TestCommandJournal(unittest.TestCase):
  def setUp(self):
    self.dir = tempfile.TemporaryDirectory()
    self.clock = Clock()
    self.cmd = os.path.join(self.dir.name, "cmd.json")
    self.lane = os.path.join(self.dir.name, "lane.json")
    self.status = os.path.join(self.dir.name, "status.json")
    self.sync = hr.RemoteCommandSync(FakeParams(), self.cmd, self.lane, clock=self.clock)

  def tearDown(self):
    self.dir.cleanup()

  def test_button_reaches_car_state_once_and_expires(self):
    source = hr.RemoteButtonSource(self.cmd, clock=self.clock)
    self.clock.now += 1
    self.assertEqual(self.sync.apply("res", hr.allowed_commands(True, True, True, False, False)), "accepted")
    presses = [p for _ in range(10) for p in source.poll()]
    self.assertEqual(presses, [("accelCruise", True), ("accelCruise", False)])
    self.assertEqual([p for _ in range(10) for p in source.poll()], [])
    self.sync.apply("set", hr.allowed_commands(True, True, True, False, False))
    self.clock.now += hr.COMMAND_MAX_AGE_S + 0.1
    self.assertEqual([p for _ in range(10) for p in source.poll()], [])

  def test_lane_command_blocked_out_of_drive_with_reason(self):
    self.sync.vehicle = (True, True, False, False, False)
    allowed = hr.allowed_commands(*self.sync.vehicle)
    self.assertEqual(self.sync.apply("lane_left", allowed), "blocked")
    self.assertEqual(self.sync.event_status()["why"], "D단 아님")
    self.assertFalse(os.path.exists(self.lane))

  def test_lane_command_pulse(self):
    source = hr.RemoteLaneChangeSource(self.lane, clock=self.clock)
    self.clock.now += 1
    self.sync.apply("lane_right", hr.allowed_commands(True, True, True, False, False))
    self.assertEqual(source.poll(), 1)
    self.clock.now += hr.LANE_CHANGE_HOLD_S + 0.05
    self.assertEqual(source.poll(), 0)

  def test_hud_status_needs_fresh_file(self):
    hr.atomic_json(self.status, {"link": hr.LINK_SLEEP, "event": {}, "ts": self.clock.now})
    self.assertEqual(hr.hud_status(self.status, clock=self.clock), {"link": hr.LINK_SLEEP})
    self.clock.now += hr.STATUS_STALE_S + 1
    self.assertIsNone(hr.hud_status(self.status, clock=self.clock))
    self.assertIsNone(hr.hud_status(os.path.join(self.dir.name, "missing.json"), clock=self.clock))


class TestInputNodeDecoding(unittest.TestCase):
  """Kernel input_event records through a FIFO (no EVIOCSCLOCKID there, so realtime stamps)."""

  def setUp(self):
    self.dir = tempfile.TemporaryDirectory()
    self.path = os.path.join(self.dir.name, "event0")
    os.mkfifo(self.path)
    self.emitted = []
    self.reader = None
    self.writer = None

  def tearDown(self):
    for fd in (self.writer,):
      if fd is not None:
        os.close(fd)
    if self.reader is not None:
      os.close(self.reader.fd)
    self.dir.cleanup()

  def open(self, mapping):
    self.reader = bt_remote.InputNode(self.path, "aa:bb", "8BitDo Micro", False,
                                      lambda node, t, g, at: self.emitted.append((t, g)),
                                      lambda *args: None, time.monotonic())
    self.writer = os.open(self.path, os.O_WRONLY)
    self.reader.gestures.mapping = mapping

  def send(self, kind, code, value, age=0.0):
    stamp = time.time() - age
    sec = int(stamp)
    os.write(self.writer, bt_remote.EVENT.pack(sec, int((stamp - sec) * 1e6), kind, code, value))

  def test_key_press_and_release(self):
    self.open({"key:30@short": "res"})
    self.send(1, 30, 1)
    self.send(1, 30, 0)
    self.reader.read(time.monotonic())
    self.assertEqual(self.emitted, [("key:30", "short")])

  def test_hat_dpad(self):
    self.open({"hat:x:-@short": "lane_left"})
    self.send(3, 16, -1)
    self.send(3, 16, 0)
    self.reader.read(time.monotonic())
    self.assertEqual(self.emitted, [("hat:x:-", "short")])

  def test_stale_events_are_dropped(self):
    self.open({"key:30@short": "res"})
    self.send(1, 30, 1, age=1.0)
    self.send(1, 30, 0, age=1.0)
    self.reader.read(time.monotonic())
    self.assertEqual(self.emitted, [])

  def test_syn_dropped_discards_stream(self):
    self.open({"key:30@short": "res"})
    self.send(0, 3, 0)
    with self.assertRaises(OSError):
      self.reader.read(time.monotonic())


class TestBluetoothScan(unittest.TestCase):
  def test_only_bluetooth_bus_with_identity(self):
    with tempfile.TemporaryDirectory() as root:
      def node(name, bus, uniq, rel="0"):
        device = os.path.join(root, name, "device")
        os.makedirs(os.path.join(device, "id"))
        os.makedirs(os.path.join(device, "capabilities"))
        for path, text in (("id/bustype", bus), ("name", "pad"), ("uniq", uniq), ("phys", ""),
                           ("properties", "0"), ("capabilities/rel", rel)):
          with open(os.path.join(device, path), "w") as f:
            f.write(text + "\n")
      node("event1", "0005", "e4:17:d8:00:00:01")
      node("event2", "0003", "usb")
      node("event3", "0005", "")
      node("event4", "0005", "e4:17:d8:00:00:02", rel="3")
      from pathlib import Path
      found, summary = bt_remote.bluetooth_nodes(Path(root))
      self.assertEqual(found, {"/dev/input/event1": ("e4:17:d8:00:00:01", "pad", True),
                               "/dev/input/event4": ("e4:17:d8:00:00:02", "pad", False)})
      self.assertIn("Bluetooth HID 3개", summary)
      self.assertIn("고유 ID 없음 1개", summary)


class TestEventLayout(unittest.TestCase):
  def test_input_event_size_matches_platform_timeval(self):
    self.assertEqual(bt_remote.EVENT.size, struct.calcsize("@llHHi"))


if __name__ == "__main__":
  unittest.main()
