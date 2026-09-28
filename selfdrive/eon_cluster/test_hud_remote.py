"""Run on a development host without cereal; integration still needs the car."""
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('hud_remote', Path(__file__).with_name('hud_remote.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
KEY = '0123456789abcdef' * 2


class Params:
  def __init__(self):
    self.values = {'HudRemoteKey': KEY, 'EonClusterHudNavApp': '1'}

  def get(self, key):
    return self.values.get(key)

  def put(self, key, value):
    self.values[key] = value


class RemoteTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.path = self.tmp.name + '/cmd.json'
    self.lane_path = self.tmp.name + '/lane.json'
    self.now = 100.0
    clock = lambda: self.now
    self.params = Params()
    self.sync = r.RemoteCommandSync(self.params, self.path, self.lane_path, clock)
    self.src = r.RemoteButtonSource(self.path, clock)
    self.lane = r.RemoteLaneChangeSource(self.lane_path, clock)
    self.packet = self.sync.telemetry()

  def request(self, command='res', number=1, key=KEY):
    body = 'HUDCMD2 %s %032x %s %s' % (self.packet['hudCmdSession'], number, self.packet['hudCmdTicket'], command)
    return (body + ' ' + hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()).encode()

  def send(self, command='res', number=1, allowed=r.ALL_COMMANDS):
    return self.sync.receive(self.request(command, number), ('s9', 7210), allowed)

  def events(self, allowed=True):
    return [event for _ in range(30) for event in self.src.poll(allowed)]

  def test_press_release_and_duplicate(self):
    self.assertTrue(self.send())
    self.assertTrue(self.send())
    self.assertEqual(self.events(), [('accelCruise', True), ('accelCruise', False)])
    self.assertEqual(self.events(), [])

  def test_journal_preserves_quick_distinct_commands(self):
    self.send('res', 1)
    self.send('set', 2)
    self.assertEqual(self.events(), [('accelCruise', True), ('accelCruise', False), ('decelCruise', True), ('decelCruise', False)])

  def test_auth_and_protocol(self):
    for payload, port in [(self.request(key='f' * 32), 7210), (b'HUDCMD1 old', 7210),
                          (self.request(), 7211), (b'x' * 257, 7210), (b'\xff', 7210)]:
      self.assertFalse(self.sync.receive(payload, ('s9', port), r.ALL_COMMANDS))
    self.assertEqual(self.events(), [])

  def test_reused_id_cannot_change_action(self):
    self.send()
    self.assertFalse(self.send('cancel'))

  def test_expired_network_packet_rejected(self):
    self.now += .401
    self.assertFalse(self.send())

  def test_key_revocation_and_restart_reject_old_session(self):
    payload = self.request()
    self.params.values['HudRemoteKey'] = 'f' * 32
    self.assertFalse(self.sync.receive(payload, ('s9', 7210), r.ALL_COMMANDS))
    self.params.values['HudRemoteKey'] = ''
    self.assertFalse(self.send())
    self.assertEqual(self.sync.telemetry()['hudCmdTicket'], '')

  def test_blocked_command_never_runs_on_retry(self):
    self.assertTrue(self.send(allowed=()))
    self.assertEqual(self.sync.result, 'blocked')
    self.assertTrue(self.send())
    self.assertEqual(self.events(), [])

  def test_stale_and_startup_files(self):
    self.send()
    self.now += .401
    self.assertEqual(self.events(), [])
    self.src = r.RemoteButtonSource(self.path, lambda: self.now)
    self.assertEqual(self.events(), [])

  def test_future_nan_and_malformed_files(self):
    for ts in [101, float('nan'), float('inf'), '100', None]:
      Path(self.path).write_text(json.dumps({'events': [{'id': str(ts), 'cmd': 'res', 'ts': ts}]}))
      self.assertEqual(self.events(), [])
    for bad in ['[]', '{', '{"events":null}']:
      Path(self.path).write_text(bad)
      self.assertEqual(self.events(), [])

  def test_consumer_driver_override_discards_speed(self):
    self.send()
    self.assertEqual(self.events(False), [])
    self.assertEqual(self.events(True), [])
    self.send('cancel', 2)
    self.assertEqual(self.events(False), [('cancel', True), ('cancel', False)])

  def test_nav_and_lane_expiry(self):
    self.send('nav_toggle')
    self.assertEqual(self.params.get('EonClusterHudNavApp'), '2')
    self.send('lane_left', 2)
    self.assertEqual(self.lane.poll(), -1)
    self.now += .31
    self.assertEqual(self.lane.poll(), 0)

  def test_vehicle_gates(self):
    self.assertEqual(r.allowed_commands(False, True, True, False, False), set(r.PARAM_COMMANDS))
    for flags in [(True, False, True, False, False), (True, True, False, False, False),
                  (True, True, True, True, False), (True, True, True, False, True)]:
      self.assertNotIn('res', r.allowed_commands(*flags))
      self.assertNotIn('lane_left', r.allowed_commands(*flags))
    self.assertIn('cancel', r.allowed_commands(True, True, True, True, False))
    self.assertEqual(r.allowed_commands(True, True, True, False, False), set(r.ALL_COMMANDS))


if __name__ == '__main__':
  unittest.main()
