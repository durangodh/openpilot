#!/usr/bin/env python3
"""Phone relay remote: authentication, token lifetime, at-most-once apply."""
import json
import os
import tempfile
import unittest

from selfdrive.eon_cluster import hud_remote as hr

KEY = "0a1b2c3d"


class FakeParams:
  def __init__(self, key=KEY):
    self.store = {"HudRemoteKey": key.encode(), "EonClusterHudNavApp": b"1"}

  def get(self, name):
    return self.store.get(name)

  def put(self, name, value):
    self.store[name] = value.encode() if isinstance(value, str) else value


class Clock:
  def __init__(self):
    self.now = 100.0

  def __call__(self):
    return self.now


def signed(body, key=KEY):
  return (body + " " + hr.sign(key, body)).encode()


def parse(reply, key=KEY):
  body, signature = reply.decode().rsplit(" ", 1)
  assert signature == hr.sign(key, body)
  return body.split(" ")


class HudRemoteTest(unittest.TestCase):
  def setUp(self):
    self.dir = tempfile.TemporaryDirectory()
    self.cmd_file = os.path.join(self.dir.name, "cmd.json")
    self.lane = os.path.join(self.dir.name, "lane.json")
    self.clock = Clock()
    self.params = FakeParams()
    self.sync = hr.RemoteCommandSync(self.params, self.cmd_file, self.lane, self.clock)
    self.drive = hr.allowed_commands(True, True, True, False, False)

  def tearDown(self):
    self.dir.cleanup()

  def hello(self):
    fields = parse(self.sync.handle(signed("HELLO3 00112233445566aa"), self.drive))
    self.assertEqual(fields[0], "SESS3")
    self.assertEqual(fields[1], "00112233445566aa")
    return fields[2], fields[3]

  def cmd(self, session, token, request, command, allowed=None):
    reply = self.sync.handle(signed("CMD3 %s %s %s %s" % (session, token, request, command)),
                             self.drive if allowed is None else allowed)
    return parse(reply) if reply is not None else None

  def test_wrong_key_is_silent(self):
    self.assertIsNone(self.sync.handle(signed("HELLO3 00112233445566aa", "ffffffff"), self.drive))

  def test_no_key_is_silent(self):
    self.params.store["HudRemoteKey"] = b""
    self.assertIsNone(self.sync.handle(signed("HELLO3 00112233445566aa"), self.drive))

  def test_accept_once_and_ack_resend(self):
    session, token = self.hello()
    req = "a" * 32
    self.assertEqual(self.cmd(session, token, req, "res"), ["ACK3", req, "accepted"])
    with open(self.cmd_file) as f:
      first = json.load(f)["events"]
    self.assertEqual(len(first), 1)
    # The phone resends after a lost ACK: same answer, no second event.
    self.clock.now += 0.2
    self.assertEqual(self.cmd(session, token, req, "res"), ["ACK3", req, "accepted"])
    with open(self.cmd_file) as f:
      self.assertEqual(len(json.load(f)["events"]), 1)

  def test_token_survives_short_wifi_stall_and_expires(self):
    session, token = self.hello()
    self.clock.now += 1.5
    self.assertEqual(self.cmd(session, token, "b" * 32, "set")[2], "accepted")
    self.clock.now += hr.TOKEN_TTL_S
    self.assertEqual(self.cmd(session, token, "c" * 32, "set")[2], "stale")

  def test_vehicle_state_blocks_lane_change(self):
    session, token = self.hello()
    parked = hr.allowed_commands(True, True, False, False, False)
    self.assertEqual(self.cmd(session, token, "d" * 32, "lane_left", parked)[2], "blocked")
    self.assertFalse(os.path.exists(self.lane))
    self.assertEqual(self.cmd(session, token, "e" * 32, "lane_left")[2], "accepted")
    with open(self.lane) as f:
      self.assertEqual(json.load(f)["direction"], -1)

  def test_key_change_starts_new_session(self):
    session, token = self.hello()
    self.params.store["HudRemoteKey"] = b"11223344"
    self.assertIsNone(self.cmd(session, token, "f" * 32, "res"))

  def test_unknown_command_rejected(self):
    session, token = self.hello()
    self.assertIsNone(self.cmd(session, token, "1" * 32, "reboot"))

  def test_button_source_reads_journal_once(self):
    session, token = self.hello()
    source = hr.RemoteButtonSource(self.cmd_file, self.clock, long_delay=40)
    self.clock.now += 0.01
    self.cmd(session, token, "2" * 32, "gap")
    presses = []
    for _ in range(12):
      presses += source.poll(True)
    self.assertEqual(presses, [("gapAdjustCruise", True), ("gapAdjustCruise", False)])

  def test_lane_source_pulse(self):
    session, token = self.hello()
    lane = hr.RemoteLaneChangeSource(self.lane, self.clock)
    self.clock.now += 0.01
    self.cmd(session, token, "3" * 32, "lane_right")
    self.assertEqual(lane.poll(), 1)
    self.clock.now += hr.LANE_CHANGE_HOLD_S + 0.05
    self.assertEqual(lane.poll(), 0)


if __name__ == "__main__":
  unittest.main()
