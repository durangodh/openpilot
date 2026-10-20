"""Run on a development host without cereal; integration still needs the car."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('hud_remote', Path(__file__).with_name('hud_remote.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class Params:
  def __init__(self):
    self.values = {'EonClusterHudNavApp': '1'}

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

  def send(self, command='res', allowed=r.ALL_COMMANDS):
    return self.sync.apply_local(command, allowed)

  def events(self, allowed=True):
    return [event for _ in range(30) for event in self.src.poll(allowed)]

  def test_press_release_and_duplicate(self):
    self.assertTrue(self.send())
    self.assertEqual(self.events(), [('accelCruise', True), ('accelCruise', False)])
    self.assertEqual(self.events(), [])

  def test_journal_preserves_quick_distinct_commands(self):
    self.send('res')
    self.send('set')
    self.assertEqual(self.events(), [('accelCruise', True), ('accelCruise', False), ('decelCruise', True), ('decelCruise', False)])

  def test_local_event_respects_allowed_commands(self):
    self.assertTrue(self.sync.apply_local('res', r.ALL_COMMANDS))
    self.assertEqual(self.events(), [('accelCruise', True), ('accelCruise', False)])
    self.assertFalse(self.sync.apply_local('res', ()))

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
    self.send('cancel')
    self.assertEqual(self.events(False), [('cancel', True), ('cancel', False)])

  def test_nav_and_lane_expiry(self):
    self.send('nav_toggle')
    self.assertEqual(self.params.get('EonClusterHudNavApp'), '2')
    self.send('lane_left')
    self.assertEqual(self.lane.poll(), -1)
    self.now += .31
    self.assertEqual(self.lane.poll(), 0)

  def test_vehicle_gates(self):
    self.assertEqual(r.allowed_commands(False, True, True, False, False), set(r.PARAM_COMMANDS))
    self.assertEqual(r.allowed_commands(True, False, True, False, False), set(r.PARAM_COMMANDS))
    # Cruise buttons only need ignition and valid car data; the cruise logic judges gear/pedals.
    for flags in [(True, True, False, False, False), (True, True, True, True, False), (True, True, True, False, True)]:
      allowed = r.allowed_commands(*flags)
      for command in ('res', 'set', 'gap', 'cancel', 'res_long', 'set_long', 'gap_long'):
        self.assertIn(command, allowed)
      self.assertNotIn('lane_left', allowed)
      self.assertNotIn('lane_right', allowed)
    self.assertEqual(r.allowed_commands(True, True, True, False, False), set(r.ALL_COMMANDS))

  def test_long_press_holds_then_releases(self):
    self.src = r.RemoteButtonSource(self.path, lambda: self.now, long_delay=40)
    self.send('res_long')
    first = [event for _ in range(5) for event in self.src.poll(True)]
    self.assertEqual(first, [('accelCruise', True)])
    held = [self.src.poll(True) for _ in range(41)]
    self.assertTrue(all(events == [] for events in held))
    self.assertEqual(self.src.poll(True), [('accelCruise', False)])
    self.assertEqual(self.events(), [])

  def test_gap_long_hold_covers_info_box_toggle(self):
    self.src = r.RemoteButtonSource(self.path, lambda: self.now, long_delay=40)
    self.send('gap_long')
    frames = [self.src.poll(True) for _ in range(200)]
    pressed = next(i for i, e in enumerate(frames) if e == [('gapAdjustCruise', True)])
    released = next(i for i, e in enumerate(frames) if e == [('gapAdjustCruise', False)])
    self.assertGreater(released - pressed, 150)

  def test_long_press_released_when_blocked(self):
    self.src = r.RemoteButtonSource(self.path, lambda: self.now, long_delay=40)
    self.send('set_long')
    self.assertEqual([event for _ in range(5) for event in self.src.poll(True)], [('decelCruise', True)])
    self.assertEqual(self.src.poll(True), [])
    self.assertEqual(self.src.poll(False), [('decelCruise', False)])

if __name__ == '__main__':
  unittest.main()
